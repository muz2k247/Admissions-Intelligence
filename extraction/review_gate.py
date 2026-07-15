"""Confidence gate for the Phase Q Needs-Review queue.

Pure functions only -- no I/O, no Firestore. `pipeline/run_full.py` (stage 5)
and the admin CMS both consume these to decide whether a record auto-
publishes or is withheld for curator review.

The gate deliberately never collapses to a single record-level score
(CLAUDE.md hard rule 2): it flags a record if ANY reviewable field is a
non-null value below the confidence threshold -- a strong deadline never
excuses a weak programs field. A null field never triggers review; hard
rule 1 treats "not stated" as an honest outcome, not a weak guess.
"""
from __future__ import annotations

import hashlib
import json

from extraction.schema import ExtractedRecord

DEFAULT_THRESHOLD = 0.8

# The four curator-overridable Field attributes (matches
# pipeline/overrides.py::_OVERRIDABLE_FIELDS) -- degree_level is a
# classifier decision, not part of the confidence gate.
REVIEW_FIELDS = ("deadline", "programs", "constituent_college", "admissions_open")


def flagged_fields(record: ExtractedRecord, threshold: float = DEFAULT_THRESHOLD) -> list[str]:
    """Names of REVIEW_FIELDS with a non-null value whose confidence is
    below `threshold`. Order matches REVIEW_FIELDS."""
    flagged = []
    for name in REVIEW_FIELDS:
        field = getattr(record, name)
        # confidence is None here only if the Field invariant were somehow
        # violated (value is not None => confidence is not None) -- treated
        # as flag-worthy defensively rather than assumed unreachable.
        if field.value is not None and (field.confidence is None or field.confidence < threshold):
            flagged.append(name)
    return flagged


def needs_review(record: ExtractedRecord, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """True iff `record` has at least one flagged field and should be
    withheld from the public records.json pending curator approval."""
    return bool(flagged_fields(record, threshold))


def content_hash(record: ExtractedRecord) -> str:
    """Sha256 hex digest of the record's four reviewable field values, in a
    canonical (sorted-key, no-whitespace) JSON encoding.

    This is what a curator's approve/reject decision is keyed against
    (alongside chunk_id): if a later re-scrape changes any of these values,
    the hash changes and a prior decision no longer matches, so the record
    re-queues instead of silently trusting a stale approval.

    Must be ported to JS with byte-identical output (dashboard/admin) --
    keep the encoding (sort_keys=True, separators=(",", ":"), ensure_ascii=
    False) in sync with the JS implementation, and see the cross-language
    parity test. ensure_ascii=False matters: JS's JSON.stringify does not
    escape non-ASCII characters, so a program/college name with e.g. Urdu
    script or diacritics would otherwise canonicalize differently on each
    side and silently break hash parity between the pipeline and the admin
    CMS -- exactly the "stale approval slips through" failure this hash
    exists to prevent.

    List order (both `programs` and a multi-entry `deadline`) is significant
    -- this hash does not sort list contents, only dict keys within each
    entry, matching the ordering already used for equality/display elsewhere
    in the pipeline.
    """
    values = [getattr(record, name).value for name in REVIEW_FIELDS]
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
