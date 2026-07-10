"""Tests for pipeline/run_full.py stage_5_sync — the Firestore sync stage.

Before this stage existed, dashboard/backend/firestore_adapter.py's
write_record_to_firestore/delete_collection were defined but never called
anywhere in the pipeline, so a deployed Cloud Run backend pointed at
Firestore would always serve an empty dataset even with correct local
extraction output.

No live network calls: delete_collection/write_record_to_firestore are
monkeypatched on pipeline.run_full's own module namespace (it imports the
names directly, so patching dashboard.backend.firestore_adapter's originals
would not affect stage_5_sync -- same binding caveat documented in
tests/test_dashboard_backend.py for EXTRACTED_DIR).
"""
from __future__ import annotations

import json

import pytest

import pipeline.run_full as run_full


def _write_extracted_record(extracted_dir, filename, chunk_id="giki", **overrides):
    extracted_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "institution_id": "giki",
        "campus": None,
        "source_url": "https://admissions.giki.edu.pk",
        "fetched_at": "2026-07-09T00:00:00Z",
        "chunk_id": chunk_id,
        "degree_level": {"value": "Undergraduate", "reason": None},
        "constituent_college": {"value": None, "confidence": None, "note": None},
        "deadline": {"value": "10 Aug 2026", "confidence": 0.85, "note": None},
        "fee": {"value": None, "confidence": None, "note": None},
        "programs": {"value": None, "confidence": None, "note": None},
    }
    record.update(overrides)
    (extracted_dir / filename).write_text(json.dumps(record), encoding="utf-8")


class TestStage5Sync:
    def test_skips_when_firebase_project_id_not_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
        calls = {"delete": 0, "write": 0}
        monkeypatch.setattr(run_full, "delete_collection", lambda: calls.__setitem__("delete", calls["delete"] + 1) or True)
        monkeypatch.setattr(run_full, "write_record_to_firestore", lambda r: calls.__setitem__("write", calls["write"] + 1) or True)

        rc = run_full.stage_5_sync(tmp_path)

        assert rc == 0
        assert calls == {"delete": 0, "write": 0}

    def test_syncs_all_extracted_records_when_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
        _write_extracted_record(tmp_path, "giki.json", chunk_id="giki")
        _write_extracted_record(tmp_path, "uet.json", chunk_id="uet")

        written_records = []
        monkeypatch.setattr(run_full, "delete_collection", lambda: True)
        monkeypatch.setattr(run_full, "write_record_to_firestore", lambda r: written_records.append(r.chunk_id) or True)

        rc = run_full.stage_5_sync(tmp_path)

        assert rc == 0
        assert sorted(written_records) == ["giki", "uet"]

    def test_delete_failure_aborts_before_any_writes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
        _write_extracted_record(tmp_path, "giki.json")

        write_calls = []
        monkeypatch.setattr(run_full, "delete_collection", lambda: False)
        monkeypatch.setattr(run_full, "write_record_to_firestore", lambda r: write_calls.append(r.chunk_id) or True)

        rc = run_full.stage_5_sync(tmp_path)

        assert rc == 1
        assert write_calls == []

    def test_write_failure_reports_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
        _write_extracted_record(tmp_path, "giki.json")

        monkeypatch.setattr(run_full, "delete_collection", lambda: True)
        monkeypatch.setattr(run_full, "write_record_to_firestore", lambda r: False)

        rc = run_full.stage_5_sync(tmp_path)

        assert rc == 1

    def test_unreadable_extracted_dir_fails_before_touching_firestore(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
        missing_dir = tmp_path / "does_not_exist"

        calls = {"delete": 0}
        monkeypatch.setattr(run_full, "delete_collection", lambda: calls.__setitem__("delete", 1) or True)
        monkeypatch.setattr(run_full, "write_record_to_firestore", lambda r: True)

        rc = run_full.stage_5_sync(missing_dir)

        assert rc == 1
        assert calls["delete"] == 0

    def test_malformed_record_fails_before_touching_firestore(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "broken.json").write_text("{not valid json,,,", encoding="utf-8")

        calls = {"delete": 0}
        monkeypatch.setattr(run_full, "delete_collection", lambda: calls.__setitem__("delete", 1) or True)
        monkeypatch.setattr(run_full, "write_record_to_firestore", lambda r: True)

        rc = run_full.stage_5_sync(tmp_path)

        assert rc == 1
        assert calls["delete"] == 0

    def test_no_extracted_records_still_clears_stale_collection(self, tmp_path, monkeypatch):
        # An empty batch is a legitimate outcome (e.g. all sources failed to
        # scrape this run) -- Firestore should still be cleared so it doesn't
        # keep serving a stale prior batch forever.
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
        tmp_path.mkdir(exist_ok=True)

        calls = {"delete": 0}
        monkeypatch.setattr(run_full, "delete_collection", lambda: calls.__setitem__("delete", 1) or True)
        monkeypatch.setattr(run_full, "write_record_to_firestore", lambda r: True)

        rc = run_full.stage_5_sync(tmp_path)

        assert rc == 0
        assert calls["delete"] == 1
