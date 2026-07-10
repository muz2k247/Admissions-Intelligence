"""Tests for the Phase D FastAPI backend (dashboard/backend/main.py).

No live network calls. /api/institutions reads the real
config/institutions.yaml (that's the documented behavior of this endpoint
and doesn't touch EXTRACTED_DIR). All other endpoints are pointed at a
tmp_path fixture directory of hand-written record JSON files via
monkeypatch on dashboard.backend.main.EXTRACTED_DIR (main.py imports the
name directly with `from dashboard.backend.config import EXTRACTED_DIR`,
which binds a new name in main's module namespace at import time -- so
patching dashboard.backend.config.EXTRACTED_DIR alone would NOT affect
_load_records(), which looks up the module-global EXTRACTED_DIR in
dashboard.backend.main's own namespace).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import dashboard.backend.main as main


@pytest.fixture()
def client():
    return TestClient(main.app)


def _record(
    chunk_id="chunk-1",
    institution_id="giki",
    campus=None,
    source_url="https://admissions.giki.edu.pk",
    fetched_at="2026-07-01T00:00:00Z",
    degree_level=None,
    constituent_college=None,
    deadline=None,
    fee=None,
    programs=None,
):
    return {
        "institution_id": institution_id,
        "campus": campus,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "chunk_id": chunk_id,
        "degree_level": degree_level or {"value": "Undergraduate", "reason": None},
        "constituent_college": constituent_college or {"value": None, "confidence": None, "note": None},
        "deadline": deadline or {"value": "2026-08-15", "confidence": 0.9, "note": None},
        "fee": fee or {"value": None, "confidence": None, "note": None},
        "programs": programs or {"value": ["BS CS"], "confidence": 0.8, "note": None},
    }


def _write(dir_path, filename, content):
    path = dir_path / filename
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_text(json.dumps(content), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/institutions -- real config/institutions.yaml, not mocked
# ---------------------------------------------------------------------------

class TestInstitutions:
    def test_returns_real_registry_data(self, client):
        resp = client.get("/api/institutions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 16, "expected 16 institutions in the live registry (UET Taxila split into its own entry)"

        for entry in data:
            assert set(entry.keys()) == {"id", "name", "admitting_body", "ug_pg_mixed", "campuses"}
            assert isinstance(entry["campuses"], list)
            assert None not in entry["campuses"]

        ids = {entry["id"] for entry in data}
        assert "giki" in ids

    def test_multi_campus_institution_lists_campuses(self, client):
        resp = client.get("/api/institutions")
        data = resp.json()
        multi = [e for e in data if len(e["campuses"]) > 1]
        assert len(multi) >= 1, "expected at least one multi-campus institution surfaced with >1 campus"


# ---------------------------------------------------------------------------
# /api/records
# ---------------------------------------------------------------------------

class TestRecordsNoFixtureDir:
    def test_missing_extracted_dir_returns_empty_list_not_error(self, client, tmp_path, monkeypatch):
        missing_dir = tmp_path / "does_not_exist"
        monkeypatch.setattr(main, "EXTRACTED_DIR", missing_dir)

        resp = client.get("/api/records")

        assert resp.status_code == 200
        assert resp.json() == []


class TestRecordsWithFixtures:
    @pytest.fixture(autouse=True)
    def _populate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "EXTRACTED_DIR", tmp_path)

        _write(tmp_path, "r1.json", _record(
            chunk_id="giki-1",
            institution_id="giki",
            degree_level={"value": "Undergraduate", "reason": None},
        ))
        _write(tmp_path, "r2.json", _record(
            chunk_id="giki-2",
            institution_id="giki",
            degree_level={"value": "Postgraduate", "reason": None},
        ))
        _write(tmp_path, "r3.json", _record(
            chunk_id="uet-1",
            institution_id="uet",
            campus="Lahore (Main)",
            degree_level={"value": None, "reason": "no-degree-keyword"},
        ))
        self.tmp_path = tmp_path

    def test_no_filters_defaults_to_undergraduate_only(self, client):
        # Project scope is undergrad-only: an absent degree_level param must
        # not mean "show everything" -- Postgraduate and Ambiguous records
        # stay hidden unless explicitly requested.
        resp = client.get("/api/records")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["chunk_id"] == "giki-1"

    def test_filters_by_institution_id(self, client):
        # institution_id alone still defaults degree_level to Undergraduate,
        # so only giki-1 (not the Postgraduate giki-2) matches.
        resp = client.get("/api/records", params={"institution_id": "giki"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["chunk_id"] == "giki-1"

    def test_filters_by_institution_id_no_match(self, client):
        resp = client.get("/api/records", params={"institution_id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filters_by_degree_level_undergraduate(self, client):
        resp = client.get("/api/records", params={"degree_level": "Undergraduate"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["chunk_id"] == "giki-1"

    def test_filters_by_degree_level_postgraduate(self, client):
        resp = client.get("/api/records", params={"degree_level": "Postgraduate"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["chunk_id"] == "giki-2"

    def test_filters_by_degree_level_ambiguous_maps_to_null_value(self, client):
        resp = client.get("/api/records", params={"degree_level": "Ambiguous"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["chunk_id"] == "uet-1"
        assert data[0]["degree_level"]["value"] is None

    def test_combined_institution_and_degree_level_filters(self, client):
        resp = client.get(
            "/api/records",
            params={"institution_id": "giki", "degree_level": "Postgraduate"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["chunk_id"] == "giki-2"

    def test_invalid_degree_level_returns_400(self, client):
        resp = client.get("/api/records", params={"degree_level": "Graduate"})
        assert resp.status_code == 400

    def test_malformed_json_file_is_skipped_not_fatal(self, client, tmp_path):
        _write(tmp_path, "broken.json", "{not valid json,,,")

        resp = client.get("/api/records")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1  # default Undergraduate-only: just giki-1
        assert "broken" not in {r["chunk_id"] for r in data}

    def test_record_missing_required_field_is_skipped_not_fatal(self, client, tmp_path):
        incomplete = _record(chunk_id="incomplete-1")
        del incomplete["chunk_id"]  # from_dict does d["chunk_id"] -> KeyError, must be caught
        _write(tmp_path, "incomplete.json", incomplete)

        resp = client.get("/api/records")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1  # default Undergraduate-only: just giki-1, incomplete one skipped

    def test_record_with_invalid_field_values_is_skipped_not_fatal(self, client, tmp_path):
        # confidence out of [0, 1] range -> Field.__post_init__ raises ValueError
        bad = _record(
            chunk_id="bad-confidence",
            deadline={"value": "2026-08-15", "confidence": 5.0, "note": None},
        )
        _write(tmp_path, "bad.json", bad)

        resp = client.get("/api/records")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1  # default Undergraduate-only: just giki-1
        assert "bad-confidence" not in {r["chunk_id"] for r in data}

    def test_get_record_by_chunk_id_found(self, client):
        resp = client.get("/api/records/giki-2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chunk_id"] == "giki-2"
        assert data["institution_id"] == "giki"

    def test_get_record_by_chunk_id_not_found(self, client):
        resp = client.get("/api/records/does-not-exist")
        assert resp.status_code == 404

    def test_record_preserves_source_url_and_field_level_confidence(self, client):
        # CLAUDE.md hard rules 2 & 4: source_url always present, confidence
        # is per-field not per-record.
        resp = client.get("/api/records/giki-1")
        data = resp.json()
        assert data["source_url"] == "https://admissions.giki.edu.pk"
        assert data["deadline"]["confidence"] == 0.9
        assert data["fee"]["value"] is None
        assert data["fee"]["confidence"] is None

    def test_null_field_never_carries_a_default_or_confidence(self, client):
        # fee was never stated for these fixtures -> must stay null, not a
        # default/backfilled value, and confidence must be None too.
        resp = client.get("/api/records")
        data = resp.json()
        for record in data:
            if record["fee"]["value"] is None:
                assert record["fee"]["confidence"] is None
