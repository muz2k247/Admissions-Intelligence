"""CLI entry point for extraction, split into two steps because UG/PG
routing is done by the content-classifier subagent (a Claude Code Agent
tool call), not by this script:

    1. chunk  — read scraper/run.py's output, produce one chunk-input file
                for the content-classifier subagent to consume.
    2. build  — read the classifier's output file plus the same scraped
                records, run field extraction, and write final
                ExtractedRecord JSON (one file per source) to an out dir.

Usage:
    python -m extraction.run chunk --scraped-dir .tmp/scraped --out .tmp/chunks/chunks.json
    python -m extraction.run build --scraped-dir .tmp/scraped --classified .tmp/chunks/classified.json --out .tmp/extracted
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


def run_build(scraped_dir: Path, classified_path: Path, out_dir: Path) -> int:
    records = _load_scraped_records(scraped_dir)
    try:
        degree_levels = load_classifier_results(classified_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"FAIL  unreadable classifier output {classified_path}: {exc}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for record in records:
        if record.get("error"):
            continue
        try:
            chunks = chunk_scraped_record(record)
        except KeyError as exc:
            print(f"SKIP  malformed scraped record (missing {exc}): {record.get('source_url', '?')}")
            continue

        for chunk in chunks:
            degree_level = degree_levels.get(
                chunk.id, DegreeLevel(value=None, reason="no-signal")
            )
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

    print(f"Wrote {written} extracted record(s) -> {out_dir}")
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

    args = parser.parse_args()

    if args.command == "chunk":
        sys.exit(run_chunk(args.scraped_dir, args.out))
    elif args.command == "build":
        sys.exit(run_build(args.scraped_dir, args.classified, args.out))


if __name__ == "__main__":
    main()
