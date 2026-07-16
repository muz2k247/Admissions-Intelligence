"""Tests for pipeline/health.py -- Phase T Task 4 pipeline run health tracking.

No live network / no live pipeline run: everything here operates against a
tmp_path fragment file, mirroring how test_schedule_gate.py isolates pure
logic from I/O.
"""
from __future__ import annotations

import json

from pipeline.health import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_REFUSED,
    _derive_status,
    finalize,
    init_run,
    record_stage,
)


def test_init_run_writes_fragment_with_schema_and_timestamps(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    fragment = init_run(fragment_path, trigger="workflow_dispatch", run_id="12345")

    assert fragment["schema_version"] == 1
    assert fragment["trigger"] == "workflow_dispatch"
    assert fragment["run_id"] == "12345"
    assert "started_at" in fragment

    on_disk = json.loads(fragment_path.read_text(encoding="utf-8"))
    assert on_disk == fragment


def test_record_stage_merges_sections_without_clobbering_others(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    init_run(fragment_path, trigger="push", run_id="1")

    record_stage("scrape", {"attempted": 17, "ok": 16, "failed": 1}, fragment_path)
    record_stage("chunk", {"chunks": 42}, fragment_path)

    on_disk = json.loads(fragment_path.read_text(encoding="utf-8"))
    assert on_disk["scrape"] == {"attempted": 17, "ok": 16, "failed": 1}
    assert on_disk["chunk"] == {"chunks": 42}
    assert on_disk["run_id"] == "1"  # init_run's fields survive later record_stage calls


def test_record_stage_overwrites_same_section_on_repeat_call(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    init_run(fragment_path)
    record_stage("scrape", {"attempted": 1}, fragment_path)
    record_stage("scrape", {"attempted": 2}, fragment_path)

    on_disk = json.loads(fragment_path.read_text(encoding="utf-8"))
    assert on_disk["scrape"] == {"attempted": 2}


def test_record_stage_never_raises_on_unwritable_path(tmp_path):
    # Fragment path whose parent is itself a file, not a directory -- mkdir()
    # inside _write_fragment must fail with OSError, which record_stage is
    # documented to swallow (never fail the stage it's observing).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    fragment_path = blocker / "run_health.json"

    record_stage("scrape", {"attempted": 1}, fragment_path)  # must not raise


def test_finalize_with_no_fragment_reports_failed(tmp_path):
    # finalize() called with no init_run/record_stage ever having run --
    # simulates a crash before stage 1, or health.py never being wired up.
    fragment_path = tmp_path / "missing_run_health.json"
    publish_dir = tmp_path / "publish"

    health = finalize(publish_dir, fragment_path)

    assert health["status"] == STATUS_FAILED
    assert any("did not reach stage 5" in w for w in health["warnings"])
    assert (publish_dir / "health.json").is_file()
    on_disk = json.loads((publish_dir / "health.json").read_text(encoding="utf-8"))
    assert on_disk == health


def test_finalize_published_status(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    publish_dir = tmp_path / "publish"
    init_run(fragment_path, trigger="workflow_dispatch", run_id="99")
    record_stage("publish", {"decision": "published", "records_published": 11}, fragment_path)

    health = finalize(publish_dir, fragment_path)

    assert health["status"] == STATUS_PUBLISHED
    assert health["publish"] == {"decision": "published", "records_published": 11}
    assert health["warnings"] == []
    assert "finished_at" in health


def test_finalize_refused_status_with_warning(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    publish_dir = tmp_path / "publish"
    init_run(fragment_path)
    record_stage("publish", {"decision": "refused_coverage_drop"}, fragment_path)

    health = finalize(publish_dir, fragment_path)

    assert health["status"] == STATUS_REFUSED
    assert any("refused_coverage_drop" in w for w in health["warnings"])


def test_finalize_atomic_write_leaves_no_temp_file(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    publish_dir = tmp_path / "publish"
    init_run(fragment_path)
    record_stage("publish", {"decision": "published"}, fragment_path)

    finalize(publish_dir, fragment_path)

    leftovers = list(publish_dir.glob("*.tmp"))
    assert leftovers == []


def test_derive_status_regex_fallback_warning():
    fragment = {"publish": {"decision": "published"}, "build": {"extraction_mode": "regex_fallback"}}
    status, warnings = _derive_status(fragment)
    assert status == STATUS_PUBLISHED
    assert any("regex" in w for w in warnings)


def test_derive_status_mixed_extraction_warning():
    fragment = {"publish": {"decision": "published"}, "build": {"extraction_mode": "mixed"}}
    status, warnings = _derive_status(fragment)
    assert status == STATUS_PUBLISHED
    assert any("mix" in w for w in warnings)


def test_derive_status_scrape_failures_warning():
    fragment = {"publish": {"decision": "published"}, "scrape": {"attempted": 17, "ok": 15, "failed": 2}}
    status, warnings = _derive_status(fragment)
    assert status == STATUS_PUBLISHED
    assert any("2 of 17" in w for w in warnings)


def test_derive_status_failed_when_publish_section_missing():
    status, warnings = _derive_status({"scrape": {"attempted": 1, "ok": 1, "failed": 0}})
    assert status == STATUS_FAILED
    assert any("did not reach stage 5" in w for w in warnings)


def test_derive_status_failed_for_unknown_decision():
    status, warnings = _derive_status({"publish": {"decision": "something_unexpected"}})
    assert status == STATUS_FAILED
    assert any("something_unexpected" in w for w in warnings)
    assert any("Unrecognized" in w for w in warnings)


def test_derive_status_named_failure_decision_not_reported_as_unrecognized():
    # run_full.py's stage_5_publish records named failed_* decisions for its
    # own anticipated early-return branches -- these must read as a clear
    # failure reason, not get lumped in with a truly unexpected value.
    status, warnings = _derive_status({"publish": {"decision": "failed_write_error"}})
    assert status == STATUS_FAILED
    assert any("Publish failed: failed_write_error" in w for w in warnings)
    assert not any("Unrecognized" in w for w in warnings)


def test_record_stage_never_raises_on_non_json_serializable_payload(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    init_run(fragment_path)

    record_stage("scrape", {"attempted_at": object()}, fragment_path)  # must not raise

    # The fragment from before the bad call is left intact -- record_stage
    # read-modify-writes the WHOLE fragment, so a failed write leaves the
    # previously-written version on disk rather than a half-updated one.
    on_disk = json.loads(fragment_path.read_text(encoding="utf-8"))
    assert "scrape" not in on_disk


def test_record_stage_before_init_run_still_produces_a_schema_version(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    record_stage("scrape", {"attempted": 1}, fragment_path)

    on_disk = json.loads(fragment_path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 1
    assert on_disk["scrape"] == {"attempted": 1}
    assert "run_id" not in on_disk  # init_run never ran; finalize() fills this as null


def test_finalize_guarantees_top_level_keys_when_init_run_never_ran(tmp_path):
    fragment_path = tmp_path / "run_health.json"
    publish_dir = tmp_path / "publish"
    record_stage("publish", {"decision": "published"}, fragment_path)

    health = finalize(publish_dir, fragment_path)

    assert health["run_id"] is None
    assert health["trigger"] is None
    assert health["started_at"] is None
    assert health["status"] == STATUS_PUBLISHED
