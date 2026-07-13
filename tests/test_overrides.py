"""Tests for pipeline/overrides.py — the curator-override read/merge backbone
of the admin CMS (Phase K).

No live network / no live Firestore: the Firestore REST API is mocked with
FakeSession, matching this project's QA policy (tests never hit a live
service). merge_overrides is a pure function tested directly.
"""
from __future__ import annotations

import json

import requests

from extraction.schema import DegreeLevel, ExtractedRecord, Field, NULL_FIELD
from pipeline.overrides import (
    _decode_document,
    _decode_firestore_value,
    fetch_overrides,
    merge_overrides,
)


# ---------------------------------------------------------------------------
# Fakes for mocking the Firestore REST session without new dependencies
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload=None, status_code=200, raise_exc=None, json_exc=None):
        self._payload = payload
        self.status_code = status_code
        self._raise_exc = raise_exc
        self._json_exc = json_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._payload


class FakeSession:
    """Minimal stand-in for requests.Session. `responses` is a list of
    FakeResponse returned in order (to exercise pagination), or a single
    FakeResponse, or an Exception to raise on .get()."""

    def __init__(self, responses):
        self._responses = responses if isinstance(responses, list) else [responses]
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        result = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def _fs_string(v):
    return {"stringValue": v}


def _fs_field_map(value, confidence, note=None):
    """Build the Firestore typed-JSON for one override field's inner map."""
    fields = {}
    if isinstance(value, list):
        fields["value"] = {"arrayValue": {"values": [{"stringValue": v} for v in value]}}
    elif value is None:
        fields["value"] = {"nullValue": None}
    else:
        fields["value"] = {"stringValue": value}
    fields["confidence"] = {"doubleValue": confidence} if confidence is not None else {"nullValue": None}
    fields["note"] = {"stringValue": note} if note is not None else {"nullValue": None}
    return {"mapValue": {"fields": fields}}


def _fs_document(chunk_id, field_overrides):
    """Build a full Firestore REST document for an overrides/{chunk_id} doc.
    field_overrides: {field_name: (value, confidence, note)}."""
    inner_fields = {name: _fs_field_map(*spec) for name, spec in field_overrides.items()}
    return {
        "name": f"projects/test-proj/databases/(default)/documents/overrides/{chunk_id}",
        "fields": {
            "chunk_id": _fs_string(chunk_id),
            "fields": {"mapValue": {"fields": inner_fields}},
        },
    }


def _record(chunk_id="giki", **overrides):
    base = dict(
        institution_id="giki",
        campus=None,
        source_url="https://giki.edu.pk/admissions/",
        fetched_at="2026-07-09T00:00:00Z",
        chunk_id=chunk_id,
        degree_level=DegreeLevel(value="Undergraduate"),
        constituent_college=NULL_FIELD,
        deadline=Field(value="10 Aug 2026", confidence=0.85),
        fee=NULL_FIELD,
        programs=NULL_FIELD,
    )
    base.update(overrides)
    return ExtractedRecord(**base)


# ---------------------------------------------------------------------------
# _decode_firestore_value
# ---------------------------------------------------------------------------

class TestDecodeFirestoreValue:
    def test_string(self):
        assert _decode_firestore_value({"stringValue": "hi"}) == "hi"

    def test_double(self):
        assert _decode_firestore_value({"doubleValue": 1.0}) == 1.0

    def test_integer_string_is_coerced(self):
        assert _decode_firestore_value({"integerValue": "42"}) == 42

    def test_bool(self):
        assert _decode_firestore_value({"booleanValue": True}) is True

    def test_null(self):
        assert _decode_firestore_value({"nullValue": None}) is None

    def test_array(self):
        v = {"arrayValue": {"values": [{"stringValue": "BS"}, {"stringValue": "BE"}]}}
        assert _decode_firestore_value(v) == ["BS", "BE"]

    def test_nested_map(self):
        v = {"mapValue": {"fields": {"a": {"stringValue": "x"}, "b": {"doubleValue": 0.5}}}}
        assert _decode_firestore_value(v) == {"a": "x", "b": 0.5}

    def test_unknown_tag_decodes_to_none(self):
        assert _decode_firestore_value({"geoPointValue": {"latitude": 1}}) is None

    def test_empty_or_non_dict_decodes_to_none(self):
        assert _decode_firestore_value({}) is None
        assert _decode_firestore_value("not a dict") is None


# ---------------------------------------------------------------------------
# _decode_document
# ---------------------------------------------------------------------------

class TestDecodeDocument:
    def test_extracts_chunk_id_and_fields(self):
        doc = _fs_document("giki", {"deadline": ("2026-08-15", 1.0, "human-verified")})
        chunk_id, fields = _decode_document(doc)

        assert chunk_id == "giki"
        assert fields["deadline"] == Field(value="2026-08-15", confidence=1.0, note="human-verified")
        assert "fee" not in fields  # not present in the doc

    def test_ignores_audit_metadata_keys(self):
        # A real curator edit also stores original/verified_by/verified_at
        # inside the field map -- Field.from_dict-style construction only
        # reads value/confidence/note, so extra keys are harmless.
        doc = _fs_document("giki", {})
        doc["fields"]["fields"]["mapValue"]["fields"]["deadline"] = {
            "mapValue": {"fields": {
                "value": {"stringValue": "2026-08-15"},
                "confidence": {"doubleValue": 1.0},
                "note": {"stringValue": "human-verified"},
                "original": {"mapValue": {"fields": {"value": {"stringValue": "10 Aug 2026"}}}},
                "verified_by": {"stringValue": "uid-123"},
                "verified_at": {"timestampValue": "2026-07-13T10:00:00Z"},
            }}
        }
        chunk_id, fields = _decode_document(doc)
        assert fields["deadline"] == Field(value="2026-08-15", confidence=1.0, note="human-verified")

    def test_invalid_field_invariant_is_skipped_not_raised(self):
        # value present but confidence null violates the Field invariant.
        doc = _fs_document("giki", {"deadline": ("2026-08-15", None, None)})
        chunk_id, fields = _decode_document(doc)
        assert chunk_id == "giki"
        assert "deadline" not in fields  # skipped, not applied, not a crash

    def test_document_without_name_returns_none(self):
        assert _decode_document({"fields": {}}) is None

    def test_non_string_name_returns_none_not_raises(self):
        # A present-but-non-string name must not AttributeError on .rsplit --
        # it degrades to a skipped document, not a crash that would discard
        # every other override already gathered.
        assert _decode_document({"name": 12345, "fields": {}}) is None

    def test_non_dict_fields_returns_empty_not_raises(self):
        doc = {"name": "projects/p/databases/(default)/documents/overrides/giki", "fields": "not-a-dict"}
        chunk_id, fields = _decode_document(doc)
        assert chunk_id == "giki"
        assert fields == {}

    def test_document_without_fields_map_returns_empty(self):
        doc = {"name": "projects/p/databases/(default)/documents/overrides/giki", "fields": {}}
        chunk_id, fields = _decode_document(doc)
        assert chunk_id == "giki"
        assert fields == {}

    def test_programs_list_value_decodes(self):
        doc = _fs_document("giki", {"programs": (["BS CS", "BS EE"], 1.0, "human-verified")})
        _, fields = _decode_document(doc)
        assert fields["programs"].value == ["BS CS", "BS EE"]

    def test_integer_confidence_is_coerced_to_float(self):
        # Firestore encodes the admin app's 1.0 as integerValue -> the decoder
        # must produce a float so overridden records publish 1.0 not 1.
        doc = {
            "name": "projects/p/databases/(default)/documents/overrides/giki",
            "fields": {"fields": {"mapValue": {"fields": {
                "fee": {"mapValue": {"fields": {
                    "value": {"stringValue": "Rs. 3000"},
                    "confidence": {"integerValue": "1"},
                    "note": {"stringValue": "human-verified"},
                }}},
            }}}},
        }
        _, fields = _decode_document(doc)
        assert fields["fee"].confidence == 1.0
        assert isinstance(fields["fee"].confidence, float)


# ---------------------------------------------------------------------------
# fetch_overrides
# ---------------------------------------------------------------------------

class TestFetchOverrides:
    def test_fetches_and_decodes_single_page(self):
        payload = {"documents": [_fs_document("giki", {"fee": ("Rs. 3000", 1.0, "human-verified")})]}
        session = FakeSession(FakeResponse(payload))

        result = fetch_overrides(project_id="test-proj", session=session)

        assert set(result) == {"giki"}
        assert result["giki"]["fee"] == Field(value="Rs. 3000", confidence=1.0, note="human-verified")

    def test_follows_pagination(self):
        page1 = {"documents": [_fs_document("giki", {"fee": ("Rs. 3000", 1.0, None)})], "nextPageToken": "tok"}
        page2 = {"documents": [_fs_document("uet", {"fee": ("Rs. 2000", 1.0, None)})]}
        session = FakeSession([FakeResponse(page1), FakeResponse(page2)])

        result = fetch_overrides(project_id="test-proj", session=session)

        assert set(result) == {"giki", "uet"}
        # second call carried the page token
        assert session.calls[1]["params"] == {"pageToken": "tok"}

    def test_network_error_returns_empty_not_raises(self):
        session = FakeSession(requests.ConnectionError("boom"))
        assert fetch_overrides(project_id="test-proj", session=session) == {}

    def test_http_error_returns_empty(self):
        session = FakeSession(FakeResponse(status_code=500))
        assert fetch_overrides(project_id="test-proj", session=session) == {}

    def test_malformed_json_returns_empty(self):
        session = FakeSession(FakeResponse(json_exc=json.JSONDecodeError("bad", "", 0)))
        assert fetch_overrides(project_id="test-proj", session=session) == {}

    def test_no_project_id_returns_empty_without_network(self, monkeypatch):
        # When no project id can be resolved (.firebaserc unreadable),
        # fetch_overrides must return {} WITHOUT ever calling the session --
        # no wasted/hanging network attempt against a bogus URL.
        monkeypatch.setattr("pipeline.overrides._load_project_id", lambda: None)
        session = FakeSession(FakeResponse({"documents": []}))

        result = fetch_overrides(project_id=None, session=session)

        assert result == {}
        assert session.calls == []  # session never touched

    def test_empty_collection_returns_empty(self):
        session = FakeSession(FakeResponse({"documents": []}))
        assert fetch_overrides(project_id="test-proj", session=session) == {}

    def test_body_is_list_not_object_returns_empty(self):
        # A 200 whose JSON body is a list -> body.get() would AttributeError;
        # must degrade to {}, never crash the publish.
        session = FakeSession(FakeResponse(["not", "an", "object"]))
        assert fetch_overrides(project_id="test-proj", session=session) == {}

    def test_documents_not_a_list_returns_empty(self):
        session = FakeSession(FakeResponse({"documents": {"unexpected": "shape"}}))
        assert fetch_overrides(project_id="test-proj", session=session) == {}

    def test_non_dict_document_entry_is_skipped(self):
        payload = {"documents": ["garbage", _fs_document("giki", {"fee": ("Rs. 3000", 1.0, None)})]}
        session = FakeSession(FakeResponse(payload))
        result = fetch_overrides(project_id="test-proj", session=session)
        assert set(result) == {"giki"}

    def test_one_malformed_document_does_not_discard_the_others(self):
        # A single malformed document (non-string name) must skip only itself,
        # not blow away every valid override already accumulated in the fetch.
        bad_doc = {"name": 999, "fields": {}}
        good_doc = _fs_document("giki", {"fee": ("Rs. 3000", 1.0, None)})
        session = FakeSession(FakeResponse({"documents": [good_doc, bad_doc]}))

        result = fetch_overrides(project_id="test-proj", session=session)

        assert set(result) == {"giki"}
        assert result["giki"]["fee"].value == "Rs. 3000"

    def test_pagination_cap_prevents_infinite_loop(self):
        # A server that always echoes a non-empty nextPageToken must not hang
        # the publish -- the loop is capped and returns what it gathered.
        payload = {"documents": [_fs_document("giki", {"fee": ("Rs. 1", 1.0, None)})], "nextPageToken": "always-more"}
        # Same response every call, forever -- FakeSession clamps to the last
        # (and only) response, so every page looks like "there's another page".
        session = FakeSession(FakeResponse(payload))

        result = fetch_overrides(project_id="test-proj", session=session)

        assert set(result) == {"giki"}  # terminated, didn't hang
        assert len(session.calls) <= 100  # respected the page cap

    def test_malformed_typed_value_shapes_do_not_raise(self):
        # arrayValue/mapValue whose inner isn't the expected dict shape must
        # decode to a safe empty value, not raise inside the fetch loop.
        doc = {
            "name": "projects/p/databases/(default)/documents/overrides/giki",
            "fields": {
                "fields": {"mapValue": {"fields": {
                    "deadline": {"mapValue": {"fields": {
                        "value": {"arrayValue": "not-a-dict"},
                        "confidence": {"doubleValue": 1.0},
                        "note": {"nullValue": None},
                    }}},
                }}},
            },
        }
        session = FakeSession(FakeResponse({"documents": [doc]}))
        result = fetch_overrides(project_id="test-proj", session=session)
        # deadline's value decoded to [] (empty array) -> a valid Field, no crash
        assert "giki" in result


# ---------------------------------------------------------------------------
# merge_overrides
# ---------------------------------------------------------------------------

class TestMergeOverrides:
    def test_applies_overridden_field(self):
        record = _record(chunk_id="giki", fee=NULL_FIELD)
        overrides = {"giki": {"fee": Field(value="Rs. 3000", confidence=1.0, note="human-verified")}}

        merged = merge_overrides(record, overrides)

        assert merged.fee == Field(value="Rs. 3000", confidence=1.0, note="human-verified")

    def test_preserves_untouched_fields_and_source_url(self):
        record = _record(chunk_id="giki")
        overrides = {"giki": {"fee": Field(value="Rs. 3000", confidence=1.0, note="human-verified")}}

        merged = merge_overrides(record, overrides)

        # deadline (not overridden) is unchanged; source_url preserved (hard rule 4)
        assert merged.deadline == record.deadline
        assert merged.source_url == record.source_url
        assert merged.degree_level == record.degree_level

    def test_no_override_entry_returns_record_unchanged(self):
        record = _record(chunk_id="giki")
        merged = merge_overrides(record, {"other_chunk": {"fee": Field(value="x", confidence=1.0)}})
        assert merged is record

    def test_empty_overrides_returns_record_unchanged(self):
        record = _record(chunk_id="giki")
        assert merge_overrides(record, {}) is record

    def test_does_not_mutate_input_record(self):
        record = _record(chunk_id="giki", fee=NULL_FIELD)
        overrides = {"giki": {"fee": Field(value="Rs. 3000", confidence=1.0, note="human-verified")}}

        merge_overrides(record, overrides)

        # original record object is untouched (dataclasses.replace returns new)
        assert record.fee == NULL_FIELD

    def test_ignores_non_overridable_field_names(self):
        # A stray key that isn't one of the four overridable Field attributes
        # must never reach dataclasses.replace (which would raise).
        record = _record(chunk_id="giki")
        overrides = {"giki": {"degree_level": Field(value="x", confidence=1.0), "fee": Field(value="Rs. 1", confidence=1.0)}}

        merged = merge_overrides(record, overrides)

        assert merged.fee.value == "Rs. 1"
        assert merged.degree_level == record.degree_level  # untouched
