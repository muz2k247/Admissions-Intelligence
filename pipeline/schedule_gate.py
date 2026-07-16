"""Admin-configurable pipeline scheduling (Phase S).

Same unauthenticated-REST, graceful-degradation contract as
pipeline/overrides.py and pipeline/review.py (see those modules' docstrings
for the full rationale): no credential is needed because Firestore security
rules -- not key secrecy -- gate writes to an allowlisted curator UID, and
any fetch failure here must degrade to a safe default rather than raise.

Two documents:

- `settings/pipeline_schedule` -- the curator's chosen cadence. `mode`
  selects one of five shapes (manual / interval_hours / weekly /
  interval_weeks / monthly), each with its own required fields (see
  DEFAULT_SCHEDULE and _validate_schedule below). Missing/unreadable/
  malformed defaults to {"mode": "manual"} -- the fail-safe direction, since
  guessing a cadence the curator never configured would violate hard rule 1
  (never infer/guess a missing value) just as much as guessing any other
  field would.
- `settings/pipeline_run_request` -- written by the CMS's "Run Now" button
  ({requested_by, requested_at}). Never cleared by anyone: whether it's
  still "pending" is derived by comparing requested_at against the most
  recent pipeline run's start time (self-healing, no write-back needed --
  see is_run_requested).

compute_is_due / is_run_requested are pure functions operating on already-
parsed datetimes so the cadence math is testable without any Firestore or
GitHub API involved; the module's `main()` (added in the chunk that wires up
the tick workflow) is what fetches from Firestore and the GitHub Actions API
and calls these.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from pipeline._firestore import (
    FIRESTORE_EXCEPTIONS,
    decode_value,
    fetch_document,
    load_project_id,
)

_DEFAULT_TIMEOUT = 30

DEFAULT_SCHEDULE = {"mode": "manual"}

_VALID_MODES = {"manual", "interval_hours", "weekly", "interval_weeks", "monthly"}

# The earliest possible "last run" for comparison purposes when nothing has
# ever run -- any real occurrence/request timestamp compares greater than
# this, so "never run" behaves like "the very first occurrence is already
# due" without needing a None-handling branch at every call site.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_hhmm(value) -> tuple[int, int] | None:
    """Parse an "HH:MM" 24-hour UTC time string. None if not a valid one --
    never guessed at (e.g. "9:5" is rejected, not silently zero-padded)."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    hh, mm = parts
    if not (len(hh) == 2 and len(mm) == 2 and hh.isdigit() and mm.isdigit()):
        return None
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _parse_date(value) -> datetime | None:
    """Parse a "YYYY-MM-DD" date string as a UTC midnight datetime."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _validate_schedule(raw: dict) -> dict:
    """Normalize a decoded `settings/pipeline_schedule` document. Any mode
    whose required fields aren't all present and valid falls all the way
    back to DEFAULT_SCHEDULE (manual) rather than running with a partially-
    guessed cadence -- there's no safe partial default for "when should this
    run," unlike e.g. review_gate's threshold."""
    if not isinstance(raw, dict):
        return dict(DEFAULT_SCHEDULE)
    mode = raw.get("mode")
    if mode not in _VALID_MODES:
        return dict(DEFAULT_SCHEDULE)
    if mode == "manual":
        return {"mode": "manual"}

    if mode == "interval_hours":
        hours = raw.get("interval_hours")
        # math.isfinite rejects NaN/+-Infinity, which Firestore's REST API
        # can legitimately encode for a doubleValue -- decode_value() would
        # otherwise pass one straight through (nan <= 0 and inf <= 0 are both
        # False) into timedelta(hours=...), which raises ValueError/
        # OverflowError outside this function's fail-closed contract.
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours <= 0:
            return dict(DEFAULT_SCHEDULE)
        return {"mode": "interval_hours", "interval_hours": float(hours)}

    if mode == "weekly":
        day = raw.get("weekly_day")
        hhmm = _parse_hhmm(raw.get("weekly_time_utc"))
        if isinstance(day, bool) or not isinstance(day, int) or not (0 <= day <= 6) or hhmm is None:
            return dict(DEFAULT_SCHEDULE)
        return {"mode": "weekly", "weekly_day": day, "weekly_time_utc": hhmm}

    if mode == "interval_weeks":
        weeks = raw.get("interval_weeks")
        hhmm = _parse_hhmm(raw.get("weekly_time_utc"))
        anchor = _parse_date(raw.get("interval_anchor"))
        # Upper-bounded at 520 weeks (10 years) -- a curator/CMS bug
        # submitting an extreme value must fail closed to manual here rather
        # than risk overflowing timedelta(weeks=...) later, unguarded, in
        # _last_interval_weeks_occurrence.
        if (
            isinstance(weeks, bool) or not isinstance(weeks, int) or not (0 < weeks <= 520)
            or hhmm is None or anchor is None
        ):
            return dict(DEFAULT_SCHEDULE)
        return {"mode": "interval_weeks", "interval_weeks": weeks, "weekly_time_utc": hhmm, "interval_anchor": anchor}

    if mode == "monthly":
        day = raw.get("monthly_day")
        hhmm = _parse_hhmm(raw.get("weekly_time_utc"))
        if isinstance(day, bool) or not isinstance(day, int) or not (1 <= day <= 28) or hhmm is None:
            return dict(DEFAULT_SCHEDULE)
        return {"mode": "monthly", "monthly_day": day, "weekly_time_utc": hhmm}

    return dict(DEFAULT_SCHEDULE)  # unreachable given the _VALID_MODES check above


def fetch_pipeline_schedule(
    project_id: str | None = None,
    session: requests.Session | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """Fetch and validate `settings/pipeline_schedule`. Defaults to
    DEFAULT_SCHEDULE (manual -- never auto-triggers) on any failure: no
    project id, network error, missing document, or a document that fails
    _validate_schedule. Manual is the safe default here because it's the
    only mode that can never fire an unwanted, unconfigured pipeline run."""
    project_id = project_id or load_project_id()
    if not project_id:
        print(
            "WARN  no Firebase project id (.firebaserc unreadable) -- "
            "treating pipeline schedule as manual-only",
            file=sys.stderr,
        )
        return dict(DEFAULT_SCHEDULE)

    session = session or requests.Session()
    try:
        doc = fetch_document("settings", "pipeline_schedule", project_id, session, timeout)
    except FIRESTORE_EXCEPTIONS as exc:
        print(f"WARN  could not fetch pipeline schedule ({exc}) -- treating as manual-only", file=sys.stderr)
        return dict(DEFAULT_SCHEDULE)

    if doc is None:
        return dict(DEFAULT_SCHEDULE)

    raw_fields = doc.get("fields", {})
    if not isinstance(raw_fields, dict):
        return dict(DEFAULT_SCHEDULE)
    decoded = {k: decode_value(v) for k, v in raw_fields.items()}
    return _validate_schedule(decoded)


def fetch_run_request(
    project_id: str | None = None,
    session: requests.Session | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> datetime | None:
    """Fetch `settings/pipeline_run_request` and return its `requested_at`
    as a UTC datetime, or None if there's no request, it's malformed, or the
    fetch fails -- degrading to "no pending manual request" is always safe
    (it just means Run Now won't fire this tick, never a spurious trigger)."""
    project_id = project_id or load_project_id()
    if not project_id:
        return None

    session = session or requests.Session()
    try:
        doc = fetch_document("settings", "pipeline_run_request", project_id, session, timeout)
    except FIRESTORE_EXCEPTIONS as exc:
        print(f"WARN  could not fetch pipeline run request ({exc}) -- ignoring", file=sys.stderr)
        return None

    if doc is None:
        return None

    raw_fields = doc.get("fields", {})
    if not isinstance(raw_fields, dict):
        return None
    decoded = {k: decode_value(v) for k, v in raw_fields.items()}
    requested_at = decoded.get("requested_at")
    if not isinstance(requested_at, str):
        return None
    try:
        return datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
    except ValueError:
        return None


def _last_weekly_occurrence(day: int, hhmm: tuple[int, int], now: datetime) -> datetime:
    """Most recent datetime <= now landing on weekday `day` (0=Sunday..
    6=Saturday, matching JS `Date.getUTCDay()` -- the convention the admin
    CMS's day-of-week picker uses) at time `hhmm` UTC."""
    hour, minute = hhmm
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    current_day = (candidate.weekday() + 1) % 7  # Python Monday=0 -> Sunday=0
    days_back = (current_day - day) % 7
    candidate -= timedelta(days=days_back)
    if candidate > now:
        candidate -= timedelta(days=7)
    return candidate


def _last_interval_weeks_occurrence(anchor: datetime, weeks: int, hhmm: tuple[int, int], now: datetime) -> datetime | None:
    """Most recent datetime <= now landing on an anchor + n*weeks boundary,
    at time `hhmm` UTC. None if the anchor itself is still in the future."""
    hour, minute = hhmm
    anchor_at_time = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if anchor_at_time > now:
        return None
    period = timedelta(weeks=weeks)
    elapsed_periods = (now - anchor_at_time) // period
    return anchor_at_time + elapsed_periods * period


def _last_monthly_occurrence(day: int, hhmm: tuple[int, int], now: datetime) -> datetime:
    """Most recent datetime <= now landing on day-of-month `day` (1-28, so
    every month has that day -- no "day 31 doesn't exist in February"
    clamping needed) at time `hhmm` UTC."""
    hour, minute = hhmm
    candidate = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > now:
        year, month = candidate.year, candidate.month
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        candidate = candidate.replace(year=year, month=month)
    return candidate


def compute_is_due(schedule: dict, now: datetime, last_run_at: datetime | None) -> bool:
    """True if `schedule`'s cadence has a due occurrence that hasn't been
    covered by the last run yet. Pure -- no I/O, no Firestore, no GitHub API.

    `last_run_at=None` (nothing has ever run) is treated as "due" for any
    non-manual mode, so a freshly-configured schedule fires on its first
    matching tick rather than waiting a full cycle."""
    mode = schedule.get("mode")
    last_run_at = last_run_at or _EPOCH

    if mode == "manual":
        return False

    if mode == "interval_hours":
        hours = schedule.get("interval_hours")
        # Bounds duplicated from _validate_schedule: this function is public
        # and documented as pure, so it must not trust that every caller
        # routed its input through validation first -- an unbounded/non-
        # finite hours would otherwise raise inside timedelta(hours=...)
        # instead of degrading to "not due" like every other malformed-input
        # branch here.
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours <= 0:
            return False
        return now - last_run_at >= timedelta(hours=hours)

    if mode == "weekly":
        day, hhmm = schedule.get("weekly_day"), schedule.get("weekly_time_utc")
        if not isinstance(day, int) or not isinstance(hhmm, tuple):
            return False
        occurrence = _last_weekly_occurrence(day, hhmm, now)
        return occurrence > last_run_at

    if mode == "interval_weeks":
        weeks, hhmm, anchor = schedule.get("interval_weeks"), schedule.get("weekly_time_utc"), schedule.get("interval_anchor")
        # Same defense-in-depth as interval_hours above: a negative weeks
        # would make _last_interval_weeks_occurrence return a timestamp
        # AFTER now (violating its own "<= now" contract), and an extreme
        # value could overflow timedelta -- both fail closed to "not due"
        # here rather than reaching that helper at all.
        if isinstance(weeks, bool) or not isinstance(weeks, int) or not (0 < weeks <= 520) or not isinstance(hhmm, tuple) or not isinstance(anchor, datetime):
            return False
        occurrence = _last_interval_weeks_occurrence(anchor, weeks, hhmm, now)
        return occurrence is not None and occurrence > last_run_at

    if mode == "monthly":
        day, hhmm = schedule.get("monthly_day"), schedule.get("weekly_time_utc")
        if not isinstance(day, int) or not isinstance(hhmm, tuple):
            return False
        occurrence = _last_monthly_occurrence(day, hhmm, now)
        return occurrence > last_run_at

    return False


def is_run_requested(requested_at: datetime | None, last_dispatch_at: datetime | None) -> bool:
    """True if a curator's "Run Now" request hasn't been satisfied by a
    dispatch yet. Self-healing: there's nothing to clear/acknowledge --
    once a dispatch happens at or after requested_at, this naturally
    returns False on the next tick without any write-back to Firestore."""
    if requested_at is None:
        return False
    if last_dispatch_at is None:
        return True
    return requested_at > last_dispatch_at


# ---------------------------------------------------------------------------
# GitHub Actions integration -- the "tick" side. The CMS only ever writes the
# two Firestore documents above; everything past this point is the
# replaceable execution adapter (see CLAUDE.md Phase S: "the mechanism that
# executes the pipeline should remain replaceable"). A future migration to
# a different execution backend only needs a new adapter reading the same
# two documents -- the schema and the CMS don't change.
# ---------------------------------------------------------------------------

GITHUB_API_BASE = "https://api.github.com"
PIPELINE_WORKFLOW_FILE = "pipeline.yml"

# GitHub Actions run statuses that mean "still going" -- any of these means
# a dispatch must NOT happen, since pipeline.yml's own concurrency group
# would only queue behind it anyway (wasting a redundant run) rather than
# actually preventing the tick from trying.
_ACTIVE_RUN_STATUSES = {"queued", "in_progress", "requested", "waiting", "pending"}

_GITHUB_EXCEPTIONS = (requests.RequestException, ValueError, TypeError, KeyError)


def _github_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def fetch_workflow_runs(
    repo: str,
    token: str,
    session: requests.Session,
    workflow_file: str = PIPELINE_WORKFLOW_FILE,
    per_page: int = 10,
    timeout: int = _DEFAULT_TIMEOUT,
) -> list[dict] | None:
    """Fetch the most recent runs of `workflow_file` via GitHub's REST API.

    Returns None on ANY failure (network error, non-200, malformed JSON) --
    deliberately distinct from an empty list (which means "fetched fine, the
    workflow has simply never run"). Callers MUST treat None as "current
    run state is unknown" and refuse to dispatch, since dispatching blind
    when we can't confirm nothing is already in flight is the one failure
    mode this module exists to prevent (concurrent pipeline runs)."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/actions/workflows/{workflow_file}/runs"
    try:
        resp = session.get(url, headers=_github_headers(token), params={"per_page": per_page}, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except _GITHUB_EXCEPTIONS as exc:
        print(f"WARN  could not fetch workflow runs ({exc}) -- treating run state as unknown", file=sys.stderr)
        return None
    if not isinstance(body, dict):
        return None
    runs = body.get("workflow_runs")
    return runs if isinstance(runs, list) else None


def summarize_runs(runs: list[dict]) -> tuple[datetime | None, bool]:
    """Pure summary of raw GitHub API run dicts: (most recent run's
    created_at, whether any run is currently active). No runs at all ->
    (None, False). A run with a missing/malformed created_at is skipped for
    the timestamp but still counts toward the active check."""
    if not runs:
        return None, False
    active = any(isinstance(r, dict) and r.get("status") in _ACTIVE_RUN_STATUSES for r in runs)
    created_ats = []
    for r in runs:
        if not isinstance(r, dict):
            continue
        created_at = r.get("created_at")
        if not isinstance(created_at, str):
            continue
        try:
            created_ats.append(datetime.fromisoformat(created_at.replace("Z", "+00:00")))
        except ValueError:
            continue
    return (max(created_ats) if created_ats else None), active


def dispatch_workflow(
    repo: str,
    token: str,
    session: requests.Session,
    ref: str = "main",  # matches pipeline.yml's own hardcoded `git push origin main`
    workflow_file: str = PIPELINE_WORKFLOW_FILE,
    timeout: int = _DEFAULT_TIMEOUT,
) -> bool:
    """POST a workflow_dispatch event for `workflow_file`. Returns whether it
    succeeded (True/False) rather than raising -- a failed dispatch should
    fail this tick's run loudly (non-zero exit, see main()) but never crash
    in a way that could look like a partial/ambiguous outcome."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    try:
        resp = session.post(url, headers=_github_headers(token), json={"ref": ref}, timeout=timeout)
        resp.raise_for_status()
        return True
    except _GITHUB_EXCEPTIONS as exc:
        print(f"ERROR could not dispatch pipeline workflow ({exc})", file=sys.stderr)
        return False


def main() -> int:
    """Tick entry point (`python -m pipeline.schedule_gate`), run frequently
    by .github/workflows/tick.yml. Reads the curator's schedule + any
    pending manual run-request from Firestore, checks GitHub for an
    already-active pipeline run, and dispatches pipeline.yml if due and
    nothing is already in flight. Never dispatches on an unknown state."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("ERROR  GITHUB_TOKEN/GITHUB_REPOSITORY not set -- refusing to run outside GitHub Actions", file=sys.stderr)
        return 1

    session = requests.Session()
    schedule = fetch_pipeline_schedule(session=session)
    requested_at = fetch_run_request(session=session)

    runs = fetch_workflow_runs(repo, token, session)
    if runs is None:
        print("WARN  could not determine current pipeline run state -- skipping this tick", file=sys.stderr)
        return 0

    last_run_at, active = summarize_runs(runs)
    if active:
        print("Pipeline already queued/in progress -- skipping this tick")
        return 0

    now = datetime.now(timezone.utc)
    due_by_schedule = compute_is_due(schedule, now, last_run_at)
    due_by_request = is_run_requested(requested_at, last_run_at)
    if not (due_by_schedule or due_by_request):
        print("Not due -- skipping this tick")
        return 0

    reason = "manual run request" if due_by_request else f"cadence ({schedule.get('mode')})"
    print(f"Dispatching pipeline run ({reason})")
    return 0 if dispatch_workflow(repo, token, session) else 1


if __name__ == "__main__":
    sys.exit(main())
