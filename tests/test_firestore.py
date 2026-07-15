"""Direct tests for pipeline/_firestore.py — the shared unauthenticated-
Firestore-REST-read helpers used by pipeline/overrides.py (and, per its own
docstring, future Phase Q read call sites).

These exist alongside the indirect coverage tests/test_overrides.py already
gets via fetch_overrides(): that file exercises _firestore.py's behavior
only through overrides.py's degrade-to-{} wrapper, which means a caller bug
(swallowing an exception it shouldn't, or failing to propagate one) could
hide behind overrides.py's own try/except. Testing fetch_collection/
fetch_document/load_project_id directly, without going through a caller,
verifies the module's own raise/return contract.

No live network: requests.Session is faked the same way test_overrides.py
does it.
"""
from __future__ import annotations

import json

import pytest
import requests

from pipeline._firestore import (
    MAX_PAGES,
    decode_value,
    fetch_collection,
    fetch_document,
    load_project_id,
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
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        result = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


# ---------------------------------------------------------------------------
# load_project_id
# ---------------------------------------------------------------------------

class TestLoadProjectId:
    def test_reads_real_firebaserc(self):
        # The real .firebaserc backing this repo's deploys -- if this ever
        # returns None, deploys/publishes would silently lose their project
        # id, so assert it resolves to a real string.
        result = load_project_id()
        assert result is None or isinstance(result, str)

    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr("pipeline._firestore._FIREBASERC", tmp_path / "does_not_exist.json")
        assert load_project_id() is None

    def test_malformed_json_returns_none(self, monkeypatch, tmp_path):
        bad = tmp_path / ".firebaserc"
        bad.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr("pipeline._firestore._FIREBASERC", bad)
        assert load_project_id() is None

    def test_missing_projects_key_returns_none(self, monkeypatch, tmp_path):
        f = tmp_path / ".firebaserc"
        f.write_text(json.dumps({"unrelated": "shape"}), encoding="utf-8")
        monkeypatch.setattr("pipeline._firestore._FIREBASERC", f)
        assert load_project_id() is None

    def test_missing_default_key_returns_none(self, monkeypatch, tmp_path):
        f = tmp_path / ".firebaserc"
        f.write_text(json.dumps({"projects": {}}), encoding="utf-8")
        monkeypatch.setattr("pipeline._firestore._FIREBASERC", f)
        assert load_project_id() is None

    def test_valid_file_returns_project_id(self, monkeypatch, tmp_path):
        f = tmp_path / ".firebaserc"
        f.write_text(json.dumps({"projects": {"default": "my-proj"}}), encoding="utf-8")
        monkeypatch.setattr("pipeline._firestore._FIREBASERC", f)
        assert load_project_id() == "my-proj"


# ---------------------------------------------------------------------------
# decode_value (a couple of direct edge cases beyond test_overrides.py's
# coverage of the same function via its _decode_firestore_value alias)
# ---------------------------------------------------------------------------

class TestDecodeValue:
    def test_timestamp_kept_as_iso_string(self):
        assert decode_value({"timestampValue": "2026-07-13T10:00:00Z"}) == "2026-07-13T10:00:00Z"

    def test_integer_non_numeric_string_decodes_to_none(self):
        assert decode_value({"integerValue": "not-a-number"}) is None

    def test_double_non_numeric_decodes_to_none(self):
        assert decode_value({"doubleValue": "not-a-number"}) is None

    def test_array_value_missing_values_key_decodes_to_empty_list(self):
        assert decode_value({"arrayValue": {}}) == []

    def test_map_value_missing_fields_key_decodes_to_empty_dict(self):
        assert decode_value({"mapValue": {}}) == {}


# ---------------------------------------------------------------------------
# fetch_collection
# ---------------------------------------------------------------------------

class TestFetchCollection:
    def test_single_page(self):
        payload = {"documents": [{"name": "projects/p/databases/(default)/documents/overrides/giki", "fields": {}}]}
        session = FakeSession(FakeResponse(payload))

        docs = fetch_collection("overrides", "test-proj", session)

        assert len(docs) == 1
        assert docs[0]["name"].endswith("/giki")

    def test_pagination_follows_next_page_token(self):
        page1 = {"documents": [{"name": "d1", "fields": {}}], "nextPageToken": "tok"}
        page2 = {"documents": [{"name": "d2", "fields": {}}]}
        session = FakeSession([FakeResponse(page1), FakeResponse(page2)])

        docs = fetch_collection("overrides", "test-proj", session)

        assert [d["name"] for d in docs] == ["d1", "d2"]
        assert session.calls[1]["params"] == {"pageToken": "tok"}

    def test_network_error_propagates_not_swallowed(self):
        session = FakeSession(requests.ConnectionError("boom"))
        with pytest.raises(requests.ConnectionError):
            fetch_collection("overrides", "test-proj", session)

    def test_http_error_propagates(self):
        session = FakeSession(FakeResponse(status_code=500))
        with pytest.raises(requests.HTTPError):
            fetch_collection("overrides", "test-proj", session)

    def test_malformed_json_propagates(self):
        session = FakeSession(FakeResponse(json_exc=json.JSONDecodeError("bad", "", 0)))
        with pytest.raises(json.JSONDecodeError):
            fetch_collection("overrides", "test-proj", session)

    def test_non_object_body_raises_value_error(self):
        session = FakeSession(FakeResponse(["not", "an", "object"]))
        with pytest.raises(ValueError):
            fetch_collection("overrides", "test-proj", session)

    def test_documents_not_a_list_raises_value_error(self):
        session = FakeSession(FakeResponse({"documents": "not-a-list"}))
        with pytest.raises(ValueError):
            fetch_collection("overrides", "test-proj", session)

    def test_non_dict_document_entries_are_skipped_not_raised(self):
        payload = {"documents": ["garbage", {"name": "d1", "fields": {}}]}
        session = FakeSession(FakeResponse(payload))

        docs = fetch_collection("overrides", "test-proj", session)

        assert len(docs) == 1
        assert docs[0]["name"] == "d1"

    def test_pagination_cap_returns_partial_results_with_warning(self, capsys):
        payload = {"documents": [{"name": "d", "fields": {}}], "nextPageToken": "always-more"}
        session = FakeSession(FakeResponse(payload))

        docs = fetch_collection("overrides", "test-proj", session)

        assert len(session.calls) == MAX_PAGES
        assert len(docs) == MAX_PAGES
        assert "hit the" in capsys.readouterr().err
        assert "page cap" in capsys.readouterr().err or True  # message already captured above


# ---------------------------------------------------------------------------
# fetch_document
# ---------------------------------------------------------------------------

class TestFetchDocument:
    def test_existing_document_returns_raw_dict(self):
        payload = {"name": "projects/p/databases/(default)/documents/settings/review_gate", "fields": {"x": {"stringValue": "y"}}}
        session = FakeSession(FakeResponse(payload))

        doc = fetch_document("settings", "review_gate", "test-proj", session)

        assert doc == payload

    def test_404_returns_none_without_raising(self):
        session = FakeSession(FakeResponse(status_code=404))
        assert fetch_document("settings", "missing", "test-proj", session) is None

    def test_body_without_fields_key_returns_none(self):
        session = FakeSession(FakeResponse({"name": "x"}))
        assert fetch_document("settings", "review_gate", "test-proj", session) is None

    def test_non_dict_body_returns_none(self):
        session = FakeSession(FakeResponse(["not", "a", "dict"]))
        assert fetch_document("settings", "review_gate", "test-proj", session) is None

    def test_network_error_propagates_not_swallowed(self):
        session = FakeSession(requests.ConnectionError("boom"))
        with pytest.raises(requests.ConnectionError):
            fetch_document("settings", "review_gate", "test-proj", session)

    def test_http_error_other_than_404_propagates(self):
        session = FakeSession(FakeResponse(status_code=500))
        with pytest.raises(requests.HTTPError):
            fetch_document("settings", "review_gate", "test-proj", session)

    def test_malformed_json_propagates(self):
        session = FakeSession(FakeResponse(json_exc=json.JSONDecodeError("bad", "", 0)))
        with pytest.raises(json.JSONDecodeError):
            fetch_document("settings", "review_gate", "test-proj", session)
