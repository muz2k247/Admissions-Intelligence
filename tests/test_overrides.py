"""Tests for pipeline/overrides.py — the curator-override read/merge backbone
of the admin CMS (Phase K), extended in Phase Q with stale-override
detection via the `original` field.

No live network / no live Firestore: the Firestore REST API is mocked with
FakeSession, matching this project's QA policy (tests never hit a live
service). merge_overrides is a pure function tested directly.
"""
from __future__ import annotations

import json

import requests

from extraction.schema import DegreeLevel, ExtractedRecord, Field, NULL_FIELD
from pipeline.overrides import (
    _NO_ORIGINAL_CAPTURED,
    _OverrideEntry,
    _decode_document,
    _decode_document_with_originals,
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


def _fs_typed(value):
    """Firestore typed-JSON for a plain Python value (str, list[str], or None)."""
    if value is None:
        return {"nullValue": None}
    if isinstance(value, list):
        return {"arrayValue": {"values": [{"stringValue": v} for v in value]}}
    return {"stringValue": value}


def _fs_field_map(value, confidence, note=None, original=_NO_ORIGINAL_CAPTURED):
    """Build the Firestore typed-JSON for one override field's inner map.
    `original` defaults to the sentinel meaning "not captured" (pre-Phase-Q
    doc) -- pass an explicit value (including None) to simulate a Phase-Q
    write that did capture it."""
    fields = {"value": _fs_typed(value)}
    fields["confidence"] = {"doubleValue": confidence} if confidence is not None else {"nullValue": None}
    fields["note"] = {"stringValue": note} if note is not None else {"nullValue": None}
    if original is not _NO_ORIGINAL_CAPTURED:
        fields["original"] = _fs_typed(original)
    return {"mapValue": {"fields": fields}}


def _fs_document(chunk_id, field_overrides):
    """Build a full Firestore REST document for an overrides/{chunk_id} doc.
    field_overrides: {field_name: (value, confidence, note)} or
    {field_name: (value, confidence, note, original)}."""
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
        programs=NULL_FIELD,
    )
    base.update(overrides)
    return ExtractedRecord(**base)


def _entry(value, confidence=1.0, note="human-verified", original=_NO_ORIGINAL_CAPTURED):
    """Build an _OverrideEntry the way merge_overrides expects it, mirroring
    what fetch_overrides would have decoded."""
    return _OverrideEntry(field=Field(value=value, confidence=confidence, note=note), original=original)


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
# _decode_document / _decode_document_with_originals
# ---------------------------------------------------------------------------

class TestDecodeDocument:
    def test_extracts_chunk_id_and_fields(self):
        doc = _fs_document("giki", {"deadline": ("2026-08-15", 1.0, "human-verified")})
        chunk_id, fields = _decode_document(doc)

        assert chunk_id == "giki"
        assert fields["deadline"] == Field(value="2026-08-15", confidence=1.0, note="human-verified")
        assert "programs" not in fields  # not present in the doc

    def test_ignores_audit_metadata_keys_other_than_original(self):
        # A real curator edit also stores verified_by/verified_at inside the
        # field map -- _decode_document only reads value/confidence/note (and
        # _decode_document_with_originals additionally reads `original`), so
        # verified_by/verified_at are harmless extras.
        doc = _fs_document("giki", {})
        doc["fields"]["fields"]["mapValue"]["fields"]["deadline"] = {
            "mapValue": {"fields": {
                "value": {"stringValue": "2026-08-15"},
                "confidence": {"doubleValue": 1.0},
                "note": {"stringValue": "human-verified"},
                "original": {"stringValue": "10 Aug 2026"},
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
                "deadline": {"mapValue": {"fields": {
                    "value": {"stringValue": "2026-08-15"},
                    "confidence": {"integerValue": "1"},
                    "note": {"stringValue": "human-verified"},
                }}},
            }}}},
        }
        _, fields = _decode_document(doc)
        assert fields["deadline"].confidence == 1.0
        assert isinstance(fields["deadline"].confidence, float)


class TestDecodeDocumentWithOriginals:
    def test_original_is_captured_when_present(self):
        doc = _fs_document("giki", {"deadline": ("2026-08-15", 1.0, "human-verified", "10 Aug 2026")})
        chunk_id, entries = _decode_document_with_originals(doc)

        assert chunk_id == "giki"
        assert entries["deadline"].field == Field(value="2026-08-15", confidence=1.0, note="human-verified")
        assert entries["deadline"].original == "10 Aug 2026"

    def test_original_null_is_a_real_captured_baseline_not_the_sentinel(self):
        # original=None (the field was genuinely null when first corrected)
        # must be distinguished from "original never captured at all".
        doc = _fs_document("giki", {"deadline": ("2026-08-15", 1.0, "human-verified", None)})
        _, entries = _decode_document_with_originals(doc)
        assert entries["deadline"].original is None
        assert entries["deadline"].original is not _NO_ORIGINAL_CAPTURED

    def test_missing_original_key_decodes_to_sentinel(self):
        # A pre-Phase-Q override doc never wrote `original` at all.
        doc = _fs_document("giki", {"deadline": ("2026-08-15", 1.0, "human-verified")})
        _, entries = _decode_document_with_originals(doc)
        assert entries["deadline"].original is _NO_ORIGINAL_CAPTURED

    def test_programs_list_original_decodes(self):
        doc = _fs_document("giki", {"programs": (["BS CS"], 1.0, "human-verified", ["BS EE"])})
        _, entries = _decode_document_with_originals(doc)
        assert entries["programs"].original == ["BS EE"]

    def test_decode_document_strips_originals_back_to_plain_fields(self):
        # _decode_document is still the plain-Field view used by callers/
        # tests that don't care about staleness.
        doc = _fs_document("giki", {"deadline": ("2026-08-15", 1.0, "human-verified", "10 Aug 2026")})
        chunk_id, fields = _decode_document(doc)
        assert fields == {"deadline": Field(value="2026-08-15", confidence=1.0, note="human-verified")}


# ---------------------------------------------------------------------------
# fetch_overrides
# ---------------------------------------------------------------------------

class TestFetchOverrides:
    def test_fetches_and_decodes_single_page(self):
        payload = {"documents": [_fs_document("giki", {"deadline": ("2026-08-15", 1.0, "human-verified")})]}
        session = FakeSession(FakeResponse(payload))

        result = fetch_overrides(project_id="test-proj", session=session)

        assert set(result) == {"giki"}
        assert result["giki"]["deadline"].field == Field(value="2026-08-15", confidence=1.0, note="human-verified")

    def test_follows_pagination(self):
        page1 = {"documents": [_fs_document("giki", {"deadline": ("2026-08-15", 1.0, None)})], "nextPageToken": "tok"}
        page2 = {"documents": [_fs_document("uet", {"deadline": ("2026-09-01", 1.0, None)})]}
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
        payload = {"documents": ["garbage", _fs_document("giki", {"deadline": ("2026-08-15", 1.0, None)})]}
        session = FakeSession(FakeResponse(payload))
        result = fetch_overrides(project_id="test-proj", session=session)
        assert set(result) == {"giki"}

    def test_one_malformed_document_does_not_discard_the_others(self):
        # A single malformed document (non-string name) must skip only itself,
        # not blow away every valid override already accumulated in the fetch.
        bad_doc = {"name": 999, "fields": {}}
        good_doc = _fs_document("giki", {"deadline": ("2026-08-15", 1.0, None)})
        session = FakeSession(FakeResponse({"documents": [good_doc, bad_doc]}))

        result = fetch_overrides(project_id="test-proj", session=session)

        assert set(result) == {"giki"}
        assert result["giki"]["deadline"].field.value == "2026-08-15"

    def test_pagination_cap_prevents_infinite_loop(self):
        # A server that always echoes a non-empty nextPageToken must not hang
        # the publish -- the loop is capped and returns what it gathered.
        payload = {"documents": [_fs_document("giki", {"deadline": ("2026-08-15", 1.0, None)})], "nextPageToken": "always-more"}
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

    def test_original_round_trips_through_fetch(self):
        payload = {"documents": [_fs_document("giki", {"deadline": ("2026-08-15", 1.0, "human-verified", "10 Aug 2026")})]}
        session = FakeSession(FakeResponse(payload))

        result = fetch_overrides(project_id="test-proj", session=session)

        assert result["giki"]["deadline"].original == "10 Aug 2026"


# ---------------------------------------------------------------------------
# merge_overrides
# ---------------------------------------------------------------------------

class TestMergeOverrides:
    def test_applies_overridden_field(self):
        record = _record(chunk_id="giki", deadline=NULL_FIELD)
        overrides = {"giki": {"deadline": _entry("2026-08-15")}}

        merged = merge_overrides(record, overrides)

        assert merged.deadline == Field(value="2026-08-15", confidence=1.0, note="human-verified")

    def test_preserves_untouched_fields_and_source_url(self):
        record = _record(chunk_id="giki")
        overrides = {"giki": {"programs": _entry(["BS CS"])}}

        merged = merge_overrides(record, overrides)

        # deadline (not overridden) is unchanged; source_url preserved (hard rule 4)
        assert merged.deadline == record.deadline
        assert merged.source_url == record.source_url
        assert merged.degree_level == record.degree_level

    def test_no_override_entry_returns_record_unchanged(self):
        record = _record(chunk_id="giki")
        merged = merge_overrides(record, {"other_chunk": {"deadline": _entry("x")}})
        assert merged is record

    def test_empty_overrides_returns_record_unchanged(self):
        record = _record(chunk_id="giki")
        assert merge_overrides(record, {}) is record

    def test_does_not_mutate_input_record(self):
        record = _record(chunk_id="giki", deadline=NULL_FIELD)
        overrides = {"giki": {"deadline": _entry("2026-08-15")}}

        merge_overrides(record, overrides)

        # original record object is untouched (dataclasses.replace returns new)
        assert record.deadline == NULL_FIELD

    def test_ignores_non_overridable_field_names(self):
        # A stray key that isn't one of the four overridable Field attributes
        # must never reach dataclasses.replace (which would raise).
        record = _record(chunk_id="giki")
        overrides = {"giki": {"degree_level": _entry("x"), "programs": _entry(["BS CS"])}}

        merged = merge_overrides(record, overrides)

        assert merged.programs.value == ["BS CS"]
        assert merged.degree_level == record.degree_level  # untouched

    def test_admissions_open_override_replaces_field_leaves_rest_untouched(self):
        record = _record(
            chunk_id="giki",
            admissions_open=Field(value="Closed", confidence=0.8),
        )
        overrides = {"giki": {"admissions_open": _entry("Open")}}

        merged = merge_overrides(record, overrides)

        assert merged.admissions_open == Field(value="Open", confidence=1.0, note="human-verified")
        # everything else on the record stays untouched
        assert merged.source_url == record.source_url
        assert merged.institution_id == record.institution_id
        assert merged.campus == record.campus
        assert merged.fetched_at == record.fetched_at
        assert merged.chunk_id == record.chunk_id
        assert merged.degree_level == record.degree_level
        assert merged.constituent_college == record.constituent_college
        assert merged.deadline == record.deadline
        assert merged.programs == record.programs

    def test_admissions_open_absent_from_chunk_override_is_unaffected(self):
        # Only deadline is overridden for this chunk -- admissions_open must
        # be left completely alone, confirming the "only fields present in
        # this chunk's override entry are touched" behavior still holds now
        # that there's a 4th overridable field.
        record = _record(
            chunk_id="giki",
            admissions_open=Field(value="Open", confidence=0.9),
        )
        overrides = {"giki": {"deadline": _entry("2026-08-15")}}

        merged = merge_overrides(record, overrides)

        assert merged.admissions_open == record.admissions_open
        assert merged.deadline == Field(value="2026-08-15", confidence=1.0, note="human-verified")


class TestMergeOverridesStaleDetection:
    def test_matching_original_applies_the_override(self):
        record = _record(chunk_id="giki", deadline=Field(value="10 Aug 2026", confidence=0.85))
        overrides = {"giki": {"deadline": _entry("2026-08-15", original="10 Aug 2026")}}

        merged = merge_overrides(record, overrides)

        assert merged.deadline == Field(value="2026-08-15", confidence=1.0, note="human-verified")

    def test_mismatched_original_drops_the_override_uses_fresh_value(self, capsys):
        # The institution genuinely changed its deadline after a curator
        # corrected the old (wrong) one -- the stale correction must not
        # keep publishing over the real, freshly-scraped value.
        fresh = Field(value="2026-10-01", confidence=0.9)
        record = _record(chunk_id="giki", deadline=fresh)
        overrides = {"giki": {"deadline": _entry("2026-08-15", original="10 Aug 2026")}}

        merged = merge_overrides(record, overrides)

        assert merged.deadline == fresh
        assert "stale" in capsys.readouterr().err.lower()

    def test_no_captured_original_keeps_apply_unconditionally_behavior(self):
        # Backward compatibility: an override doc written before Phase Q has
        # no `original` at all -- must still apply, exactly like before.
        record = _record(chunk_id="giki", deadline=Field(value="totally different", confidence=0.9))
        overrides = {"giki": {"deadline": _entry("2026-08-15", original=_NO_ORIGINAL_CAPTURED)}}

        merged = merge_overrides(record, overrides)

        assert merged.deadline == Field(value="2026-08-15", confidence=1.0, note="human-verified")

    def test_original_null_matches_a_currently_null_fresh_field(self):
        # original=None means the field was genuinely null when first
        # corrected -- if it's still null now, that's not staleness.
        record = _record(chunk_id="giki", constituent_college=NULL_FIELD)
        overrides = {"giki": {"constituent_college": _entry("Allied", original=None)}}

        merged = merge_overrides(record, overrides)

        assert merged.constituent_college == Field(value="Allied", confidence=1.0, note="human-verified")

    def test_original_null_but_fresh_now_has_a_value_is_stale(self):
        # The field was null when corrected, but a later scrape now finds a
        # real value -- the override predates that discovery and is stale.
        record = _record(chunk_id="giki", constituent_college=Field(value="King Edward Medical University", confidence=0.9))
        overrides = {"giki": {"constituent_college": _entry("Allied", original=None)}}

        merged = merge_overrides(record, overrides)

        assert merged.constituent_college == Field(value="King Edward Medical University", confidence=0.9)

    def test_programs_list_original_comparison(self):
        record = _record(chunk_id="giki", programs=Field(value=["BS CS", "BS EE"], confidence=0.9))
        overrides = {"giki": {"programs": _entry(["BS CS"], original=["BS CS", "BS EE"])}}

        merged = merge_overrides(record, overrides)

        assert merged.programs == Field(value=["BS CS"], confidence=1.0, note="human-verified")

    def test_stale_and_fresh_overrides_on_the_same_chunk_are_independent(self):
        # A strong deadline override doesn't excuse dropping a stale programs
        # override, and vice versa -- each field's staleness is independent.
        record = _record(
            chunk_id="giki",
            deadline=Field(value="10 Aug 2026", confidence=0.85),  # matches original -> applies
            programs=Field(value=["BE EE"], confidence=0.9),  # does not match original -> stale
        )
        overrides = {
            "giki": {
                "deadline": _entry("2026-08-15", original="10 Aug 2026"),
                "programs": _entry(["BS CS"], original=["BS CS", "BS EE"]),
            }
        }

        merged = merge_overrides(record, overrides)

        assert merged.deadline == Field(value="2026-08-15", confidence=1.0, note="human-verified")
        assert merged.programs == Field(value=["BE EE"], confidence=0.9)  # fresh value, override dropped

    def test_all_overrides_stale_returns_record_with_only_fresh_values(self):
        record = _record(chunk_id="giki", deadline=Field(value="fresh value", confidence=0.9))
        overrides = {"giki": {"deadline": _entry("stale corrected value", original="old original")}}

        merged = merge_overrides(record, overrides)

        assert merged.deadline == Field(value="fresh value", confidence=0.9)


# ---------------------------------------------------------------------------
# admissions_open coverage in _decode_document
# ---------------------------------------------------------------------------

class TestDecodeDocumentAdmissionsOpen:
    def test_admissions_open_field_decodes(self):
        doc = _fs_document("giki", {"admissions_open": ("Open", 0.95, "regex-fallback")})
        chunk_id, fields = _decode_document(doc)

        assert chunk_id == "giki"
        assert fields["admissions_open"] == Field(value="Open", confidence=0.95, note="regex-fallback")

    def test_admissions_open_absent_from_document_is_not_in_result(self):
        doc = _fs_document("giki", {"deadline": ("2026-08-15", 1.0, None)})
        _, fields = _decode_document(doc)
        assert "admissions_open" not in fields
