"""Curator field overrides: read human-verified corrections from Firestore
and merge them into extracted records at publish time (stage 5).

This is the read side of the admin CMS (Phase K). The public dashboard
stays 100% static-file-only -- it never reads Firestore. Instead, this
module fetches the `overrides` collection during stage 5 and bakes verified
values into the published records.json, so a correction shows up on the
next pipeline run, not live.

Why this can run with zero credentials: Firebase web/project config is not
secret by design -- security is enforced by Firestore security rules, not
key secrecy. The `overrides` collection is `allow read: if true` (its
contents become public dashboard data once merged anyway -- no more
sensitive than records.json), while writes are locked to an allowlist of
curator UIDs (see firestore.rules). So an unauthenticated REST GET is the
correct, intended way for the sandboxed pipeline to read it -- the pipeline
holds no secret, matching CLAUDE.md's "the routine can't hold credentials"
constraint.

Graceful degradation: any network/parse failure returns {} rather than
raising, so a Firestore outage never blanks or fails the publish -- it just
publishes the pipeline-extracted values without curator overrides, exactly
as it did before Phase K. This matches the pipeline's philosophy throughout
(stage_1_scrape, stage_4_build).

Stale-override detection (Phase Q): a curator edit also captures `original`,
the pipeline-extracted value the curator was looking at when they made the
correction (dashboard/admin/src/api/overrides.js already writes this on
every save; this module simply started reading it back). If a later re-
scrape produces a fresh value that no longer matches `original`, the source
page changed since the correction was made -- applying the (now stale)
override would silently keep publishing a value the curator never actually
verified against the new content. merge_overrides drops such overrides
instead, letting the fresh extracted value flow through the confidence gate
(extraction/review_gate.py) like any other unreviewed value.
"""
from __future__ import annotations

import dataclasses
import sys

import requests

from extraction.schema import Field, ExtractedRecord
from pipeline._firestore import (
    FIRESTORE_EXCEPTIONS,
    decode_value as _decode_firestore_value,
    fetch_collection,
    load_project_id as _load_project_id,
)

_DEFAULT_TIMEOUT = 30

# Only these record fields are curator-overridable. Matches the four Field-
# typed attributes the field-extractor produces; degree_level (a DegreeLevel,
# not a Field) is deliberately not overridable here -- UG/PG routing stays a
# classifier decision (CLAUDE.md hard rule 3).
_OVERRIDABLE_FIELDS = ("deadline", "programs", "constituent_college", "admissions_open")

# Sentinel distinguishing "this override doc predates Phase Q and never
# captured `original`" from "original was captured and is null" -- the
# former keeps the pre-Phase-Q apply-unconditionally behavior (no stale-
# override migration needed for existing corrections), the latter is a
# real baseline to compare against.
_NO_ORIGINAL_CAPTURED = object()


@dataclasses.dataclass(frozen=True)
class _OverrideEntry:
    field: Field
    original: object = _NO_ORIGINAL_CAPTURED


def _decode_document(doc: dict) -> tuple[str, dict[str, Field]] | None:
    """Turn one Firestore document into (chunk_id, {field_name: Field}).

    The document id (chunk_id) is the last path segment of doc["name"]. Only
    the `fields` map's overridable entries are converted to Field objects;
    audit metadata a curator's edit also stores (verified_by, verified_at)
    is ignored here -- it lives in Firestore as the audit trail, not in the
    published record. `original` is the one piece of audit metadata this
    module does read (see module docstring) -- decoded via
    `_decode_document_with_originals` below, which this function delegates
    to and then strips back down to the plain {field_name: Field} shape for
    backward-compatible callers/tests. A field whose value/confidence
    violates the Field invariant degrades to skipped (not applied) rather
    than raising."""
    decoded = _decode_document_with_originals(doc)
    if decoded is None:
        return None
    chunk_id, entries = decoded
    return chunk_id, {name: entry.field for name, entry in entries.items()}


def _decode_document_with_originals(doc: dict) -> tuple[str, dict[str, "_OverrideEntry"]] | None:
    """Like _decode_document, but also carries each field's `original`
    (the extracted value the curator saw when they made the correction),
    used by merge_overrides to detect a stale override."""
    name = doc.get("name")
    if not isinstance(name, str) or not name:
        return None
    chunk_id = name.rsplit("/", 1)[-1]

    raw_fields = doc.get("fields", {})
    if not isinstance(raw_fields, dict):
        return chunk_id, {}
    decoded = {k: _decode_firestore_value(v) for k, v in raw_fields.items()}
    fields_map = decoded.get("fields")
    if not isinstance(fields_map, dict):
        return chunk_id, {}

    result: dict[str, _OverrideEntry] = {}
    for field_name in _OVERRIDABLE_FIELDS:
        entry = fields_map.get(field_name)
        if not isinstance(entry, dict):
            continue
        # The admin app writes confidence 1.0, but the Firebase Web SDK encodes
        # a whole-number float as Firestore integerValue, so it decodes to a
        # Python int here. Coerce to float so overridden records publish a
        # consistent 1.0 (matching the pipeline's other float confidences),
        # not 1. bool is an int subclass -- exclude it so a stray boolean
        # confidence still fails the Field invariant rather than becoming 1.0.
        confidence = entry.get("confidence")
        if isinstance(confidence, int) and not isinstance(confidence, bool):
            confidence = float(confidence)
        try:
            field = Field(
                value=entry.get("value"),
                confidence=confidence,
                note=entry.get("note"),
            )
        except (ValueError, TypeError) as exc:
            print(
                f"WARN  override {chunk_id!r}.{field_name}: invalid field ({exc}) -- ignored",
                file=sys.stderr,
            )
            continue
        original = entry["original"] if "original" in entry else _NO_ORIGINAL_CAPTURED
        result[field_name] = _OverrideEntry(field=field, original=original)
    return chunk_id, result


def fetch_overrides(
    project_id: str | None = None,
    session: requests.Session | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, dict[str, "_OverrideEntry"]]:
    """Fetch the `overrides` collection via an unauthenticated Firestore REST
    GET, returning {chunk_id: {field_name: _OverrideEntry}}. Access the
    Field via entry.field (NOT entry.value/entry.confidence directly --
    _OverrideEntry is not a Field).

    Returns {} on ANY failure (no project id, network error, non-200,
    malformed JSON) -- a Firestore problem must never block or blank the
    publish. Paginates via nextPageToken (unlikely to matter at ~dozens of
    overrides, but correct). Each field entry carries `original` alongside
    its Field (see module docstring) for merge_overrides' stale-override
    check."""
    project_id = project_id or _load_project_id()
    if not project_id:
        print("WARN  no Firebase project id (.firebaserc unreadable) -- publishing without curator overrides", file=sys.stderr)
        return {}

    session = session or requests.Session()
    overrides: dict[str, dict[str, _OverrideEntry]] = {}

    try:
        documents = fetch_collection("overrides", project_id, session, timeout)
    except FIRESTORE_EXCEPTIONS as exc:
        print(f"WARN  could not fetch curator overrides ({exc}) -- publishing without them", file=sys.stderr)
        return {}

    for doc in documents:
        decoded = _decode_document_with_originals(doc)
        if decoded is not None:
            chunk_id, entries = decoded
            overrides[chunk_id] = entries

    return overrides


def merge_overrides(record: ExtractedRecord, overrides: dict[str, dict[str, "_OverrideEntry"]]) -> ExtractedRecord:
    """Return `record` with any curator-overridden fields replaced (pure --
    never mutates the input). Only fields present in this chunk's override
    entry are touched; everything else, including source_url (hard rule 4)
    and degree_level, is preserved exactly. A chunk with no override entry
    returns unchanged.

    Stale-override detection (Phase Q): an entry whose `original` no longer
    matches this record's FRESH (pre-override) value for that field means
    the source changed since the curator's correction -- that override is
    dropped (a WARN is printed) and the fresh extracted value flows through
    untouched, to be evaluated by the confidence gate like any other
    unreviewed value. Entries with no captured `original` (override docs
    written before this check existed) keep the old apply-unconditionally
    behavior."""
    chunk_overrides = overrides.get(record.chunk_id)
    if not chunk_overrides:
        return record
    replacements: dict[str, Field] = {}
    for name, entry in chunk_overrides.items():
        if name not in _OVERRIDABLE_FIELDS:
            continue
        if entry.original is not _NO_ORIGINAL_CAPTURED:
            fresh_value = getattr(record, name).value
            if entry.original != fresh_value:
                print(
                    f"WARN  override {record.chunk_id!r}.{name}: stale (source changed since "
                    "correction) -- using fresh extracted value instead",
                    file=sys.stderr,
                )
                continue
        replacements[name] = entry.field
    if not replacements:
        return record
    return dataclasses.replace(record, **replacements)
