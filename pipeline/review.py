"""Needs-Review queue (Phase Q): read curator review decisions and the
admin-configurable confidence-gate settings from Firestore at publish time.

Same unauthenticated-REST, graceful-degradation contract as
pipeline/overrides.py (see that module's docstring for the full rationale):
no credential is needed because Firestore security rules -- not key secrecy
-- gate writes to an allowlisted curator UID, and any fetch failure here
must degrade to a safe default rather than block or corrupt a publish.

Two collections/documents:

- `review_decisions/{chunkId}` -- a curator's approve/reject call on a
  record the confidence gate (extraction/review_gate.py) flagged, keyed by
  chunk_id + content_hash (the hash of the four reviewable field values the
  curator was actually looking at). stage_5_publish only trusts a decision
  whose stored content_hash matches the record's CURRENT content_hash -- a
  re-scrape that changes any reviewable field produces a different hash, so
  a stale decision doesn't silently keep publishing or dropping a record
  whose content has since changed.
- `settings/review_gate` -- a single admin-configurable document
  ({enabled, threshold}) controlling whether the gate runs at all and where
  its confidence cutoff sits, so curators can tune or disable it without a
  code deploy. Missing/unreadable defaults to DEFAULT_SETTINGS (gate ON at
  the standard 0.8 threshold) -- the fail-safe direction, since silently
  defaulting to "gate off" would let low-confidence data reach the public
  dashboard unreviewed.
"""
from __future__ import annotations

import sys

import requests

from pipeline._firestore import (
    FIRESTORE_EXCEPTIONS,
    decode_value,
    fetch_collection,
    fetch_document,
    load_project_id,
)

_DEFAULT_TIMEOUT = 30

_VALID_DECISIONS = {"approved", "rejected"}

DEFAULT_SETTINGS = {"enabled": True, "threshold": 0.8}


def _decode_decision_document(doc: dict) -> tuple[str, dict] | None:
    """Turn one review_decisions document into (chunk_id, {decision,
    content_hash}). Only decision/content_hash matter to the publish
    pipeline; decided_by/decided_at are audit trail the admin CMS itself
    reads, not consumed here. A document missing either field, or whose
    decision isn't "approved"/"rejected", is skipped (not applied) with a
    WARN -- never guessed at or defaulted."""
    name = doc.get("name")
    if not isinstance(name, str) or not name:
        return None
    chunk_id = name.rsplit("/", 1)[-1]

    raw_fields = doc.get("fields", {})
    if not isinstance(raw_fields, dict):
        return None
    decoded = {k: decode_value(v) for k, v in raw_fields.items()}

    decision = decoded.get("decision")
    content_hash = decoded.get("content_hash")
    # decision comes straight from decode_value() and could be a dict/list
    # (a malformed mapValue/arrayValue write) rather than a scalar -- check
    # it's a str BEFORE the set-membership test, since `x in a_set` requires
    # `hash(x)` and would raise TypeError on an unhashable value, crashing
    # the publish instead of degrading (the exact failure mode this
    # function exists to avoid).
    if not isinstance(decision, str) or decision not in _VALID_DECISIONS or not isinstance(content_hash, str) or not content_hash:
        print(
            f"WARN  review decision {chunk_id!r}: invalid shape "
            f"(decision={decision!r}, content_hash={content_hash!r}) -- ignored",
            file=sys.stderr,
        )
        return None
    return chunk_id, {"decision": decision, "content_hash": content_hash}


def fetch_review_decisions(
    project_id: str | None = None,
    session: requests.Session | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, dict]:
    """Fetch the `review_decisions` collection, returning
    {chunk_id: {"decision": "approved"|"rejected", "content_hash": str}}.

    Returns {} on ANY failure (no project id, network error, non-200,
    malformed JSON) -- a Firestore problem must never block a publish. On
    empty/failure, every gate-flagged record is simply treated as pending
    (no matching decision), which is the safe default: it stays queued for
    review rather than silently publishing or dropping."""
    project_id = project_id or load_project_id()
    if not project_id:
        print(
            "WARN  no Firebase project id (.firebaserc unreadable) -- "
            "treating all flagged records as pending review",
            file=sys.stderr,
        )
        return {}

    session = session or requests.Session()
    try:
        documents = fetch_collection("review_decisions", project_id, session, timeout)
    except FIRESTORE_EXCEPTIONS as exc:
        print(
            f"WARN  could not fetch review decisions ({exc}) -- "
            "treating all flagged records as pending review",
            file=sys.stderr,
        )
        return {}

    decisions: dict[str, dict] = {}
    for doc in documents:
        decoded = _decode_decision_document(doc)
        if decoded is not None:
            chunk_id, info = decoded
            decisions[chunk_id] = info
    return decisions


def fetch_review_settings(
    project_id: str | None = None,
    session: requests.Session | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """Fetch the `settings/review_gate` document, returning
    {"enabled": bool, "threshold": float}.

    Defaults to DEFAULT_SETTINGS (gate ON, threshold 0.8) whenever the
    document is missing, unreadable, or has an invalid/missing field --
    the fail-safe direction, since defaulting to "gate off" would let
    low-confidence data reach the public dashboard unreviewed. A present
    but partially-invalid document (e.g. threshold out of [0, 1]) keeps the
    default for just that field rather than discarding the whole document."""
    project_id = project_id or load_project_id()
    if not project_id:
        print(
            "WARN  no Firebase project id (.firebaserc unreadable) -- "
            "using default review-gate settings",
            file=sys.stderr,
        )
        return dict(DEFAULT_SETTINGS)

    session = session or requests.Session()
    try:
        doc = fetch_document("settings", "review_gate", project_id, session, timeout)
    except FIRESTORE_EXCEPTIONS as exc:
        print(f"WARN  could not fetch review-gate settings ({exc}) -- using defaults", file=sys.stderr)
        return dict(DEFAULT_SETTINGS)

    if doc is None:
        return dict(DEFAULT_SETTINGS)

    raw_fields = doc.get("fields", {})
    if not isinstance(raw_fields, dict):
        return dict(DEFAULT_SETTINGS)
    decoded = {k: decode_value(v) for k, v in raw_fields.items()}

    result = dict(DEFAULT_SETTINGS)

    enabled = decoded.get("enabled")
    if isinstance(enabled, bool):
        result["enabled"] = enabled

    threshold = decoded.get("threshold")
    # Firestore encodes a whole-number float (e.g. the admin app writing
    # 1.0) as integerValue, so it can decode to a Python int here -- bool is
    # an int subclass, so it's excluded to avoid True/False being read as
    # 1.0/0.0.
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) and 0.0 <= threshold <= 1.0:
        result["threshold"] = float(threshold)

    return result
