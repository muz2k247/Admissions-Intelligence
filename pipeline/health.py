"""Pipeline run health tracking (Phase T Task 4).

Every pipeline run -- published, refused, or crashed -- leaves a durable,
machine-readable record as a 4th static file (dashboard/frontend/public/
data/health.json), alongside records.json/institutions.json/needs_review.json.
No new backend: git history of health.json is the free time series (see
CLAUDE.md Phase T Task 4 / "Do NOT build" #7).

Two-phase design, mirroring why _write_json_files_atomic exists in
run_full.py: stages accumulate their sections into a small on-disk fragment
(.tmp/health/run_health.json) as the run progresses via record_stage(), so
that a crash mid-run still leaves whatever sections were reached; finalize()
is called exactly once at the very end (CI: `if: always()`) to derive an
overall status/warnings from whichever sections exist and atomically publish
health.json. Absence of a section IS the signal (stage not reached) -- v1
carries no field a stage doesn't already compute today (see the schema
anti-over-engineering note in the Phase T plan).

record_stage() never raises: a health-tracking bug must never fail the
pipeline stage it's observing. _derive_status/_write_fragment/_read_fragment
are the only I/O-touching helpers; the derivation logic itself is a pure
function of the fragment dict, kept separate for testability (house style,
cf. pipeline/schedule_gate.py's compute_is_due).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_FRAGMENT_PATH = Path(".tmp") / "health" / "run_health.json"

STATUS_PUBLISHED = "published"
STATUS_REFUSED = "publish_refused"
STATUS_FAILED = "failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_fragment(fragment_path: Path, fragment: dict) -> None:
    """Atomic replace (temp file + os.replace), same pattern as finalize()'s
    health.json write -- a process killed mid-write must never leave a
    truncated fragment behind, which _read_fragment's corruption fallback
    would otherwise silently treat as "nothing recorded yet", discarding
    every previously-recorded stage section."""
    fragment_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = fragment_path.with_name(f"{fragment_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(fragment, indent=2), encoding="utf-8")
    os.replace(tmp_path, fragment_path)


def _read_fragment(fragment_path: Path) -> dict:
    """Read the fragment accumulated so far. A missing or corrupt fragment
    degrades to a bare skeleton rather than raising -- callers (init_run,
    record_stage, finalize) must keep working even if the fragment file was
    never created (e.g. finalize running after a crash before stage 1)."""
    if not fragment_path.is_file():
        return {"schema_version": SCHEMA_VERSION}
    try:
        data = json.loads(fragment_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"schema_version": SCHEMA_VERSION}
    except (json.JSONDecodeError, OSError):
        return {"schema_version": SCHEMA_VERSION}


def init_run(
    fragment_path: Path = DEFAULT_FRAGMENT_PATH,
    trigger: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Create a fresh run-health fragment, called once at the very start of
    a run before any stage. run_id/trigger default from the GITHUB_RUN_ID /
    GITHUB_EVENT_NAME env vars GitHub Actions sets automatically; explicit
    overrides exist for local/test use where those env vars aren't set."""
    fragment = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or os.environ.get("GITHUB_RUN_ID"),
        "trigger": trigger or os.environ.get("GITHUB_EVENT_NAME"),
        "started_at": _now_iso(),
    }
    _write_fragment(fragment_path, fragment)
    return fragment


def record_stage(name: str, payload: dict, fragment_path: Path = DEFAULT_FRAGMENT_PATH) -> None:
    """Merge `payload` into the fragment's `name` section (e.g. "scrape",
    "chunk", "classify", "extract_llm", "build", "publish"). Overwrites any
    prior value for that section -- callers pass their full section payload
    in one call, not incremental patches. Never raises: a failure here must
    not fail the pipeline stage it's observing, only warn.

    Not safe for concurrent callers (read-modify-write, no locking) -- fine
    for today's strictly-sequential 5-stage run_full.py; a future
    parallelized stage calling this concurrently would need a lock added
    here first."""
    try:
        fragment = _read_fragment(fragment_path)
        fragment[name] = payload
        _write_fragment(fragment_path, fragment)
    except (OSError, TypeError, ValueError) as exc:
        # TypeError/ValueError: json.dumps can raise on a non-JSON-
        # serializable payload (e.g. a caller accidentally passing a
        # datetime or set) -- must degrade to a warning, never crash the
        # pipeline stage this call is only meant to observe.
        print(f"WARN  could not record health for stage '{name}': {exc}", file=sys.stderr)


def _derive_status(fragment: dict) -> tuple[str, list[str]]:
    """Pure derivation of (status, warnings) from whichever sections are
    present in `fragment`. No I/O -- testable without a fragment file."""
    warnings: list[str] = []

    publish = fragment.get("publish")
    if isinstance(publish, dict):
        decision = publish.get("decision")
        if decision == "published":
            status = STATUS_PUBLISHED
        elif decision in ("refused_coverage_drop", "refused_no_records"):
            status = STATUS_REFUSED
            warnings.append(f"Publish refused: {decision}")
        elif isinstance(decision, str) and decision.startswith("failed_"):
            # Named failure decisions run_full.py's stage_5_publish records
            # for every one of its own early-return branches (e.g.
            # failed_write_error, failed_unreadable_record) -- these are
            # anticipated, not "unrecognized"; the distinct message keeps
            # them out of the generic unrecognized-value bucket below.
            status = STATUS_FAILED
            warnings.append(f"Publish failed: {decision}")
        else:
            status = STATUS_FAILED
            warnings.append(f"Unrecognized publish decision: {decision!r}")
    else:
        status = STATUS_FAILED
        warnings.append("Run did not reach stage 5 (publish) — check which stage sections below are present.")

    build = fragment.get("build")
    if isinstance(build, dict):
        mode = build.get("extraction_mode")
        if mode == "regex_fallback":
            warnings.append("Extraction fell back to regex for every chunk this run (LLM extraction unavailable).")
        elif mode == "mixed":
            warnings.append("Extraction used a mix of LLM and regex-fallback fields this run.")

    scrape = fragment.get("scrape")
    if isinstance(scrape, dict):
        failed = scrape.get("failed")
        attempted = scrape.get("attempted")
        if isinstance(failed, int) and failed > 0:
            attempted_label = attempted if isinstance(attempted, int) else "?"
            warnings.append(f"{failed} of {attempted_label} source(s) failed to scrape.")

    return status, warnings


def finalize(publish_dir: Path, fragment_path: Path = DEFAULT_FRAGMENT_PATH) -> dict:
    """Derive the final health.json from the accumulated fragment and write
    it to publish_dir/health.json (atomic replace, same pattern as
    run_full.py's _write_json_files_atomic). Called exactly once, as the
    last step of a run, with `if: always()` in CI so it runs after a crash
    too. Returns the written payload."""
    fragment = _read_fragment(fragment_path)
    status, warnings = _derive_status(fragment)
    # Guarantee the schema-v1 top-level keys always exist (as null, not
    # absent) even if init_run() never ran for this fragment -- e.g. a crash
    # before stage 1, or finalize() invoked against a fresh/missing fragment
    # path -- so a consumer (dashboard/CMS) can rely on a stable key set
    # rather than defensively checking for each one's presence.
    health = {
        "schema_version": SCHEMA_VERSION,
        "run_id": None,
        "trigger": None,
        "started_at": None,
        **fragment,
        "finished_at": _now_iso(),
        "status": status,
        "warnings": warnings,
    }

    publish_dir.mkdir(parents=True, exist_ok=True)
    dest = publish_dir / "health.json"
    tmp_path = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(health, indent=2), encoding="utf-8")
    os.replace(tmp_path, dest)
    return health


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline run health tracking (Phase T Task 4).")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Start a fresh run-health fragment.")
    init_p.add_argument("--fragment", type=Path, default=DEFAULT_FRAGMENT_PATH)

    finalize_p = sub.add_parser("finalize", help="Derive and publish health.json from the accumulated run fragment.")
    finalize_p.add_argument(
        "--publish-dir", type=Path, required=True,
        help="Output dir for health.json (same dir as records.json/institutions.json/needs_review.json).",
    )
    finalize_p.add_argument("--fragment", type=Path, default=DEFAULT_FRAGMENT_PATH)

    args = parser.parse_args()
    if args.command == "init":
        init_run(args.fragment)
        print(f"Run health fragment initialized -> {args.fragment}")
        return 0
    if args.command == "finalize":
        health = finalize(args.publish_dir, args.fragment)
        print(f"health.json finalized: status={health['status']}, warnings={len(health['warnings'])}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
