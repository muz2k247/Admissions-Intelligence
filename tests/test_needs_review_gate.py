"""Tests for the Phase Q Needs-Review gate wired into
pipeline/run_full.py::stage_5_publish -- the confidence-gate split between
records.json (auto-published + approved) and needs_review.json (pending
curator review), and its interaction with the coverage-regression guard.

No live network / no live Firestore: fetch_overrides/fetch_review_settings/
fetch_review_decisions are all monkeypatched, matching the convention in
tests/test_pipeline_publish.py and tests/test_stage5_coverage_guard.py.
"""
from __future__ import annotations

import json

import pytest

import pipeline.run_full as run_full
from extraction.review_gate import content_hash
from extraction.schema import DegreeLevel, ExtractedRecord, Field, NULL_FIELD
from pipeline.overrides import _OverrideEntry


@pytest.fixture(autouse=True)
def _no_live_firestore(monkeypatch):
    monkeypatch.setattr(run_full, "fetch_overrides", lambda *a, **k: {})
    # (Phase R) _institutions_payload() now resolves institutions via
    # load_merged_institutions(), which makes a live Firestore REST call
    # internally (fetch_institution_docs()) unless stubbed here too. This
    # file doesn't assert on institutions.json's contents, so an empty list
    # is fine.
    monkeypatch.setattr(run_full, "load_merged_institutions", lambda *a, **k: [])


def _write_extracted_record(extracted_dir, filename, chunk_id="giki", institution_id="giki", deadline_confidence=0.85, **overrides):
    extracted_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "institution_id": institution_id,
        "campus": None,
        "source_url": f"https://{institution_id}.edu.pk/admissions/",
        "fetched_at": "2026-07-09T00:00:00Z",
        "chunk_id": chunk_id,
        "degree_level": {"value": "Undergraduate", "reason": None},
        "constituent_college": {"value": None, "confidence": None, "note": None},
        "deadline": {"value": "10 Aug 2026", "confidence": deadline_confidence, "note": None},
        "programs": {"value": None, "confidence": None, "note": None},
    }
    record.update(overrides)
    (extracted_dir / filename).write_text(json.dumps(record), encoding="utf-8")


def _record_hash(chunk_id="giki", institution_id="giki", deadline_value="10 Aug 2026", deadline_confidence=0.5):
    """Build the ExtractedRecord matching _write_extracted_record's default
    shape and compute its content_hash, for constructing matching decisions."""
    return content_hash(ExtractedRecord(
        institution_id=institution_id,
        campus=None,
        source_url=f"https://{institution_id}.edu.pk/admissions/",
        fetched_at="2026-07-09T00:00:00Z",
        chunk_id=chunk_id,
        degree_level=DegreeLevel(value="Undergraduate"),
        constituent_college=NULL_FIELD,
        deadline=Field(value=deadline_value, confidence=deadline_confidence),
        programs=NULL_FIELD,
    ))


class TestGateEnabled:
    def test_high_confidence_record_auto_publishes_not_queued(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.9)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in published] == ["giki"]
        assert queued == []

    def test_low_confidence_record_is_queued_not_published(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.5)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert published == []
        assert [r["chunk_id"] for r in queued] == ["giki"]

    def test_queued_record_includes_flagged_fields_and_content_hash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.5)

        run_full.stage_5_publish(extracted_dir, publish_dir)

        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert queued[0]["flagged_fields"] == ["deadline"]
        assert queued[0]["content_hash"] == _record_hash(deadline_value="10 Aug 2026", deadline_confidence=0.5)

    def test_matching_approved_decision_publishes_flagged_record(self, tmp_path, monkeypatch):
        h = _record_hash(deadline_value="10 Aug 2026", deadline_confidence=0.5)
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"giki": {"decision": "approved", "content_hash": h}},
        )
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.5)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in published] == ["giki"]
        assert queued == []

    def test_matching_rejected_decision_drops_record_entirely(self, tmp_path, monkeypatch):
        h = _record_hash(deadline_value="10 Aug 2026", deadline_confidence=0.5)
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"giki": {"decision": "rejected", "content_hash": h}},
        )
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.5)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert published == []
        assert queued == []  # dropped, not queued, not published

    def test_stale_decision_hash_mismatch_requeues(self, tmp_path, monkeypatch):
        # The decision's content_hash doesn't match this run's fresh content
        # (e.g. a re-scrape changed the deadline since the curator decided)
        # -- must NOT trust the stale decision either way.
        stale_hash = "0" * 64
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"giki": {"decision": "approved", "content_hash": stale_hash}},
        )
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.5)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert published == []
        assert [r["chunk_id"] for r in queued] == ["giki"]  # re-queued, decision not trusted

    def test_needs_review_json_always_written_even_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.9)

        run_full.stage_5_publish(extracted_dir, publish_dir)

        assert (publish_dir / "needs_review.json").is_file()
        assert json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8")) == []

    def test_stale_needs_review_json_is_overwritten_when_gate_later_clears_it(self, tmp_path, monkeypatch):
        # A record that was queued in run 1 gets approved before run 2 --
        # needs_review.json must reflect the CURRENT pending set, not
        # accumulate stale entries from a prior run.
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.5)
        run_full.stage_5_publish(extracted_dir, publish_dir)
        assert len(json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))) == 1

        h = _record_hash(deadline_value="10 Aug 2026", deadline_confidence=0.5)
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"giki": {"decision": "approved", "content_hash": h}},
        )
        run_full.stage_5_publish(extracted_dir, publish_dir)

        assert json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8")) == []
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in published] == ["giki"]

    def test_decision_matching_content_hash_covers_multiple_flagged_fields_atomically(self, tmp_path, monkeypatch):
        # A record flagged on TWO fields (deadline AND programs both below
        # threshold) with a decision keyed on the whole-record content_hash
        # -- content_hash is a single hash of all REVIEW_FIELDS values
        # together (extraction/review_gate.py), so one "approved" decision
        # must publish the record whole (both fields intact), never partial
        # per-field application. This is what distinguishes "content_hash
        # covers the record atomically" from a (nonexistent) per-field
        # decision scheme.
        record = ExtractedRecord(
            institution_id="giki",
            campus=None,
            source_url="https://giki.edu.pk/admissions/",
            fetched_at="2026-07-09T00:00:00Z",
            chunk_id="giki",
            degree_level=DegreeLevel(value="Undergraduate"),
            constituent_college=NULL_FIELD,
            deadline=Field(value="10 Aug 2026", confidence=0.5),
            programs=Field(value=["BS CS"], confidence=0.4),
        )
        h = content_hash(record)
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"giki": {"decision": "approved", "content_hash": h}},
        )
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(
            extracted_dir, "giki.json", deadline_confidence=0.5,
            programs={"value": ["BS CS"], "confidence": 0.4, "note": None},
        )

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert len(published) == 1
        assert published[0]["deadline"]["value"] == "10 Aug 2026"
        assert published[0]["programs"]["value"] == ["BS CS"]
        assert queued == []  # both flagged fields cleared by the single approval, not just one

    def test_decision_for_chunk_id_absent_from_current_records_is_silently_ignored(self, tmp_path, monkeypatch):
        # decisions can reference a chunk_id from a PAST run (e.g. the
        # institution's chunking changed, or it was dropped from the
        # config) that no longer exists among this run's extracted
        # records -- must be silently unused, not raise or otherwise
        # error the publish.
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"some-stale-chunk-id-not-in-this-run": {"decision": "approved", "content_hash": "0" * 64}},
        )
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.5)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert published == []
        assert [r["chunk_id"] for r in queued] == ["giki"]  # unaffected by the unrelated stale decision

    def test_needs_review_gate_summary_line_format(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        h = _record_hash(chunk_id="approved-one", institution_id="approved-one", deadline_confidence=0.5)
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"approved-one": {"decision": "approved", "content_hash": h}},
        )
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "auto.json", chunk_id="auto", institution_id="auto", deadline_confidence=0.9)
        _write_extracted_record(extracted_dir, "approved.json", chunk_id="approved-one", institution_id="approved-one", deadline_confidence=0.5)
        _write_extracted_record(extracted_dir, "queued.json", chunk_id="queued-one", institution_id="queued-one", deadline_confidence=0.5)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        out = capsys.readouterr().out
        assert "Needs-review gate (threshold=0.8): 1 auto-published, 1 approved from queue, 0 rejected (dropped), 1 pending review." in out
        assert "Stage 5 summary: 2 record(s), 1 queued for review, and" in out
        assert "institution(s) published to" in out

    def test_gate_disabled_summary_line_format(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": False, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.1)

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        out = capsys.readouterr().out
        assert "Needs-review gate disabled (settings/review_gate) -- publishing all records." in out
        assert "Stage 5 summary: 1 record(s), 0 queued for review, and" in out


class TestGateDisabled:
    def test_gate_disabled_publishes_everything_no_queue(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": False, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.1)  # would be flagged if gate were on

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in published] == ["giki"]
        assert queued == []

    def test_fetch_review_settings_failure_defaults_to_gate_enabled(self, tmp_path, monkeypatch):
        # fetch_review_settings itself already fails safe to {"enabled": True,
        # ...} on any Firestore problem (pipeline/review.py) -- this confirms
        # stage_5_publish doesn't second-guess that default toward "off"
        # (e.g. via a bad .get() default) if the settings dict is somehow
        # missing the "enabled" key entirely.
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", deadline_confidence=0.1)

        run_full.stage_5_publish(extracted_dir, publish_dir)

        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert published == []  # flagged and gate stayed on -- queued, not silently published
        assert [r["chunk_id"] for r in queued] == ["giki"]


class TestCoverageGuardUnaffectedByGating:
    def test_turning_gate_on_does_not_look_like_a_coverage_regression(self, tmp_path, monkeypatch):
        # Run 1: gate disabled, two institutions' records both publish in
        # full to records.json (needs_review.json empty). Basic sanity check
        # that flipping the gate on doesn't itself trip the guard -- doesn't
        # by itself distinguish the old (records.json-only) baseline from
        # the fixed (union) one, since needs_review.json is empty either
        # way here; see test_baseline_union_catches_regression_that_
        # records_only_baseline_would_miss below for that.
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": False, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        _write_extracted_record(extracted_dir, "giki.json", chunk_id="giki", institution_id="giki", deadline_confidence=0.9,
                                 programs={"value": ["BS CS"], "confidence": 0.9, "note": None})
        _write_extracted_record(extracted_dir, "uet.json", chunk_id="uet", institution_id="uet", deadline_confidence=0.5,
                                 programs={"value": ["BE EE"], "confidence": 0.9, "note": None})
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        assert len(json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))) == 2

        # Run 2: SAME extracted data, gate now enabled -- uet's low-
        # confidence deadline gets queued instead of published, shrinking
        # records.json from 2 to 1. The guard must not treat this as a
        # coverage/institution-count regression, since the full underlying
        # extraction (records + needs_review) is unchanged.
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        rc2 = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc2 == 0  # NOT refused by the coverage guard
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in published] == ["giki"]
        assert [r["chunk_id"] for r in queued] == ["uet"]

    def test_baseline_union_catches_regression_that_records_only_baseline_would_miss(self, tmp_path, monkeypatch):
        # This is the test that actually distinguishes the fixed (union)
        # baseline from the old (records.json-only) one. In steady-state
        # gate-on operation, records.json is a SUBSET of the true previous
        # extraction (queued records live in needs_review.json instead) --
        # a baseline built from records.json alone under-counts "previous",
        # which makes the guard's drop threshold (previous * 0.5) smaller
        # and therefore EASIER to clear, silently missing real regressions.
        #
        # Run 1: 3 institutions extracted. "a" auto-publishes (high conf);
        # "b" and "c" are queued (low conf, no decision) -- records.json
        # holds only "a" (1), needs_review.json holds "b","c" (2).
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        _write_extracted_record(extracted_dir, "a.json", chunk_id="a", institution_id="a", deadline_confidence=0.9,
                                 programs={"value": ["BS CS"], "confidence": 0.9, "note": None})
        _write_extracted_record(extracted_dir, "b.json", chunk_id="b", institution_id="b", deadline_confidence=0.5,
                                 programs={"value": ["BE EE"], "confidence": 0.9, "note": None})
        _write_extracted_record(extracted_dir, "c.json", chunk_id="c", institution_id="c", deadline_confidence=0.5,
                                 programs={"value": ["BE CE"], "confidence": 0.9, "note": None})
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        assert len(json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))) == 1
        assert len(json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))) == 2

        # Run 2: a genuine regression -- "b" and "c" vanish entirely from
        # extraction (e.g. their scrape started failing), leaving only "a".
        # That's a 3 -> 1 institution drop (a real ~67% loss) that the guard
        # must catch. Under the old records.json-only baseline (1, just
        # "a"), this run's new count (1) would NOT look like a drop at all
        # (1 is not < 1*0.5) -- the regression would have been silently
        # missed. Under the fixed union baseline (3: "a"+"b"+"c"), it is
        # correctly caught.
        extracted_dir2 = tmp_path / "extracted2"
        _write_extracted_record(extracted_dir2, "a.json", chunk_id="a", institution_id="a", deadline_confidence=0.9,
                                 programs={"value": ["BS CS"], "confidence": 0.9, "note": None})
        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 1  # caught by the guard

    def test_genuine_extraction_regression_still_caught_with_gate_enabled(self, tmp_path, monkeypatch):
        # Sanity check the guard still works AT ALL once gating is wired in
        # -- a genuine mass-scrape-failure run (going from many institutions
        # to one) must still be refused, gate on or off.
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
        for inst in ["a", "b", "c", "d", "e"]:
            _write_extracted_record(
                extracted_dir, f"{inst}.json", chunk_id=inst, institution_id=inst, deadline_confidence=0.9,
                programs={"value": ["BS CS"], "confidence": 0.9, "note": None},
            )
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0

        extracted_dir2 = tmp_path / "extracted2"
        _write_extracted_record(
            extracted_dir2, "a.json", chunk_id="a", institution_id="a", deadline_confidence=0.9,
            programs={"value": ["BS CS"], "confidence": 0.9, "note": None},
        )
        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 1  # refused: 5 institutions -> 1 is a >50% drop

    def test_rejected_record_permanently_dropping_from_baseline_does_not_trip_guard(self, tmp_path, monkeypatch):
        # Documents/locks in the ACCEPTED gap noted in stage_5_publish's
        # comment: once a record is "rejected" (matching content_hash), it
        # is dropped from BOTH records.json and needs_review.json, and so
        # drops out of the union baseline too -- permanently, as long as its
        # content doesn't change. This test proves that is genuinely inert
        # (does not itself trip the coverage guard on a later run where the
        # rejected record's institution has simply vanished from
        # extraction), so a future change to the baseline computation can't
        # silently alter this accepted behavior without a test failing here.
        #
        # Run 1: three institutions ("a","b","c") extracted; "c" is
        # low-confidence with a decision that REJECTS it (matching hash) --
        # dropped entirely, not published, not queued. "a" and "b" publish
        # normally. records.json = [a, b], needs_review.json = [].
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        c_hash = _record_hash(chunk_id="c", institution_id="c", deadline_confidence=0.5)
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"c": {"decision": "rejected", "content_hash": c_hash}},
        )
        _write_extracted_record(extracted_dir, "a.json", chunk_id="a", institution_id="a", deadline_confidence=0.9)
        _write_extracted_record(extracted_dir, "b.json", chunk_id="b", institution_id="b", deadline_confidence=0.9)
        _write_extracted_record(extracted_dir, "c.json", chunk_id="c", institution_id="c", deadline_confidence=0.5)
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        assert sorted(r["chunk_id"] for r in json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))) == ["a", "b"]
        assert json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8")) == []

        # Run 2: "c" has genuinely vanished from extraction entirely (as far
        # as this run is concerned, indistinguishable from "still rejected
        # with unchanged content" -- that's exactly the accepted gap). "a"
        # and "b" are re-extracted unchanged. Per the accepted behavior,
        # the guard's baseline is only ever built from what was actually
        # published last time (a, b) -- "c" never re-enters it -- so this
        # run (a, b again) must NOT look like a regression.
        extracted_dir2 = tmp_path / "extracted2"
        _write_extracted_record(extracted_dir2, "a.json", chunk_id="a", institution_id="a", deadline_confidence=0.9)
        _write_extracted_record(extracted_dir2, "b.json", chunk_id="b", institution_id="b", deadline_confidence=0.9)
        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 0  # accepted: does not trip the guard, "c" is inert
        assert sorted(r["chunk_id"] for r in json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))) == ["a", "b"]


class TestEditThenApproveEndToEnd:
    """Regression coverage for the admin CMS "edit then approve" bug: a
    curator correcting one flagged field via FieldEditor, then clicking
    Approve for the whole record, used to submit a decision keyed on the
    STALE pre-edit content_hash (dashboard/admin/src/components/ReviewQueue.jsx
    passed the untouched `record` to submitReviewDecision). Since
    stage_5_publish merges the curator's override in BEFORE computing
    content_hash for the gate comparison, that stale hash could never match,
    and the record silently re-queued forever despite being "approved".

    Fixed by dashboard/admin/src/lib/reviewRecord.js::applyFieldEdits (see
    dashboard/admin/scripts/verify_edit_then_approve.mjs for the JS-side
    check). This test exercises the equivalent full cycle on the Python side:
    run 1 queues a two-field-flagged record; the curator's correction lands
    as a Firestore override AND a decision keyed on the hash the FIXED UI
    would now compute (content_hash of the record with just the edited field
    replaced); run 2 must publish the record whole, both fields intact.
    """

    def test_edit_then_approve_publishes_on_next_run(self, tmp_path, monkeypatch):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        monkeypatch.setattr(run_full, "fetch_overrides", lambda *a, **k: {})
        monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})

        # Run 1: record flagged on BOTH deadline and programs -> queued, not published.
        _write_extracted_record(
            extracted_dir, "giki.json", deadline_confidence=0.5,
            programs={"value": ["BS CS"], "confidence": 0.4, "note": None},
        )
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        assert json.loads((publish_dir / "records.json").read_text(encoding="utf-8")) == []
        assert [r["chunk_id"] for r in json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))] == ["giki"]

        # Curator corrects `deadline` (via FieldEditor -> saveFieldOverride)
        # and leaves `programs` as-is, then clicks Approve. The override
        # captures `original` (the value the curator saw) same as the real
        # admin app does.
        overrides = {
            "giki": {
                "deadline": _OverrideEntry(
                    field=Field(value="15 Aug 2026", confidence=1.0, note="human-verified"),
                    original="10 Aug 2026",
                ),
            }
        }
        monkeypatch.setattr(run_full, "fetch_overrides", lambda *a, **k: overrides)

        # The hash the FIXED UI submits: deadline replaced with the edited
        # value, programs left at its original (still low-confidence) value
        # -- exactly what applyFieldEdits produces and what stage_5_publish
        # will compute post-merge_overrides.
        fixed_decision_record = ExtractedRecord(
            institution_id="giki",
            campus=None,
            source_url="https://giki.edu.pk/admissions/",
            fetched_at="2026-07-09T00:00:00Z",
            chunk_id="giki",
            degree_level=DegreeLevel(value="Undergraduate"),
            constituent_college=NULL_FIELD,
            deadline=Field(value="15 Aug 2026", confidence=1.0),
            programs=Field(value=["BS CS"], confidence=0.4),
        )
        fixed_hash = content_hash(fixed_decision_record)
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"giki": {"decision": "approved", "content_hash": fixed_hash}},
        )

        # Run 2: same extracted data as run 1 (the pipeline re-scraped and
        # extracted the same low-confidence values -- only the curator's
        # override/decision are new).
        rc2 = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc2 == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert [r["chunk_id"] for r in published] == ["giki"]
        assert published[0]["deadline"]["value"] == "15 Aug 2026"  # curator's correction applied
        assert published[0]["programs"]["value"] == ["BS CS"]  # unedited field preserved as-is
        assert queued == []  # published, not stuck re-queued

    def test_stale_pre_edit_hash_would_have_left_it_stuck_in_the_queue(self, tmp_path, monkeypatch):
        """Documents the bug this fix resolves: if the decision had been
        keyed on the STALE pre-edit hash (the old, broken behavior), the
        approval would never match post-merge content_hash and the record
        would incorrectly remain queued forever, even though a curator
        explicitly approved it."""
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
        _write_extracted_record(
            extracted_dir, "giki.json", deadline_confidence=0.5,
            programs={"value": ["BS CS"], "confidence": 0.4, "note": None},
        )

        overrides = {
            "giki": {
                "deadline": _OverrideEntry(
                    field=Field(value="15 Aug 2026", confidence=1.0, note="human-verified"),
                    original="10 Aug 2026",
                ),
            }
        }
        monkeypatch.setattr(run_full, "fetch_overrides", lambda *a, **k: overrides)

        # The STALE hash: computed from the record as it looked BEFORE the
        # edit (what the old, buggy ReviewQueue.jsx would have submitted).
        stale_decision_record = ExtractedRecord(
            institution_id="giki",
            campus=None,
            source_url="https://giki.edu.pk/admissions/",
            fetched_at="2026-07-09T00:00:00Z",
            chunk_id="giki",
            degree_level=DegreeLevel(value="Undergraduate"),
            constituent_college=NULL_FIELD,
            deadline=Field(value="10 Aug 2026", confidence=0.5),
            programs=Field(value=["BS CS"], confidence=0.4),
        )
        stale_hash = content_hash(stale_decision_record)
        monkeypatch.setattr(
            run_full, "fetch_review_decisions",
            lambda *a, **k: {"giki": {"decision": "approved", "content_hash": stale_hash}},
        )

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        queued = json.loads((publish_dir / "needs_review.json").read_text(encoding="utf-8"))
        assert published == []
        assert [r["chunk_id"] for r in queued] == ["giki"]  # stuck -- this is the bug the fix avoids
