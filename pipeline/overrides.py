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
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import requests

from extraction.schema import Field, ExtractedRecord

REPO_ROOT = Path(__file__).resolve().parent.parent
_FIREBASERC = REPO_ROOT / ".firebaserc"
_FIRESTORE_BASE = "https://firestore.googleapis.com/v1"
_DEFAULT_TIMEOUT = 30
# Bounds the pagination loop so a misbehaving server that keeps echoing a
# non-empty nextPageToken can't hang the unattended publish forever. At the
# default Firestore page size this covers far more overrides than the ~dozens
# this project will ever have; hitting it means something's wrong, so warn.
_MAX_PAGES = 100

# Only these record fields are curator-overridable. Matches the four Field-
# typed attributes the field-extractor produces; degree_level (a DegreeLevel,
# not a Field) is deliberately not overridable here -- UG/PG routing stays a
# classifier decision (CLAUDE.md hard rule 3).
_OVERRIDABLE_FIELDS = ("deadline", "fee", "programs", "constituent_college")


def _load_project_id() -> str | None:
    """Project id from .firebaserc (never hardcoded twice -- same source the
    deploy uses). None if unreadable, so callers degrade to no-overrides."""
    try:
        with _FIREBASERC.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data["projects"]["default"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def _decode_firestore_value(value: dict):
    """Decode one Firestore REST typed-JSON value into a plain Python value.

    Firestore's REST API wraps every scalar in a type tag
    (e.g. {"stringValue": "x"}, {"doubleValue": 1.0}) and nests objects/
    arrays as mapValue/arrayValue -- this unwraps that recursively into the
    plain shape the rest of the pipeline uses. Unknown/unhandled tags decode
    to None (an honest "couldn't read it", never a guess)."""
    if not isinstance(value, dict) or not value:
        return None
    tag, inner = next(iter(value.items()))
    if tag == "nullValue":
        return None
    if tag == "stringValue":
        return inner
    if tag == "booleanValue":
        return bool(inner)
    if tag == "integerValue":
        # Firestore returns integers as strings in JSON.
        try:
            return int(inner)
        except (TypeError, ValueError):
            return None
    if tag == "doubleValue":
        try:
            return float(inner)
        except (TypeError, ValueError):
            return None
    if tag == "timestampValue":
        return inner  # ISO-8601 string, kept as-is
    if tag == "arrayValue":
        values = inner.get("values", []) if isinstance(inner, dict) else []
        return [_decode_firestore_value(v) for v in values]
    if tag == "mapValue":
        fields = inner.get("fields", {}) if isinstance(inner, dict) else {}
        return {k: _decode_firestore_value(v) for k, v in fields.items()}
    return None


def _decode_document(doc: dict) -> tuple[str, dict[str, Field]] | None:
    """Turn one Firestore document into (chunk_id, {field_name: Field}).

    The document id (chunk_id) is the last path segment of doc["name"]. Only
    the `fields` map's overridable entries are converted to Field objects;
    audit metadata a curator's edit also stores (original, verified_by,
    verified_at) is ignored here -- it lives in Firestore as the audit trail,
    not in the published record. A field whose value/confidence violates the
    Field invariant degrades to skipped (not applied) rather than raising."""
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

    result: dict[str, Field] = {}
    for field_name in _OVERRIDABLE_FIELDS:
        entry = fields_map.get(field_name)
        if not isinstance(entry, dict):
            continue
        try:
            result[field_name] = Field(
                value=entry.get("value"),
                confidence=entry.get("confidence"),
                note=entry.get("note"),
            )
        except (ValueError, TypeError) as exc:
            print(
                f"WARN  override {chunk_id!r}.{field_name}: invalid field ({exc}) -- ignored",
                file=sys.stderr,
            )
    return chunk_id, result


def fetch_overrides(
    project_id: str | None = None,
    session: requests.Session | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, dict[str, Field]]:
    """Fetch the `overrides` collection via an unauthenticated Firestore REST
    GET, returning {chunk_id: {field_name: Field}}.

    Returns {} on ANY failure (no project id, network error, non-200,
    malformed JSON) -- a Firestore problem must never block or blank the
    publish. Paginates via nextPageToken (unlikely to matter at ~dozens of
    overrides, but correct)."""
    project_id = project_id or _load_project_id()
    if not project_id:
        print("WARN  no Firebase project id (.firebaserc unreadable) -- publishing without curator overrides", file=sys.stderr)
        return {}

    session = session or requests.Session()
    url = f"{_FIRESTORE_BASE}/projects/{project_id}/databases/(default)/documents/overrides"
    overrides: dict[str, dict[str, Field]] = {}
    page_token: str | None = None

    # The exception set is deliberately broad (adds AttributeError/TypeError/
    # KeyError beyond the obvious network/JSON errors): "never raise, always
    # return {}" is a hard requirement here (a Firestore problem must never
    # crash or blank the publish), so any malformed-shape response -- e.g. a
    # JSON body that's a list not an object, so body.get() would AttributeError
    # -- must degrade, not propagate.
    try:
        for _ in range(_MAX_PAGES):
            params = {"pageToken": page_token} if page_token else None
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            body = resp.json()
            if not isinstance(body, dict):
                raise ValueError(f"Firestore response was not a JSON object: {type(body).__name__}")
            documents = body.get("documents", [])
            if not isinstance(documents, list):
                raise ValueError("Firestore response 'documents' was not a list")
            for doc in documents:
                if not isinstance(doc, dict):
                    continue
                decoded = _decode_document(doc)
                if decoded is not None:
                    chunk_id, fields = decoded
                    overrides[chunk_id] = fields
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        else:
            # Ran the full _MAX_PAGES without a terminating (empty) token --
            # a misbehaving server. Keep what we gathered rather than hang.
            print(f"WARN  curator-overrides fetch hit the {_MAX_PAGES}-page cap -- returning partial results", file=sys.stderr)
    except (requests.RequestException, json.JSONDecodeError, ValueError, AttributeError, TypeError, KeyError) as exc:
        print(f"WARN  could not fetch curator overrides ({exc}) -- publishing without them", file=sys.stderr)
        return {}

    return overrides


def merge_overrides(record: ExtractedRecord, overrides: dict[str, dict[str, Field]]) -> ExtractedRecord:
    """Return `record` with any curator-overridden fields replaced (pure --
    never mutates the input). Only fields present in this chunk's override
    entry are touched; everything else, including source_url (hard rule 4)
    and degree_level, is preserved exactly. A chunk with no override entry
    returns unchanged."""
    chunk_overrides = overrides.get(record.chunk_id)
    if not chunk_overrides:
        return record
    replacements = {name: field for name, field in chunk_overrides.items() if name in _OVERRIDABLE_FIELDS}
    if not replacements:
        return record
    return dataclasses.replace(record, **replacements)
