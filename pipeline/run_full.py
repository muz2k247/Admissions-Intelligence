"""Full pipeline orchestration: scraper → chunking → classification → extraction → static publish.

Stages 1, 2, 4, 5 run here (deterministic Python). Stage 3 (classifier) and the
field-extractor step are both invoked separately by the calling orchestration
prompt via Claude Code Agent, against the same stage-2 chunks file.

Usage:
    Stage 1-2: python -m pipeline.run_full stage1_2 --out-scraped .tmp/scraped --out-chunks .tmp/chunks/chunks.json
    Stage 4:   python -m pipeline.run_full stage4 --out-scraped .tmp/scraped --classified .tmp/chunks/classified.json --llm-extracted .tmp/chunks/llm_fields.json --out .tmp/extracted
    Stage 5:   python -m pipeline.run_full stage5 --extracted .tmp/extracted

Returns:
    Exit code 0 if all stages succeed. Exit code 1 if any stage fails.
    All failures logged to stdout (no silent errors).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from extraction.chunker import chunk_scraped_record
from extraction.classify import load_classifier_results
from extraction.llm_fields import load_llm_field_results
from extraction.run import build_extracted_records, write_extracted_records
from extraction.schema import ExtractedRecord
from pipeline.overrides import fetch_overrides, merge_overrides
from scraper.config import load_institutions, iter_sources
from scraper.fetch import build_session, fetch_source

DEFAULT_SCRAPED_DIR = Path(".tmp") / "scraped"
DEFAULT_CHUNKS_OUT = Path(".tmp") / "chunks" / "chunks.json"
DEFAULT_EXTRACTED_OUT = Path(".tmp") / "extracted"
DEFAULT_PUBLISH_DIR = Path("dashboard") / "frontend" / "public" / "data"


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
        0 if at least one source was attempted and at least one succeeded.
        1 if zero sources were attempted at all (every institution disabled,
          or --institution matched nothing), or every attempted source
          failed, or a fatal error.
    """
    print(f"\n{'='*60}")
    print("STAGE 1: SCRAPING")
    print(f"{'='*60}")

    out_dir.mkdir(parents=True, exist_ok=True)
    institutions = load_institutions()
    session = build_session()

    disabled = [i.id for i in institutions if not i.enabled]
    if disabled:
        print(f"[SKIP] {len(disabled)} institution(s) disabled in config: {', '.join(disabled)}")

    # On a full run, clear stale scraped files first: a source renamed or
    # removed in config (e.g. UET's Taxila campus becoming its own institution)
    # would otherwise leave an orphan JSON here that later stages treat as a
    # live source. Skip this when filtering to a single institution for debug,
    # so other institutions' scraped data isn't wiped.
    if institution_filter is None:
        for stale in out_dir.glob("*.json"):
            stale.unlink()

    exit_code = 0
    ok_count = 0
    attempted = 0
    for institution, source in iter_sources(institutions):
        if institution_filter and institution.id != institution_filter:
            continue
        attempted += 1

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

    if attempted == 0:
        reason = f"--institution '{institution_filter}' matched no enabled institution" if institution_filter else "0 institutions are enabled in config"
        print(f"ERROR: No sources attempted ({reason}).", file=sys.stderr)
        return 1

    print(f"\nStage 1 summary: {ok_count} of {attempted} attempted source(s) processed successfully")
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


def stage_4_build(scraped_dir: Path, classified_path: Path, out_dir: Path, llm_extracted_path: Path | None = None) -> int:
    """Stage 4: Merge classifier results with field extraction into final records.

    llm_extracted_path, when given, is the field-extractor subagent's output
    file -- its fields win per chunk where present; omitted, unreadable, or a
    chunk not covered by it, falls back to the regex extractor
    (extraction/fields.py). Unlike classified_path, a missing/corrupt
    llm_extracted_path is never fatal to the run -- see the warning printed
    at load time.

    Returns:
        0 if build succeeds (even if produces 0 records).
        1 if fatal error (unreadable scraped records or classifier output).
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

    llm_fields = None
    if llm_extracted_path is not None:
        try:
            llm_fields = load_llm_field_results(llm_extracted_path)
        except (json.JSONDecodeError, OSError) as exc:
            # Degrade, don't fail the run: a missing/corrupt field-extractor
            # output must behave exactly like --llm-extracted was never
            # passed (full regex fallback), not like a fatal error -- the
            # pipeline's graceful-degradation guarantee shouldn't depend on
            # the orchestration prompt correctly omitting the flag under
            # every failure mode (e.g. field-extractor timed out before
            # writing anything at all).
            print(f"WARN: Unreadable field-extractor output {llm_extracted_path}: {exc} -- falling back to regex extraction for all chunks")
            llm_fields = None

    built, skipped, excluded_postgraduate = build_extracted_records(records, degree_levels, llm_fields)
    written = write_extracted_records(built, out_dir)

    print(
        f"Stage 4 summary: {written} record(s) extracted, {skipped} skipped, "
        f"{excluded_postgraduate} postgraduate record(s) excluded (undergrad-only scope)"
    )
    if written == 0:
        print("[WARN] No records extracted.")
        return 1

    return 0


def _institutions_payload() -> list[dict]:
    institutions = load_institutions()
    return [
        {
            "id": inst.id,
            "name": inst.name,
            "admitting_body": inst.admitting_body,
            "ug_pg_mixed": inst.ug_pg_mixed,
            "campuses": [s.campus for s in inst.sources if s.campus is not None],
            "enabled": inst.enabled,
        }
        for inst in institutions
    ]


def _write_json_files_atomic(files: dict[Path, object]) -> None:
    """Write every (path -> payload) pair to a PID-scoped temp sibling first;
    only once ALL temp writes succeed are they os.replace()'d into place.

    This publishes records.json and institutions.json as one unit: if a
    later file's write fails after an earlier file's temp write already
    succeeded, nothing has been replaced yet, so neither previously-published
    file is touched -- avoiding a mismatched pair (e.g. new records.json
    paired with stale institutions.json). The PID-scoped temp name also
    avoids two overlapping pipeline runs racing on the same temp path.
    Raises OSError on failure; caller decides how to report it.
    """
    tmp_paths: dict[Path, Path] = {}
    try:
        for path, payload in files.items():
            tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_paths[path] = tmp_path
    except OSError:
        for tmp_path in tmp_paths.values():
            tmp_path.unlink(missing_ok=True)
        raise
    for path, tmp_path in tmp_paths.items():
        os.replace(tmp_path, path)


def stage_5_publish(extracted_dir: Path, publish_dir: Path) -> int:
    """Stage 5: Build the static data/*.json artifacts the dashboard fetches
    directly (no backend, no database).

    Records are loaded from `extracted_dir` into memory first; only once that
    succeeds — and only if at least one record was found — are the output
    files written, so neither a read failure nor an empty/wrong extracted_dir
    can corrupt or blank out the previously-published (live) artifacts (same
    "build fully in memory before touching the destination" pattern as
    write_extracted_records, extended here to guard zero-output the same way
    stage_2_chunk/stage_4_build already do).

    Human-verified curator corrections (admin CMS, Phase K) are merged in
    after loading and before writing: fetch_overrides() reads them from
    Firestore, returning {} on any failure so a Firestore problem degrades
    to publishing the pipeline-extracted values rather than failing.

    Returns:
        0 if publish succeeds.
        1 if extracted_dir is unreadable/missing, contains zero records,
          any record fails to load, config/institutions.yaml is unreadable,
          or writing the published files fails.
    """
    print(f"\n{'='*60}")
    print("STAGE 5: BUILD & PUBLISH STATIC DATA")
    print(f"{'='*60}")

    if not extracted_dir.is_dir():
        print(f"ERROR: Extracted records dir not found: {extracted_dir}", file=sys.stderr)
        return 1

    records: list[ExtractedRecord] = []
    for path in sorted(extracted_dir.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                records.append(ExtractedRecord.from_dict(json.load(f)))
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as exc:
            print(f"ERROR: Unreadable extracted record {path}: {exc}", file=sys.stderr)
            return 1

    if not records:
        print(
            f"ERROR: No records found in {extracted_dir}; refusing to publish "
            "and overwrite previously-published data. Previous data retained.",
            file=sys.stderr,
        )
        return 1

    # Merge in human-verified curator corrections (admin CMS, Phase K).
    # fetch_overrides() returns {} on any failure, so a Firestore problem
    # degrades to publishing the pipeline-extracted values -- never fatal.
    overrides = fetch_overrides()
    if overrides:
        records = [merge_overrides(r, overrides) for r in records]
        overridden = sum(1 for r in records if r.chunk_id in overrides)
        print(f"Applied curator overrides to {overridden} of {len(records)} record(s).")

    try:
        institutions_payload = _institutions_payload()
    except (OSError, KeyError, ValueError) as exc:
        print(f"ERROR: config/institutions.yaml is unreadable: {exc}", file=sys.stderr)
        return 1

    publish_dir.mkdir(parents=True, exist_ok=True)
    try:
        _write_json_files_atomic({
            publish_dir / "records.json": [r.to_dict() for r in records],
            publish_dir / "institutions.json": institutions_payload,
        })
    except OSError as exc:
        print(f"ERROR: Failed to write published data: {exc}", file=sys.stderr)
        return 1

    print(
        f"Stage 5 summary: {len(records)} record(s) and {len(institutions_payload)} "
        f"institution(s) published to {publish_dir}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline orchestration: stages 1, 2, 4, 5 (3 is external).")
    sub = parser.add_subparsers(dest="stage", required=True)

    stage1_p = sub.add_parser("stage1_2", help="Run scraper (stage 1) and chunking (stage 2).")
    stage1_p.add_argument("--out-scraped", type=Path, default=DEFAULT_SCRAPED_DIR, help="Output dir for scraped records.")
    stage1_p.add_argument("--out-chunks", type=Path, default=DEFAULT_CHUNKS_OUT, help="Output file for chunks.")
    stage1_p.add_argument("--institution", type=str, default=None, help="Filter to single institution (debug only).")

    stage4_p = sub.add_parser("stage4", help="Run extraction build (stage 4).")
    stage4_p.add_argument("--out-scraped", type=Path, default=DEFAULT_SCRAPED_DIR, help="Scraped records dir.")
    stage4_p.add_argument("--classified", type=Path, required=True, help="Classifier output file.")
    stage4_p.add_argument("--out", type=Path, default=DEFAULT_EXTRACTED_OUT, help="Output dir for extracted records.")
    stage4_p.add_argument(
        "--llm-extracted", type=Path, default=None,
        help="field-extractor subagent output file. When omitted, falls back to the regex extractor for every chunk.",
    )

    stage5_p = sub.add_parser("stage5", help="Build static dashboard data/*.json artifacts (stage 5).")
    stage5_p.add_argument("--extracted", type=Path, default=DEFAULT_EXTRACTED_OUT, help="Extracted records dir.")
    stage5_p.add_argument(
        "--publish-dir", type=Path, default=DEFAULT_PUBLISH_DIR,
        help="Output dir for published data/*.json (served as static dashboard data).",
    )

    args = parser.parse_args()

    if args.stage == "stage1_2":
        stage1_exit = stage_1_scrape(args.out_scraped, args.institution)
        if stage1_exit != 0:
            print("\n⚠️  Stage 1 had failures; proceeding anyway (partial data).")
        stage2_exit = stage_2_chunk(args.out_scraped, args.out_chunks)
        sys.exit(stage2_exit)
    elif args.stage == "stage4":
        sys.exit(stage_4_build(args.out_scraped, args.classified, args.out, args.llm_extracted))
    elif args.stage == "stage5":
        sys.exit(stage_5_publish(args.extracted, args.publish_dir))


if __name__ == "__main__":
    main()
