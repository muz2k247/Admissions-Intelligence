"""Full pipeline orchestration: scraper → chunking → classification → extraction.

Stages 1, 2, 4 run here (deterministic Python). Stage 3 (classifier) is invoked
separately by the calling orchestration prompt via Claude Code Agent.

Usage:
    Stage 1-2: python -m pipeline.run_full stage1_2 --out-scraped .tmp/scraped --out-chunks .tmp/chunks/chunks.json
    Stage 4:   python -m pipeline.run_full stage4 --out-scraped .tmp/scraped --classified .tmp/chunks/classified.json --out .tmp/extracted

Returns:
    Exit code 0 if all stages succeed. Exit code 1 if any stage fails.
    All failures logged to stdout (no silent errors).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from extraction.chunker import chunk_scraped_record
from extraction.classify import load_classifier_results
from extraction.fields import extract_constituent_college, extract_deadline, extract_fee, extract_programs
from extraction.schema import DegreeLevel, ExtractedRecord
from scraper.config import load_institutions, iter_sources
from scraper.fetch import build_session, fetch_source

DEFAULT_SCRAPED_DIR = Path(".tmp") / "scraped"
DEFAULT_CHUNKS_OUT = Path(".tmp") / "chunks" / "chunks.json"
DEFAULT_EXTRACTED_OUT = Path(".tmp") / "extracted"


def _load_scraped_records(scraped_dir: Path) -> list[dict]:
    """Load all scraped records from directory, skip malformed ones."""
    records = []
    for path in sorted(scraped_dir.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                records.append(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"SKIP  {path}: unreadable scraped record ({exc})", file=sys.stderr)
    return records


def stage_1_scrape(out_dir: Path, institution_filter: str | None = None) -> int:
    """Stage 1: Fetch raw HTML/PDF from all configured sources.

    Returns:
        0 if all sources succeed or at least one succeeds.
        1 if all sources fail or fatal error.
    """
    print(f"\n{'='*60}")
    print("STAGE 1: SCRAPING")
    print(f"{'='*60}")

    out_dir.mkdir(parents=True, exist_ok=True)
    institutions = load_institutions()
    session = build_session()

    exit_code = 0
    ok_count = 0
    for institution, source in iter_sources(institutions):
        if institution_filter and institution.id != institution_filter:
            continue

        result = fetch_source(source, session=session)
        record = {
            "institution_id": result.institution_id,
            "campus": result.campus,
            "source_url": result.source_url,
            "fetched_at": result.fetched_at,
            "html": result.html,
            "pdfs": [{"url": p.url, "text": p.text, "error": p.error} for p in result.pdfs],
            "error": result.error,
        }

        campus_label = source.campus or "default"
        out_path = out_dir / f"{institution.id}__{campus_label}.json"
        out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

        if not result.ok:
            print(f"FAIL  {institution.id} ({campus_label}): {result.error}")
            exit_code = 1
        elif result.pdfs and len(result.pdf_failures) == len(result.pdfs):
            print(
                f"WARN  {institution.id} ({campus_label}): HTML fetched, "
                f"but all {len(result.pdfs)} linked PDF(s) failed"
            )
            ok_count += 1
        else:
            print(f"OK    {institution.id} ({campus_label})")
            ok_count += 1

    print(f"\nStage 1 summary: {ok_count} sources processed successfully")
    if exit_code != 0:
        print(f"[WARN] Some sources failed; {ok_count} succeeded. Proceeding with partial data.")

    return exit_code if ok_count == 0 else 0


def stage_2_chunk(scraped_dir: Path, out_path: Path) -> int:
    """Stage 2: Chunk scraped HTML into announcement blocks for classification.

    Returns:
        0 if chunking succeeds (even if produces 0 chunks).
        1 if fatal error (unreadable input).
    """
    print(f"\n{'='*60}")
    print("STAGE 2: CHUNKING")
    print(f"{'='*60}")

    records = _load_scraped_records(scraped_dir)
    if not records:
        print("ERROR: No scraped records found in", scraped_dir)
        return 1

    chunks = []
    skipped = 0
    for record in records:
        if record.get("error"):
            print(f"SKIP  {record.get('source_url', '?')}: scraper error")
            skipped += 1
            continue
        try:
            chunks.extend(chunk_scraped_record(record))
        except KeyError as exc:
            print(f"SKIP  malformed scraped record (missing {exc}): {record.get('source_url', '?')}")
            skipped += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([c.to_classifier_dict() for c in chunks], indent=2),
        encoding="utf-8",
    )

    print(f"Stage 2 summary: {len(chunks)} chunk(s) produced, {skipped} record(s) skipped")
    if len(chunks) == 0:
        print("[WARN] No chunks produced. Classifier will have no input.")
        return 1

    return 0


def stage_4_build(scraped_dir: Path, classified_path: Path, out_dir: Path) -> int:
    """Stage 4: Merge classifier results with field extraction into final records.

    Returns:
        0 if build succeeds (even if produces 0 records).
        1 if fatal error (unreadable inputs).
    """
    print(f"\n{'='*60}")
    print("STAGE 4: EXTRACTION BUILD")
    print(f"{'='*60}")

    records = _load_scraped_records(scraped_dir)
    if not records:
        print("ERROR: No scraped records found in", scraped_dir)
        return 1

    try:
        degree_levels = load_classifier_results(classified_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: Unreadable classifier output {classified_path}: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ERROR: Invalid classifier output {classified_path}: {exc}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0

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
            degree_level = degree_levels.get(chunk.id, DegreeLevel(value=None, reason="no-signal"))
            extracted = ExtractedRecord(
                institution_id=chunk.institution_id,
                campus=chunk.campus,
                source_url=chunk.source_url,
                fetched_at=chunk.fetched_at,
                chunk_id=chunk.id,
                degree_level=degree_level,
                constituent_college=extract_constituent_college(chunk.raw_text),
                deadline=extract_deadline(chunk.raw_text),
                fee=extract_fee(chunk.raw_text),
                programs=extract_programs(chunk.raw_text),
            )
            out_path = out_dir / f"{chunk.id}.json"
            out_path.write_text(json.dumps(extracted.to_dict(), indent=2), encoding="utf-8")
            written += 1

    print(f"Stage 4 summary: {written} record(s) extracted, {skipped} skipped")
    if written == 0:
        print("[WARN] No records extracted.")
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline orchestration: stages 1, 2, 4 (3 is external).")
    sub = parser.add_subparsers(dest="stage", required=True)

    stage1_p = sub.add_parser("stage1_2", help="Run scraper (stage 1) and chunking (stage 2).")
    stage1_p.add_argument("--out-scraped", type=Path, default=DEFAULT_SCRAPED_DIR, help="Output dir for scraped records.")
    stage1_p.add_argument("--out-chunks", type=Path, default=DEFAULT_CHUNKS_OUT, help="Output file for chunks.")
    stage1_p.add_argument("--institution", type=str, default=None, help="Filter to single institution (debug only).")

    stage4_p = sub.add_parser("stage4", help="Run extraction build (stage 4).")
    stage4_p.add_argument("--out-scraped", type=Path, default=DEFAULT_SCRAPED_DIR, help="Scraped records dir.")
    stage4_p.add_argument("--classified", type=Path, required=True, help="Classifier output file.")
    stage4_p.add_argument("--out", type=Path, default=DEFAULT_EXTRACTED_OUT, help="Output dir for extracted records.")

    args = parser.parse_args()

    if args.stage == "stage1_2":
        stage1_exit = stage_1_scrape(args.out_scraped, args.institution)
        if stage1_exit != 0:
            print("\n⚠️  Stage 1 had failures; proceeding anyway (partial data).")
        stage2_exit = stage_2_chunk(args.out_scraped, args.out_chunks)
        sys.exit(stage2_exit)
    elif args.stage == "stage4":
        sys.exit(stage_4_build(args.out_scraped, args.classified, args.out))


if __name__ == "__main__":
    main()
