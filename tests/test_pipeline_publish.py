"""Tests for pipeline/run_full.py stage_5_publish — the static-data publish
stage that replaced Firestore sync (Phase E: drop Cloud Run + Firestore,
dashboard fetches dashboard/frontend/public/data/*.json directly).

No live network calls, no live Firestore. institutions.json is checked
against the real config/institutions.yaml registry (same convention already
used by tests/test_scraper.py::TestConfig and the old
tests/test_dashboard_backend.py for /api/institutions) since
_institutions_payload() always reads the live registry — there's no
per-call override to inject a fixture registry, and the registry's shape is
itself a stable, versioned fact worth asserting against directly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import pipeline.run_full as run_full
from scraper.config import DEFAULT_CONFIG_PATH, Institution, Source, load_institutions


def _write_extracted_record(extracted_dir, filename, chunk_id="giki", **overrides):
    extracted_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "institution_id": "giki",
        "campus": None,
        "source_url": "https://giki.edu.pk/admissions/admissions-undergraduates/",
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


class TestStage5Publish:
    def test_unreadable_extracted_dir_fails_before_publishing(self, tmp_path):
        missing_dir = tmp_path / "does_not_exist"
        publish_dir = tmp_path / "publish"

        rc = run_full.stage_5_publish(missing_dir, publish_dir)

        assert rc == 1
        assert not publish_dir.exists()

    def test_malformed_record_fails_before_publishing(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        extracted_dir.mkdir()
        (extracted_dir / "broken.json").write_text("{not valid json,,,", encoding="utf-8")

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 1
        assert not (publish_dir / "records.json").exists()
        assert not (publish_dir / "institutions.json").exists()

    def test_publishes_all_extracted_records_to_records_json(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", chunk_id="giki")
        _write_extracted_record(extracted_dir, "uet.json", chunk_id="uet", institution_id="uet")

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert sorted(r["chunk_id"] for r in published) == ["giki", "uet"]

    def test_record_preserves_source_url_and_field_level_confidence(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json")

        run_full.stage_5_publish(extracted_dir, publish_dir)

        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        record = published[0]
        assert record["source_url"] == "https://giki.edu.pk/admissions/admissions-undergraduates/"
        assert record["deadline"] == {"value": "10 Aug 2026", "confidence": 0.85, "note": None}

    def test_null_field_never_carries_a_default_or_confidence(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json")

        run_full.stage_5_publish(extracted_dir, publish_dir)

        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        fee = published[0]["fee"]
        assert fee["value"] is None
        assert fee["confidence"] is None

    def test_zero_extracted_records_refuses_to_publish(self, tmp_path):
        # A zero-record extracted_dir is refused, not published as an empty
        # records.json -- matching stage_2_chunk/stage_4_build's existing
        # "produced 0 -> fail" convention. This runs unattended on a cron
        # schedule; a wrong/empty --extracted path must not silently blank
        # out previously-published live data.
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        extracted_dir.mkdir()

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 1
        assert not (publish_dir / "records.json").exists()

    def test_writes_atomically_leaving_no_temp_files_behind(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json")

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        names = sorted(p.name for p in publish_dir.iterdir())
        assert names == ["institutions.json", "records.json"]

    def test_institutions_json_matches_real_registry_shape(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json")

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "institutions.json").read_text(encoding="utf-8"))
        real_institutions = load_institutions(DEFAULT_CONFIG_PATH)
        assert len(published) == len(real_institutions)
        giki = next(i for i in published if i["id"] == "giki")
        assert giki["name"]
        assert giki["campuses"] == []  # single-URL institution, no campus split

    def test_multi_campus_institution_lists_campuses(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json")

        run_full.stage_5_publish(extracted_dir, publish_dir)

        published = json.loads((publish_dir / "institutions.json").read_text(encoding="utf-8"))
        uet = next(i for i in published if i["id"] == "uet")
        assert len(uet["campuses"]) >= 1


class TestStage5PublishGaps:
    """Additional coverage for scenarios not exercised by TestStage5Publish:
    a config-read failure occurring after records are already loaded, repeat
    runs (overwrite vs. append/mix), publish_dir creation/reuse behavior, and
    _institutions_payload() edge cases (zero-source institutions, campus
    filtering/ordering). Also probes a write-failure window between the two
    _write_json_files_atomic() call that TestStage5Publish does not cover.

    Binding note: pipeline/run_full.py does `from scraper.config import
    load_institutions`, so `_institutions_payload()` resolves the name via
    `pipeline.run_full`'s own module globals at call time. Patching
    `scraper.config.load_institutions` directly would NOT affect
    `stage_5_publish` -- tests below patch `run_full.load_institutions`
    instead, matching the documented caveat in the (now-removed)
    tests/test_pipeline_sync.py for the equivalent Firestore-stage functions.
    """

    # -- 1. institutions_payload() read failure after records are loaded ----

    def test_institutions_payload_failure_leaves_no_output_files_on_fresh_publish_dir(
        self, tmp_path, monkeypatch
    ):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json")

        def _raise():
            raise OSError("config/institutions.yaml temporarily unreadable")

        monkeypatch.setattr(run_full, "load_institutions", _raise)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 1
        # publish_dir.mkdir() happens after the institutions_payload() call,
        # so a failure here must never create the directory or either file.
        assert not publish_dir.exists()

    def test_institutions_payload_failure_leaves_previously_published_files_untouched(
        self, tmp_path, monkeypatch
    ):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", chunk_id="run1-good")

        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        prior_records = (publish_dir / "records.json").read_text(encoding="utf-8")
        prior_institutions = (publish_dir / "institutions.json").read_text(encoding="utf-8")

        # Simulate a second pipeline run with new extracted data, but the
        # registry read fails this time.
        extracted_dir2 = tmp_path / "extracted2"
        _write_extracted_record(extracted_dir2, "giki.json", chunk_id="run2-would-be-published")

        def _raise():
            raise OSError("config/institutions.yaml moved mid-run")

        monkeypatch.setattr(run_full, "load_institutions", _raise)

        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 1
        # The live (previously-published) artifacts must be exactly as they
        # were -- a read failure on the second run must not touch them at all,
        # let alone leave a records.json with no matching institutions.json.
        assert (publish_dir / "records.json").read_text(encoding="utf-8") == prior_records
        assert (publish_dir / "institutions.json").read_text(encoding="utf-8") == prior_institutions

    # -- 2. consecutive runs overwrite, not append/mix -----------------------

    def test_second_successful_run_replaces_first_runs_records_entirely(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", chunk_id="run1-record")

        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        first_published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in first_published] == ["run1-record"]

        # Second run reads from a different extracted_dir (simulating a fresh
        # pipeline run where the prior chunk no longer exists upstream).
        extracted_dir2 = tmp_path / "extracted_run2"
        _write_extracted_record(extracted_dir2, "uet.json", chunk_id="run2-record", institution_id="uet")

        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)
        assert rc2 == 0

        second_published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in second_published] == ["run2-record"]

    def test_second_run_with_zero_records_leaves_first_runs_data_intact(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", chunk_id="run1-record")
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0

        empty_extracted_dir = tmp_path / "extracted_empty"
        empty_extracted_dir.mkdir()

        rc2 = run_full.stage_5_publish(empty_extracted_dir, publish_dir)

        assert rc2 == 1
        # A zero-record second run must not blank out the still-good first
        # run's published data.
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in published] == ["run1-record"]

    # -- 3. publish_dir creation and reuse -----------------------------------

    def test_publish_dir_created_when_missing_including_nested_parents(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "nested" / "does" / "not" / "exist" / "yet"
        _write_extracted_record(extracted_dir, "giki.json")

        assert not publish_dir.exists()
        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        assert publish_dir.is_dir()
        assert (publish_dir / "records.json").exists()
        assert (publish_dir / "institutions.json").exists()

    def test_preexisting_unrelated_file_in_publish_dir_is_left_alone(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        publish_dir.mkdir(parents=True)
        unrelated = publish_dir / "old_report.txt"
        unrelated.write_text("leftover from manual debugging", encoding="utf-8")
        _write_extracted_record(extracted_dir, "giki.json")

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        assert unrelated.exists()
        assert unrelated.read_text(encoding="utf-8") == "leftover from manual debugging"
        names = sorted(p.name for p in publish_dir.iterdir())
        assert names == ["institutions.json", "old_report.txt", "records.json"]

    # -- 4. _institutions_payload() edge cases -------------------------------

    def test_institutions_payload_zero_sources_produces_empty_campus_list_not_a_crash(
        self, monkeypatch
    ):
        fake_institution = Institution(
            id="fake-empty",
            name="Fake Empty Institution",
            admitting_body=False,
            ug_pg_mixed=False,
            sources=[],
        )
        monkeypatch.setattr(run_full, "load_institutions", lambda: [fake_institution])

        payload = run_full._institutions_payload()

        assert payload == [
            {
                "id": "fake-empty",
                "name": "Fake Empty Institution",
                "admitting_body": False,
                "ug_pg_mixed": False,
                "campuses": [],
            }
        ]

    def test_institutions_payload_filters_null_campus_and_preserves_named_order(self, monkeypatch):
        fake_institution = Institution(
            id="fake-multi",
            name="Fake Multi Institution",
            admitting_body=True,
            ug_pg_mixed=False,
            sources=[
                Source(institution_id="fake-multi", campus=None, url="https://a.example", format="html"),
                Source(institution_id="fake-multi", campus="Lahore", url="https://b.example", format="html"),
                Source(institution_id="fake-multi", campus="Karachi", url="https://c.example", format="html"),
            ],
        )
        monkeypatch.setattr(run_full, "load_institutions", lambda: [fake_institution])

        payload = run_full._institutions_payload()

        assert payload[0]["campuses"] == ["Lahore", "Karachi"]

    # -- write-failure atomicity across the two published files -------------

    def test_write_failure_on_one_file_leaves_both_previously_published_files_untouched(
        self, tmp_path, monkeypatch
    ):
        """stage_5_publish writes both files as one unit via
        _write_json_files_atomic: every payload is written to a temp sibling
        first, and only once ALL temp writes succeed are any of them
        os.replace()'d into place. So if writing institutions.json's temp
        file fails, records.json's temp file was already written but never
        replaced -- publish_dir must still show run1's original pair, not a
        new records.json paired with a stale institutions.json. The failure
        is also caught and reported (return 1), not an unhandled OSError.
        """
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", chunk_id="run1")
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        stale_records = (publish_dir / "records.json").read_text(encoding="utf-8")
        stale_institutions = (publish_dir / "institutions.json").read_text(encoding="utf-8")

        extracted_dir2 = tmp_path / "extracted2"
        _write_extracted_record(extracted_dir2, "giki.json", chunk_id="run2-new-data")

        original_write_text = Path.write_text

        def flaky_write_text(self, *args, **kwargs):
            if self.name.startswith("institutions.json."):
                raise OSError("simulated disk failure writing institutions.json")
            return original_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", flaky_write_text)

        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 1
        assert (publish_dir / "records.json").read_text(encoding="utf-8") == stale_records
        assert (publish_dir / "institutions.json").read_text(encoding="utf-8") == stale_institutions
        # No leftover temp file from the aborted write.
        assert sorted(p.name for p in publish_dir.iterdir()) == ["institutions.json", "records.json"]

    # -- 5. CLI wiring for `stage5` ------------------------------------------

    def test_cli_stage5_invokes_stage_5_publish_with_parsed_args(self, tmp_path, monkeypatch):
        """No existing test in this file (or elsewhere in tests/) exercises
        pipeline/run_full.py's main()/argparse wiring for any stage -- all
        coverage of this file is at the function level (stage_5_publish,
        stage_4_build in tests/test_pipeline_fixes.py, etc.). This adds one
        minimal CLI-level test for stage5 by patching sys.argv and stubbing
        stage_5_publish itself, to catch wiring bugs (e.g. wrong dest name,
        args passed in the wrong order/position) that function-level tests
        can't catch since they call stage_5_publish directly.
        """
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        extracted_dir.mkdir()

        captured = {}

        def fake_stage_5_publish(extracted, publish):
            captured["extracted"] = extracted
            captured["publish"] = publish
            return 0

        monkeypatch.setattr(run_full, "stage_5_publish", fake_stage_5_publish)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_full.py",
                "stage5",
                "--extracted",
                str(extracted_dir),
                "--publish-dir",
                str(publish_dir),
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            run_full.main()

        assert exc_info.value.code == 0
        assert captured["extracted"] == extracted_dir
        assert captured["publish"] == publish_dir
