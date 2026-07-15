"""Tests for pipeline/review.py -- the Phase Q Needs-Review queue's Firestore
read side: curator approve/reject decisions and the admin-configurable
confidence-gate settings.

No live network / no live Firestore: mocked the same way test_overrides.py
and test_firestore.py do it.
"""
from __future__ import annotations

import json

import requests

from pipeline.review import (
    DEFAULT_SETTINGS,
    _decode_decision_document,
    fetch_review_decisions,
    fetch_review_settings,
)


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


def _decision_doc(chunk_id, decision=None, content_hash=None, extra_fields=None):
    fields = {}
    if decision is not None:
        fields["decision"] = _fs_string(decision)
    if content_hash is not None:
        fields["content_hash"] = _fs_string(content_hash)
    if extra_fields:
        fields.update(extra_fields)
    return {
        "name": f"projects/test-proj/databases/(default)/documents/review_decisions/{chunk_id}",
        "fields": fields,
    }


def _settings_doc(enabled=None, threshold=None):
    fields = {}
    if enabled is not None:
        fields["enabled"] = {"booleanValue": enabled}
    if threshold is not None:
        fields["threshold"] = {"doubleValue": threshold}
    return {
        "name": "projects/test-proj/databases/(default)/documents/settings/review_gate",
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# _decode_decision_document
# ---------------------------------------------------------------------------

class TestDecodeDecisionDocument:
    def test_decodes_approved(self):
        doc = _decision_doc("giki", decision="approved", content_hash="abc123")
        chunk_id, info = _decode_decision_document(doc)
        assert chunk_id == "giki"
        assert info == {"decision": "approved", "content_hash": "abc123"}

    def test_decodes_rejected(self):
        doc = _decision_doc("giki", decision="rejected", content_hash="abc123")
        _, info = _decode_decision_document(doc)
        assert info["decision"] == "rejected"

    def test_invalid_decision_value_is_skipped(self):
        doc = _decision_doc("giki", decision="maybe", content_hash="abc123")
        assert _decode_decision_document(doc) is None

    def test_missing_decision_is_skipped(self):
        doc = _decision_doc("giki", content_hash="abc123")
        assert _decode_decision_document(doc) is None

    def test_missing_content_hash_is_skipped(self):
        doc = _decision_doc("giki", decision="approved")
        assert _decode_decision_document(doc) is None

    def test_empty_content_hash_is_skipped(self):
        doc = _decision_doc("giki", decision="approved", content_hash="")
        assert _decode_decision_document(doc) is None

    def test_non_string_content_hash_is_skipped(self):
        doc = _decision_doc("giki", decision="approved")
        doc["fields"]["content_hash"] = {"doubleValue": 1.0}
        assert _decode_decision_document(doc) is None

    def test_decision_decoded_as_map_does_not_raise(self):
        # A malformed write (decision stored as a mapValue instead of a
        # scalar) decodes to a dict via decode_value() -- `dict in a_set`
        # would TypeError on an unhashable value if not guarded; must
        # degrade to "skip this document", never crash the fetch.
        doc = _decision_doc("giki", content_hash="abc123")
        doc["fields"]["decision"] = {"mapValue": {"fields": {"x": {"stringValue": "y"}}}}
        assert _decode_decision_document(doc) is None

    def test_decision_decoded_as_array_does_not_raise(self):
        doc = _decision_doc("giki", content_hash="abc123")
        doc["fields"]["decision"] = {"arrayValue": {"values": [{"stringValue": "approved"}]}}
        assert _decode_decision_document(doc) is None

    def test_audit_metadata_is_ignored(self):
        # decided_by/decided_at are present on a real doc but not consumed --
        # only decision/content_hash matter to the publish pipeline.
        doc = _decision_doc(
            "giki", decision="approved", content_hash="abc123",
            extra_fields={"decided_by": _fs_string("uid-123"), "decided_at": {"timestampValue": "2026-07-15T00:00:00Z"}},
        )
        chunk_id, info = _decode_decision_document(doc)
        assert info == {"decision": "approved", "content_hash": "abc123"}

    def test_document_without_name_returns_none(self):
        assert _decode_decision_document({"fields": {}}) is None

    def test_non_string_name_returns_none_not_raises(self):
        assert _decode_decision_document({"name": 12345, "fields": {}}) is None

    def test_non_dict_fields_returns_none(self):
        doc = {"name": "projects/p/databases/(default)/documents/review_decisions/giki", "fields": "not-a-dict"}
        assert _decode_decision_document(doc) is None


# ---------------------------------------------------------------------------
# fetch_review_decisions
# ---------------------------------------------------------------------------

class TestFetchReviewDecisions:
    def test_fetches_and_decodes(self):
        payload = {"documents": [_decision_doc("giki", decision="approved", content_hash="abc123")]}
        session = FakeSession(FakeResponse(payload))

        result = fetch_review_decisions(project_id="test-proj", session=session)

        assert result == {"giki": {"decision": "approved", "content_hash": "abc123"}}

    def test_multiple_documents(self):
        payload = {"documents": [
            _decision_doc("giki", decision="approved", content_hash="hash1"),
            _decision_doc("uet", decision="rejected", content_hash="hash2"),
        ]}
        session = FakeSession(FakeResponse(payload))

        result = fetch_review_decisions(project_id="test-proj", session=session)

        assert set(result) == {"giki", "uet"}
        assert result["uet"]["decision"] == "rejected"

    def test_empty_collection_returns_empty(self):
        session = FakeSession(FakeResponse({"documents": []}))
        assert fetch_review_decisions(project_id="test-proj", session=session) == {}

    def test_follows_pagination(self):
        # fetch_collection (pipeline/_firestore.py) paginates via
        # nextPageToken -- exercise that fetch_review_decisions actually
        # consumes ALL pages, not just the first, the same way
        # test_overrides.py's test_follows_pagination does.
        page1 = {
            "documents": [_decision_doc("giki", decision="approved", content_hash="hash1")],
            "nextPageToken": "tok",
        }
        page2 = {"documents": [_decision_doc("uet", decision="rejected", content_hash="hash2")]}
        session = FakeSession([FakeResponse(page1), FakeResponse(page2)])

        result = fetch_review_decisions(project_id="test-proj", session=session)

        assert set(result) == {"giki", "uet"}
        assert len(session.calls) == 2
        # second call carried the page token
        assert session.calls[1]["params"] == {"pageToken": "tok"}

    def test_pagination_cap_prevents_infinite_loop(self):
        # A server that always echoes a non-empty nextPageToken must not hang
        # an unattended publish -- fetch_collection bounds this at MAX_PAGES
        # (100) and returns partial results with a warning instead.
        payload = {
            "documents": [_decision_doc("giki", decision="approved", content_hash="hash1")],
            "nextPageToken": "always-more",
        }
        session = FakeSession(FakeResponse(payload))  # same response every call
        result = fetch_review_decisions(project_id="test-proj", session=session)

        assert "giki" in result
        assert len(session.calls) <= 100

    def test_network_error_returns_empty_not_raises(self):
        session = FakeSession(requests.ConnectionError("boom"))
        assert fetch_review_decisions(project_id="test-proj", session=session) == {}

    def test_http_error_returns_empty(self):
        session = FakeSession(FakeResponse(status_code=500))
        assert fetch_review_decisions(project_id="test-proj", session=session) == {}

    def test_malformed_json_returns_empty(self):
        session = FakeSession(FakeResponse(json_exc=json.JSONDecodeError("bad", "", 0)))
        assert fetch_review_decisions(project_id="test-proj", session=session) == {}

    def test_no_project_id_returns_empty_without_network(self, monkeypatch):
        monkeypatch.setattr("pipeline.review.load_project_id", lambda: None)
        session = FakeSession(FakeResponse({"documents": []}))

        result = fetch_review_decisions(project_id=None, session=session)

        assert result == {}
        assert session.calls == []

    def test_one_malformed_document_does_not_discard_others(self):
        good = _decision_doc("giki", decision="approved", content_hash="hash1")
        bad = _decision_doc("uet", decision="not-a-real-decision", content_hash="hash2")
        session = FakeSession(FakeResponse({"documents": [good, bad]}))

        result = fetch_review_decisions(project_id="test-proj", session=session)

        assert set(result) == {"giki"}

    def test_unhashable_decision_field_does_not_crash_the_whole_fetch(self):
        # A single malformed document (decision stored as a mapValue) must
        # not TypeError out of the fetch and discard every other decision
        # already gathered alongside it.
        good = _decision_doc("giki", decision="approved", content_hash="hash1")
        bad = _decision_doc("uet", content_hash="hash2")
        bad["fields"]["decision"] = {"mapValue": {"fields": {}}}
        session = FakeSession(FakeResponse({"documents": [good, bad]}))

        result = fetch_review_decisions(project_id="test-proj", session=session)

        assert set(result) == {"giki"}


# ---------------------------------------------------------------------------
# fetch_review_settings
# ---------------------------------------------------------------------------

class TestFetchReviewSettings:
    def test_fetches_and_decodes(self):
        session = FakeSession(FakeResponse(_settings_doc(enabled=False, threshold=0.6)))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result == {"enabled": False, "threshold": 0.6}

    def test_missing_document_returns_defaults(self):
        # fetch_document (pipeline/_firestore.py) returns None on a 404.
        session = FakeSession(FakeResponse(status_code=404))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result == DEFAULT_SETTINGS

    def test_network_error_returns_defaults(self):
        session = FakeSession(requests.ConnectionError("boom"))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result == DEFAULT_SETTINGS

    def test_no_project_id_returns_defaults_without_network(self, monkeypatch):
        monkeypatch.setattr("pipeline.review.load_project_id", lambda: None)
        session = FakeSession(FakeResponse(_settings_doc(enabled=False, threshold=0.6)))

        result = fetch_review_settings(project_id=None, session=session)

        assert result == DEFAULT_SETTINGS
        assert session.calls == []

    def test_partial_document_fills_in_defaults_per_field(self):
        # Only `enabled` present -- threshold keeps the default.
        session = FakeSession(FakeResponse(_settings_doc(enabled=False)))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result == {"enabled": False, "threshold": 0.8}

    def test_threshold_out_of_range_keeps_default(self):
        session = FakeSession(FakeResponse(_settings_doc(threshold=1.5)))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result["threshold"] == 0.8

    def test_negative_threshold_keeps_default(self):
        session = FakeSession(FakeResponse(_settings_doc(threshold=-0.1)))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result["threshold"] == 0.8

    def test_threshold_boundary_values_are_accepted(self):
        session = FakeSession(FakeResponse(_settings_doc(threshold=0.0)))
        assert fetch_review_settings(project_id="test-proj", session=session)["threshold"] == 0.0
        session2 = FakeSession(FakeResponse(_settings_doc(threshold=1.0)))
        assert fetch_review_settings(project_id="test-proj", session=session2)["threshold"] == 1.0

    def test_integer_threshold_is_coerced_to_float(self):
        # Firestore encodes a whole-number float as integerValue.
        doc = {
            "name": "projects/test-proj/databases/(default)/documents/settings/review_gate",
            "fields": {"threshold": {"integerValue": "1"}},
        }
        session = FakeSession(FakeResponse(doc))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result["threshold"] == 1.0
        assert isinstance(result["threshold"], float)

    def test_boolean_threshold_is_rejected_not_coerced(self):
        # bool is an int subclass -- must not be read as 1.0/0.0.
        doc = {
            "name": "projects/test-proj/databases/(default)/documents/settings/review_gate",
            "fields": {"threshold": {"booleanValue": True}},
        }
        session = FakeSession(FakeResponse(doc))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result["threshold"] == 0.8  # default kept, not coerced to 1.0

    def test_non_boolean_enabled_is_rejected_keeps_default(self):
        doc = {
            "name": "projects/test-proj/databases/(default)/documents/settings/review_gate",
            "fields": {"enabled": {"stringValue": "yes"}},
        }
        session = FakeSession(FakeResponse(doc))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result["enabled"] is True  # default kept

    def test_malformed_document_body_returns_defaults(self):
        # fetch_document itself only returns a body when it has a "fields"
        # key -- but guard defensively against a non-dict fields value too.
        doc = {"name": "projects/test-proj/databases/(default)/documents/settings/review_gate", "fields": "not-a-dict"}
        session = FakeSession(FakeResponse(doc))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result == DEFAULT_SETTINGS

    def test_default_settings_constant_is_not_mutated_by_callers(self):
        # fetch_review_settings must return a fresh dict each call, not a
        # shared reference to DEFAULT_SETTINGS that a caller could mutate.
        result = fetch_review_settings(project_id=None, session=FakeSession(FakeResponse({})))
        result["enabled"] = False
        assert DEFAULT_SETTINGS["enabled"] is True

    def test_map_valued_enabled_does_not_raise_and_keeps_default(self):
        # A malformed write (enabled stored as a mapValue) decodes to a dict
        # via decode_value(). The isinstance(enabled, bool) guard never
        # raises on a dict, unlike a `x in a_set` membership test would --
        # confirm it degrades to the default instead of crashing or being
        # truthily coerced.
        doc = {
            "name": "projects/test-proj/databases/(default)/documents/settings/review_gate",
            "fields": {"enabled": {"mapValue": {"fields": {"x": {"booleanValue": True}}}}},
        }
        session = FakeSession(FakeResponse(doc))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result["enabled"] is True  # default kept, not the truthy dict

    def test_array_valued_threshold_does_not_raise_and_keeps_default(self):
        doc = {
            "name": "projects/test-proj/databases/(default)/documents/settings/review_gate",
            "fields": {"threshold": {"arrayValue": {"values": [{"doubleValue": 0.5}]}}},
        }
        session = FakeSession(FakeResponse(doc))
        result = fetch_review_settings(project_id="test-proj", session=session)
        assert result["threshold"] == 0.8  # default kept, list is not (int, float)

    def test_no_failure_path_ever_produces_gate_off(self):
        # The fail-safe direction is {"enabled": True} (gate ON) on any
        # failure -- {"enabled": False} on a failure path would be the UNSAFE
        # direction, since it would let low-confidence data publish
        # unreviewed. Exercise every degrade path and assert none of them
        # ever produces enabled=False.
        # 1. no project id
        import pipeline.review as review_module
        assert review_module.fetch_review_settings(project_id=None, session=FakeSession(FakeResponse({}))) == {"enabled": True, "threshold": 0.8}
        # 2. network error
        assert fetch_review_settings(project_id="test-proj", session=FakeSession(requests.ConnectionError("boom"))) == {"enabled": True, "threshold": 0.8}
        # 3. HTTP error
        assert fetch_review_settings(project_id="test-proj", session=FakeSession(FakeResponse(status_code=500))) == {"enabled": True, "threshold": 0.8}
        # 4. missing document (404)
        assert fetch_review_settings(project_id="test-proj", session=FakeSession(FakeResponse(status_code=404))) == {"enabled": True, "threshold": 0.8}
        # 5. malformed JSON
        assert fetch_review_settings(project_id="test-proj", session=FakeSession(FakeResponse(json_exc=json.JSONDecodeError("bad", "", 0)))) == {"enabled": True, "threshold": 0.8}
        # 6. malformed document body (fields not a dict)
        malformed = {"name": "projects/test-proj/databases/(default)/documents/settings/review_gate", "fields": "not-a-dict"}
        assert fetch_review_settings(project_id="test-proj", session=FakeSession(FakeResponse(malformed))) == {"enabled": True, "threshold": 0.8}
        # 7. document present but enabled field itself malformed (never
        # silently flips to False just because SOME value was present)
        malformed_enabled = {
            "name": "projects/test-proj/databases/(default)/documents/settings/review_gate",
            "fields": {"enabled": {"nullValue": None}},
        }
        assert fetch_review_settings(project_id="test-proj", session=FakeSession(FakeResponse(malformed_enabled)))["enabled"] is True
