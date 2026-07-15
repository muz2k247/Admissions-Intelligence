"""Full pipeline orchestration: scraper → chunking → classification → extraction → static publish.

Stages 1, 2, 4, 5 run here (deterministic Python). Stage 3 (classifier) and the
field-extractor step are both invoked separately by the calling orchestration
prompt via Gemini Agent, against the same stage-2 chunks file.

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
from extraction.review_gate import content_hash, flagged_fields, needs_review
from extraction.run import build_extracted_records, write_extracted_records
from extraction.schema import ExtractedRecord
from pipeline.overrides import fetch_overrides, merge_overrides
from pipeline.review import fetch_review_decisions, fetch_review_settings
from scraper.config import load_institutions, iter_sources
from scraper.fetch import build_session, fetch_source

DEFAULT_SCRAPED_DIR = Path(".tmp") / "scraped"
DEFAULT_CHUNKS_OUT = Path(".tmp") / "chunks" / "chunks.json"
DEFAULT_EXTRACTED_OUT = Path(".tmp") / "extracted"
DEFAULT_PUBLISH_DIR = Path("dashboard") / "frontend" / "public" / "data"

_COVERAGE_FIELDS = ("deadline", "programs", "constituent_college")
_COVERAGE_DROP_THRESHOLD = 0.5  # refuse to publish if coverage falls below this fraction of the last published run


def _institution_count(records: list[ExtractedRecord]) -> int:
    """Distinct institutions represented in a record set. Field coverage
    alone can't tell "extraction quality regressed" apart from "most
    institutions failed to scrape but the few that succeeded extracted
    cleanly" -- a mass scrape outage can leave fill-rate unchanged or even
    higher while the actual institution count collapses. This is checked
    alongside _field_coverage, not instead of it."""
    return len({r.institution_id for r in records})


def _field_coverage(records: list[ExtractedRecord]) -> float | None:
    """Fraction of (record, field) pairs across _COVERAGE_FIELDS that carry
    a non-null value. None when there is nothing to measure (empty input),
    so callers can distinguish "no data" from "0% coverage"."""
    if not records:
        return None
    total = len(records) * len(_COVERAGE_FIELDS)
    non_null = sum(
        1 for r in records for name in _COVERAGE_FIELDS if getattr(r, name).value is not None
    )
    return non_null / total


def _load_published_records(path: Path) -> list[ExtractedRecord]:
    """Load a previously-published records.json or needs_review.json for
    the coverage-guard baseline. Returns [] if the file is missing or
    malformed -- there's nothing trustworthy to compare against, which the
    guard's own None/0 checks already treat as "skip this signal" rather
    than a reason to fail stage 5 (which validates the *new* records
    separately, above)."""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [ExtractedRecord.from_dict(d) for d in raw]
    except Exception:
        # Deliberately broad: the failure modes here are open-ended (bad
        # JSON, wrong shape, unexpected None entries), and every one of them
        # should degrade to "nothing to compare against", not crash an
        # otherwise-valid publish.
        return []


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

    stats: dict[str, int] = {}
    built, skipped, excluded_postgraduate = build_extracted_records(records, degree_levels, llm_fields, stats=stats)
    written = write_extracted_records(built, out_dir)

    print(
        f"Stage 4 summary: {written} record(s) extracted ({stats['llm_chunks']} via LLM, "
        f"{stats['regex_chunks']} via regex fallback), {skipped} skipped, "
        f"{excluded_postgraduate} postgraduate record(s) excluded (undergrad-only scope)"
    )
    if written == 0:
        print("[WARN] No records extracted.")
        return 1

    # llm_extracted_path being set means the field-extractor step ran and
    # produced a readable file (see the WARN branch above for the unreadable
    # case) -- but the file loading cleanly doesn't mean it covered anything.
    # A CI job with `continue-on-error: true` on that step can look green
    # while every chunk silently fell back to regex, which is exactly the
    # kind of degraded-but-successful run this pipeline needs to surface
    # instead of publishing quietly.
    if llm_extracted_path is not None and llm_fields is not None and stats["llm_chunks"] == 0 and stats["regex_chunks"] > 0:
        print(
            "[WARN] LLM field extraction produced 0 usable chunks; every record used the "
            "regex fallback this run -- check Gemini API health (key, quota, model)."
        )

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

    This publishes every file passed in (records.json, institutions.json,
    and needs_review.json) as one unit: if a later file's write fails after
    an earlier file's temp write already succeeded, nothing has been
    replaced yet, so none of the previously-published files are touched --
    avoiding a mismatched set (e.g. new records.json paired with stale
    institutions.json, or a needs_review.json that's out of sync with
    either). The PID-scoped temp name also avoids two overlapping pipeline
    runs racing on the same temp path. Raises OSError on failure; caller
    decides how to report it.
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


def stage_5_publish(extracted_dir: Path, publish_dir: Path, allow_coverage_drop: bool = False) -> int:
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

    Before writing, two signals for the new record set are compared against
    the currently-published records.json + needs_review.json (the FULL
    record set, unaffected by the Needs-Review gate below -- see that
    section's comment for why): field coverage (_field_coverage) and
    distinct institution count (_institution_count) -- coverage alone can't
    tell "extraction quality regressed" apart from "most institutions
    failed to scrape but the few survivors extracted cleanly," since the
    latter can leave fill-rate flat or even higher while the actual data
    volume collapses. A zero-record run is already refused above the
    empty-records check; these two catch quieter failure modes: extraction
    running "successfully" (e.g. a best-effort Gemini step degrading to the
    regex fallback for every chunk) but yielding far less/narrower real data
    than the run it would replace. Refusing to publish keeps the previous,
    better data live instead of silently degrading the public dashboard.
    Pass allow_coverage_drop=True to publish anyway (e.g. a genuine, expected
    drop like admissions season ending, or an institution intentionally
    disabled in config).

    Needs-Review gate (Phase Q): after the coverage guard, each record is
    evaluated by extraction/review_gate.py::needs_review() against an
    admin-configurable confidence threshold (pipeline/review.py::
    fetch_review_settings(), defaulting fail-safe to {enabled: True,
    threshold: 0.8} on any failure). A record that isn't flagged auto-
    publishes. A flagged record only publishes if a matching curator
    decision exists in Firestore's review_decisions collection (pipeline/
    review.py::fetch_review_decisions()) whose stored content_hash equals
    the record's CURRENT content_hash -- a re-scrape that changes any
    reviewable field produces a different hash, so a stale approval/
    rejection doesn't silently keep publishing or dropping a record whose
    content has since changed; it re-queues instead. Rejected (matching-
    hash) records are dropped entirely. Everything else pending lands in
    needs_review.json (published alongside records.json, technically
    public like every other pipeline output in this project -- security
    here is Firestore write rules, not file obscurity) for curator review.
    Disabling the gate (settings.enabled = False) publishes every record
    exactly as before Phase Q, with an empty needs_review.json.

    Returns:
        0 if publish succeeds.
        1 if extracted_dir is unreadable/missing, contains zero records,
          any record fails to load, config/institutions.yaml is unreadable,
          field coverage regressed more than _COVERAGE_DROP_THRESHOLD without
          allow_coverage_drop, or writing the published files fails.
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
        merged_records = [merge_overrides(r, overrides) for r in records]
        # Count records that actually changed, not merely records whose
        # chunk_id had an override entry -- merge_overrides can drop every
        # field in that entry as stale (Phase Q), in which case the record
        # is unchanged and shouldn't be counted as "applied".
        overridden = sum(1 for before, after in zip(records, merged_records) if before != after)
        records = merged_records
        print(f"Applied curator overrides to {overridden} of {len(records)} record(s).")

    new_coverage = _field_coverage(records)
    new_institution_count = _institution_count(records)
    # Baseline is the UNION of the previously-published records.json and
    # needs_review.json -- the full extraction from the last run, not just
    # what the Needs-Review gate let through to records.json. Comparing a
    # gated subset against this run's full set would make turning the gate
    # on (or a curator's approve/reject backlog shrinking/growing) look like
    # a coverage regression even though nothing about extraction changed.
    #
    # Known accepted gap: a record with a matching-hash "rejected" decision
    # (see the gating block below) is dropped from BOTH files and so drops
    # out of this baseline too, permanently (as long as its content -- and
    # thus content_hash -- doesn't change). A later genuine scrape failure
    # for exactly that record would not be caught by this guard, since the
    # baseline stopped counting it once rejected. Accepted rather than fixed
    # here: closing it would mean persisting a separate pre-gate "last full
    # extraction" snapshot purely for guard bookkeeping, which is more
    # machinery than this narrow edge case (a curator having actively
    # rejected a record, which a human already looked at) currently
    # justifies.
    previous_records = _load_published_records(publish_dir / "records.json")
    previous_needs_review = _load_published_records(publish_dir / "needs_review.json")
    previous_full = previous_records + previous_needs_review
    previous_coverage = _field_coverage(previous_full) if previous_full else None
    previous_institution_count = _institution_count(previous_full) if previous_full else None

    coverage_dropped = (
        previous_coverage is not None
        and previous_coverage > 0
        and new_coverage is not None
        and new_coverage < previous_coverage * _COVERAGE_DROP_THRESHOLD
    )
    institutions_dropped = (
        previous_institution_count is not None
        and previous_institution_count > 0
        and new_institution_count < previous_institution_count * _COVERAGE_DROP_THRESHOLD
    )

    if (coverage_dropped or institutions_dropped) and not allow_coverage_drop:
        if institutions_dropped:
            print(
                f"ERROR: Institution count dropped from {previous_institution_count} to "
                f"{new_institution_count} (more than {int((1 - _COVERAGE_DROP_THRESHOLD) * 100)}% "
                "relative drop) -- refusing to publish a likely mass-scrape-failure run. "
                "Previous data retained.",
                file=sys.stderr,
            )
        if coverage_dropped:
            print(
                f"ERROR: Field coverage dropped from {previous_coverage:.0%} to {new_coverage:.0%} "
                f"(more than {int((1 - _COVERAGE_DROP_THRESHOLD) * 100)}% relative drop) -- refusing to "
                "publish a likely-degraded extraction run. Previous data retained.",
                file=sys.stderr,
            )
        print(
            "If this drop is genuine (e.g. admissions season ending, or an institution was "
            "intentionally disabled), re-run with allow_coverage_drop=True / --allow-coverage-drop.",
            file=sys.stderr,
        )
        return 1

    # Needs-Review gate (Phase Q). fetch_review_settings() returns {} on any
    # failure -- fail-safe direction is "gate stays on", never "gate off"
    # (see pipeline/review.py). Applied to the same full `records` list the
    # coverage guard above just measured, so gating never influences that
    # guard.
    settings = fetch_review_settings()
    to_publish: list[ExtractedRecord] = []
    to_queue: list[dict] = []
    if not settings.get("enabled", True):
        to_publish = records
        print("Needs-review gate disabled (settings/review_gate) -- publishing all records.")
    else:
        threshold = settings.get("threshold", 0.8)
        # Defensive: fetch_review_settings() already validates threshold is
        # a bounded float before returning it, but don't let a future
        # change to that contract (or a test/mocked settings dict) turn
        # into a crashed publish here -- degrade to the default instead.
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not (0.0 <= threshold <= 1.0):
            threshold = 0.8
        decisions = fetch_review_decisions()
        auto_count = approved_count = rejected_count = 0
        for record in records:
            if not needs_review(record, threshold=threshold):
                to_publish.append(record)
                auto_count += 1
                continue
            record_hash = content_hash(record)
            decision = decisions.get(record.chunk_id)
            if decision is not None and decision.get("content_hash") == record_hash:
                if decision["decision"] == "approved":
                    to_publish.append(record)
                    approved_count += 1
                    continue
                # "rejected" -- dropped entirely, not published, not queued.
                rejected_count += 1
                continue
            # No decision, or a stale one (hash no longer matches this run's
            # content) -- stays pending for curator review.
            queued = record.to_dict()
            queued["flagged_fields"] = flagged_fields(record, threshold=threshold)
            queued["content_hash"] = record_hash
            to_queue.append(queued)
        print(
            f"Needs-review gate (threshold={threshold}): {auto_count} auto-published, "
            f"{approved_count} approved from queue, {rejected_count} rejected (dropped), "
            f"{len(to_queue)} pending review."
        )

    try:
        institutions_payload = _institutions_payload()
    except (OSError, KeyError, ValueError) as exc:
        print(f"ERROR: config/institutions.yaml is unreadable: {exc}", file=sys.stderr)
        return 1

    publish_dir.mkdir(parents=True, exist_ok=True)
    try:
        _write_json_files_atomic({
            publish_dir / "records.json": [r.to_dict() for r in to_publish],
            publish_dir / "institutions.json": institutions_payload,
            publish_dir / "needs_review.json": to_queue,
        })
    except OSError as exc:
        print(f"ERROR: Failed to write published data: {exc}", file=sys.stderr)
        return 1

    print(
        f"Stage 5 summary: {len(to_publish)} record(s), {len(to_queue)} queued for review, "
        f"and {len(institutions_payload)} institution(s) published to {publish_dir}"
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
    stage5_p.add_argument(
        "--allow-coverage-drop", action="store_true",
        help="Publish even if field coverage dropped more than the regression threshold vs the currently-published data.",
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
        sys.exit(stage_5_publish(args.extracted, args.publish_dir, args.allow_coverage_drop))


if __name__ == "__main__":
    main()
