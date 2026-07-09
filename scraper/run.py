"""CLI entry point: fetch every source in config/institutions.yaml and save
raw HTML/PDF-text output to an output directory, one JSON file per source.

Usage:
    python -m scraper.run [--out DIR] [--institution ID]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from scraper.config import load_institutions, iter_sources
from scraper.fetch import build_session, fetch_source

DEFAULT_OUT_DIR = Path(".tmp") / "scraped"


def slugify_source(institution_id: str, campus: str | None) -> str:
    if campus is None:
        return institution_id
    campus_slug = campus.lower().replace("&", " and ")
    campus_slug = re.sub(r"[^a-z0-9]+", "_", campus_slug).strip("_")
    return f"{institution_id}__{campus_slug}"


def run(out_dir: Path, institution_filter: str | None = None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    institutions = load_institutions()
    session = build_session()

    exit_code = 0
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
            "pdfs": [
                {"url": p.url, "text": p.text, "error": p.error} for p in result.pdfs
            ],
            "error": result.error,
        }

        out_path = out_dir / f"{slugify_source(institution.id, source.campus)}.json"
        out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

        if not result.ok:
            print(f"FAIL  {institution.id} ({source.campus or 'default'}): {result.error}")
            exit_code = 1
        elif result.pdfs and len(result.pdf_failures) == len(result.pdfs):
            print(
                f"WARN  {institution.id} ({source.campus or 'default'}): HTML fetched, "
                f"but all {len(result.pdfs)} linked PDF(s) failed -> {out_path}"
            )
        else:
            print(f"OK    {institution.id} ({source.campus or 'default'}) -> {out_path}")

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch raw HTML/PDF for all configured admission sources.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for raw JSON records.")
    parser.add_argument("--institution", type=str, default=None, help="Only fetch this institution id.")
    args = parser.parse_args()

    sys.exit(run(args.out, args.institution))


if __name__ == "__main__":
    main()
