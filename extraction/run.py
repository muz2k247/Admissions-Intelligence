"""CLI entry point for extraction, split into two steps because UG/PG
routing and (optionally) field extraction are both done by Gemini
Agent tool calls, not by this script:

    1. chunk  — read scraper/run.py's output, produce one chunk-input file
                shared by the content-classifier and field-extractor
                subagents.
    2. build  — read the classifier's output file, the (optional)
                field-extractor's output file, and the same scraped
                records; run field extraction (LLM output wins per chunk
                where present, regex extractor is the fallback); write
                final ExtractedRecord JSON (one file per chunk) to an out
                dir.

Usage:
    python -m extraction.run chunk --scraped-dir .tmp/scraped --out .tmp/chunks/chunks.json
    python -m extraction.run build --scraped-dir .tmp/scraped --classified .tmp/chunks/classified.json --llm-extracted .tmp/chunks/llm_fields.json --out .tmp/extracted
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from extraction.chunker import chunk_scraped_record
from extraction.classify import load_classifier_results
from extraction.fields import extract_constituent_college, extract_deadline, extract_fee, extract_programs
from extraction.llm_fields import load_llm_field_results
from extraction.schema import NULL_FIELD, DegreeLevel, ExtractedRecord

DEFAULT_CHUNK_OUT = Path(".tmp") / "chunks" / "chunks.json"
DEFAULT_EXTRACT_OUT = Path(".tmp") / "extracted"


def _load_scraped_records(scraped_dir: Path) -> list[dict]:
    """A malformed file under scraped_dir is skipped and reported, not fatal
    to the rest of the batch — the scraper already treats each source
    independently, so extraction should too."""
    records = []
    for path in sorted(scraped_dir.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                records.append(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"SKIP  {path}: unreadable scraped record ({exc})")
    return records


def run_chunk(scraped_dir: Path, out_path: Path) -> int:
    records = _load_scraped_records(scraped_dir)
    chunks = []
    for record in records:
        if record.get("error"):
            continue  # nothing to chunk from a failed fetch
        try:
            chunks.extend(chunk_scraped_record(record))
        except KeyError as exc:
            print(f"SKIP  malformed scraped record (missing {exc}): {record.get('source_url', '?')}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([c.to_classifier_dict() for c in chunks], indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(chunks)} chunk(s) -> {out_path}")
    return 0


def build_extracted_records(
    records: list[dict], degree_levels: dict, llm_fields: dict[str, dict] | None = None
) -> tuple[list[tuple[str, ExtractedRecord]], int, int]:
    """Merge scraped records with classifier output into ExtractedRecords.

    Returns (list of (chunk_id, record), skipped_count, excluded_postgraduate_count).
    Builds everything in memory — no disk writes here — so callers can
    construct the full result set before touching the output directory (see
    write_extracted_records).

    Postgraduate-classified chunks are dropped here, not merely hidden by the
    dashboard's default filter: the project is undergrad-only in scope, so
    there is nothing to gain from persisting PG data. Ambiguous chunks
    (degree_level.value is None) are kept — CLAUDE.md hard rule 5 treats
    Ambiguous as a distinct, reviewable outcome, not the same failure type as
    Postgraduate, and it carries its own reason code for that review.

    llm_fields (from extraction.llm_fields.load_llm_field_results) is field-
    extractor subagent output keyed by chunk_id. When a chunk has an entry
    there, its LLM-produced fields win outright — not a per-field race with
    the regex extractor. When llm_fields is None (the step wasn't run) or a
    specific chunk has no entry in it (the subagent skipped/omitted it), that
    chunk falls back to the regex extractor — extraction/fields.py stays the
    permanent zero-cost fallback so the pipeline degrades gracefully instead
    of failing outright if the LLM step is unavailable."""
    built: list[tuple[str, ExtractedRecord]] = []
    skipped = 0
    excluded_postgraduate = 0
    for record in records:
        if record.get("error"):
            skipped += 1
            continue
        try:
            chunks = chunk_scraped_record(record)
        except KeyError as exc:
            print(f"SKIP  malformed scraped record (missing {exc}): {record.get('source_url', '?')}")
            skipped += 1
            continue

        for chunk in chunks:
            degree_level = degree_levels.get(
                chunk.id, DegreeLevel(value=None, reason="no-signal")
            )
            if degree_level.value == "Postgraduate":
                excluded_postgraduate += 1
                continue

            chunk_llm_fields = llm_fields.get(chunk.id) if llm_fields is not None else None
            if chunk_llm_fields is not None:
                constituent_college = chunk_llm_fields.get("constituent_college", NULL_FIELD)
                deadline = chunk_llm_fields.get("deadline", NULL_FIELD)
                fee = chunk_llm_fields.get("fee", NULL_FIELD)
                programs = chunk_llm_fields.get("programs", NULL_FIELD)
            else:
                constituent_college = extract_constituent_college(chunk.raw_text)
                deadline = extract_deadline(chunk.raw_text)
                fee = extract_fee(chunk.raw_text)
                programs = extract_programs(chunk.raw_text)

            extracted = ExtractedRecord(
                institution_id=chunk.institution_id,
                campus=chunk.campus,
                source_url=chunk.source_url,
                fetched_at=chunk.fetched_at,
                chunk_id=chunk.id,
                degree_level=degree_level,
                constituent_college=constituent_college,
                deadline=deadline,
                fee=fee,
                programs=programs,
            )
            built.append((chunk.id, extracted))
    return built, skipped, excluded_postgraduate


def write_extracted_records(built: list[tuple[str, ExtractedRecord]], out_dir: Path) -> int:
    """Clear stale *.json from out_dir, then write all built records.

    The clear happens only after `built` is fully constructed by the caller,
    so a mid-build error (e.g. a schema ValueError) never wipes the previous
    run's good data — and a stale record for an institution whose scrape now
    fails can never outlive that failure (the bug that served months-old
    fabricated GIKI data as if it were live)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.json"):
        stale.unlink()
    for chunk_id, extracted in built:
        out_path = out_dir / f"{chunk_id}.json"
        out_path.write_text(json.dumps(extracted.to_dict(), indent=2), encoding="utf-8")
    return len(built)


def run_build(scraped_dir: Path, classified_path: Path, out_dir: Path, llm_extracted_path: Path | None = None) -> int:
    records = _load_scraped_records(scraped_dir)
    try:
        degree_levels = load_classifier_results(classified_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"FAIL  unreadable classifier output {classified_path}: {exc}")
        return 1

    llm_fields = None
    if llm_extracted_path is not None:
        try:
            llm_fields = load_llm_field_results(llm_extracted_path)
        except (json.JSONDecodeError, OSError) as exc:
            # Degrade, don't fail the run: this is the field-extractor's
            # zero-cost fallback path (every chunk uses the regex extractor
            # instead), so a missing/corrupt file here must behave exactly
            # like --llm-extracted was never passed, not like a fatal error --
            # the pipeline's graceful-degradation guarantee shouldn't depend
            # on the caller correctly omitting the flag under every failure.
            print(f"WARN  unreadable field-extractor output {llm_extracted_path}: {exc} -- falling back to regex extraction for all chunks")
            llm_fields = None

    built, _, excluded_postgraduate = build_extracted_records(records, degree_levels, llm_fields)
    written = write_extracted_records(built, out_dir)

    print(f"Wrote {written} extracted record(s) -> {out_dir}")
    print(f"Excluded {excluded_postgraduate} postgraduate record(s) (undergrad-only scope)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Extraction pipeline: chunking, field extraction, classifier merge.")
    sub = parser.add_subparsers(dest="command", required=True)

    chunk_p = sub.add_parser("chunk", help="Produce chunk-input file for the content-classifier subagent.")
    chunk_p.add_argument("--scraped-dir", type=Path, default=Path(".tmp") / "scraped")
    chunk_p.add_argument("--out", type=Path, default=DEFAULT_CHUNK_OUT)

    build_p = sub.add_parser("build", help="Merge classifier output with field extraction into final records.")
    build_p.add_argument("--scraped-dir", type=Path, default=Path(".tmp") / "scraped")
    build_p.add_argument("--classified", type=Path, required=True)
    build_p.add_argument("--out", type=Path, default=DEFAULT_EXTRACT_OUT)
    build_p.add_argument(
        "--llm-extracted", type=Path, default=None,
        help="field-extractor subagent output file. When omitted, falls back to the regex extractor for every chunk.",
    )

    args = parser.parse_args()

    if args.command == "chunk":
        sys.exit(run_chunk(args.scraped_dir, args.out))
    elif args.command == "build":
        sys.exit(run_build(args.scraped_dir, args.classified, args.out, args.llm_extracted))


if __name__ == "__main__":
    main()
