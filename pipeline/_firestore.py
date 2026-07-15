"""Shared unauthenticated-Firestore-REST-read helpers.

Three modules now read Firestore collections/documents at publish time with
no credential (`pipeline/overrides.py`, and Phase Q's `pipeline/review.py`):
project-id loading, the typed-JSON value decoder, and the paginated-GET
mechanics are identical across all of them, so they live here once.

Every function here either returns data or raises -- it does NOT decide how
a caller should degrade on failure (return {}, use a default, etc.). That
policy (including the exact WARN message) stays in each caller, since the
"a Firestore problem must never block or blank a publish" contract is the
same everywhere but the right fallback value differs per caller.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
_FIREBASERC = REPO_ROOT / ".firebaserc"
FIRESTORE_BASE = "https://firestore.googleapis.com/v1"
DEFAULT_TIMEOUT = 30
# Bounds fetch_collection's pagination loop so a misbehaving server that
# keeps echoing a non-empty nextPageToken can't hang an unattended publish
# forever. Hitting it means something's wrong, so it warns rather than
# silently truncating.
MAX_PAGES = 100

# The exception set every caller here should catch around a fetch_collection/
# fetch_document call: broad on purpose (adds AttributeError/TypeError/
# KeyError beyond the obvious network/JSON errors) because "never raise,
# always degrade" is a hard requirement -- any malformed-shape response must
# degrade, not propagate and crash the publish.
FIRESTORE_EXCEPTIONS = (requests.RequestException, json.JSONDecodeError, ValueError, AttributeError, TypeError, KeyError)


def load_project_id() -> str | None:
    """Project id from .firebaserc (never hardcoded twice -- same source the
    deploy uses). None if unreadable, so callers degrade to no-data."""
    try:
        with _FIREBASERC.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data["projects"]["default"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def decode_value(value: dict):
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
        return [decode_value(v) for v in values]
    if tag == "mapValue":
        fields = inner.get("fields", {}) if isinstance(inner, dict) else {}
        return {k: decode_value(v) for k, v in fields.items()}
    return None


def fetch_collection(
    collection: str,
    project_id: str,
    session: requests.Session,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch every document in `collection` via a paginated, unauthenticated
    Firestore REST GET. Returns raw Firestore document dicts (undecoded) --
    decoding is collection-specific and stays with the caller.

    Raises on network/parse/shape failures (see FIRESTORE_EXCEPTIONS) --
    callers must catch and degrade, never let this propagate into a publish
    failure. The one exception that does NOT raise: hitting MAX_PAGES
    without a terminating empty token (a misbehaving server) returns
    whatever was gathered so far, with a warning, rather than hanging."""
    url = f"{FIRESTORE_BASE}/projects/{project_id}/databases/(default)/documents/{collection}"
    documents: list[dict] = []
    page_token: str | None = None

    for _ in range(MAX_PAGES):
        params = {"pageToken": page_token} if page_token else None
        resp = session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise ValueError(f"Firestore response was not a JSON object: {type(body).__name__}")
        docs = body.get("documents", [])
        if not isinstance(docs, list):
            raise ValueError("Firestore response 'documents' was not a list")
        documents.extend(d for d in docs if isinstance(d, dict))
        page_token = body.get("nextPageToken")
        if not page_token:
            return documents

    print(f"WARN  {collection!r} fetch hit the {MAX_PAGES}-page cap -- returning partial results", file=sys.stderr)
    return documents


def fetch_document(
    collection: str,
    doc_id: str,
    project_id: str,
    session: requests.Session,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict | None:
    """Fetch a single document by id. Returns the raw Firestore document dict
    (undecoded), or None if it doesn't exist (404) or the response isn't a
    valid document shape. Raises on network/parse failures the same way
    fetch_collection does -- callers catch and degrade."""
    url = f"{FIRESTORE_BASE}/projects/{project_id}/databases/(default)/documents/{collection}/{doc_id}"
    resp = session.get(url, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict) or "fields" not in body:
        return None
    return body
