"""Tests for pipeline/run_full.py's Phase M coverage-regression guard in
stage_5_publish: refuse to publish when field coverage across
("deadline", "programs", "constituent_college") drops more than
_COVERAGE_DROP_THRESHOLD (50%) relative to the currently-published
records.json, unless allow_coverage_drop=True.

No live network calls, no live Firestore -- fetch_overrides is stubbed to
{} exactly like tests/test_pipeline_publish.py's autouse fixture, so this
file mirrors that convention rather than depending on it via import order.
"""
from __future__ import annotations

import json

import pytest

import pipeline.run_full as run_full


@pytest.fixture(autouse=True)
def _no_live_firestore(monkeypatch):
    monkeypatch.setattr(run_full, "fetch_overrides", lambda *a, **k: {})


def _record(chunk_id, institution_id="giki", deadline=None, programs=None, constituent_college=None):
    """Build one extracted-record dict with full coverage (all three
    _COVERAGE_FIELDS non-null) unless a field is explicitly passed as None."""
    def _field(value):
        if value is None:
            return {"value": None, "confidence": None, "note": None}
        return {"value": value, "confidence": 0.9, "note": None}

    return {
        "institution_id": institution_id,
        "campus": None,
        "source_url": f"https://{institution_id}.edu.pk/admissions",
        "fetched_at": "2026-07-09T00:00:00Z",
        "chunk_id": chunk_id,
        "degree_level": {"value": "Undergraduate", "reason": None},
        "constituent_college": _field(constituent_college),
        "deadline": _field(deadline),
        "fee": {"value": None, "confidence": None, "note": None},
        "programs": _field(programs),
    }


def _write(extracted_dir, filename, **kwargs):
    extracted_dir.mkdir(parents=True, exist_ok=True)
    (extracted_dir / filename).write_text(json.dumps(_record(**kwargs)), encoding="utf-8")


def _full_coverage_record(chunk_id, institution_id="giki"):
    return dict(
        chunk_id=chunk_id,
        institution_id=institution_id,
        deadline="10 Aug 2026",
        programs="BS Computer Science",
        constituent_college=None,  # giki has no constituent colleges; leave null intentionally
    )


class TestCoverageRegressionGuard:
    def test_first_ever_publish_with_no_previous_records_skips_guard(self, tmp_path):
        # No previous records.json exists at publish_dir at all -- even a
        # zero-coverage new run must be allowed through since there is
        # nothing to regress from.
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write(extracted_dir, "giki.json", chunk_id="giki")  # all fields null

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        assert (publish_dir / "records.json").is_file()

    def test_genuine_coverage_drop_below_threshold_is_refused_and_previous_untouched(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"

        # Run 1: full coverage across 2 records (deadline + programs set,
        # constituent_college left null -- giki has none) = 2/3 per record.
        _write(extracted_dir, "giki.json", chunk_id="giki", deadline="10 Aug 2026", programs="BS CS")
        _write(extracted_dir, "uet.json", chunk_id="uet", institution_id="uet", deadline="1 Sep 2026", programs="BS EE")
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        prior_records = (publish_dir / "records.json").read_text(encoding="utf-8")

        # Run 2: same two chunk ids, but every field regressed to null --
        # coverage collapses from 2/3 to 0, well under 50% of the previous
        # value. Must be refused and the previous files left byte-identical.
        extracted_dir2 = tmp_path / "extracted2"
        _write(extracted_dir2, "giki.json", chunk_id="giki")
        _write(extracted_dir2, "uet.json", chunk_id="uet", institution_id="uet")
        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 1
        assert (publish_dir / "records.json").read_text(encoding="utf-8") == prior_records

    def test_coverage_drop_that_stays_above_threshold_is_allowed_through(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"

        # Run 1: 4 records, all 3 coverage fields populated -> coverage 1.0.
        for i in range(4):
            _write(extracted_dir, f"r{i}.json", chunk_id=f"r{i}", deadline="10 Aug 2026",
                   programs="BS CS", constituent_college="Some College")
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0

        # Run 2: same 4 records, but only deadline populated (1/3 coverage
        # per record = 0.333) -- a real drop, but 0.333 > 1.0 * 0.5 is FALSE...
        # Actually we need coverage to stay ABOVE threshold*previous, i.e.
        # new_coverage >= previous_coverage * 0.5 = 0.5. Use 2 of 3 fields
        # populated -> per-record coverage 2/3 = 0.667 >= 0.5.
        extracted_dir2 = tmp_path / "extracted2"
        for i in range(4):
            _write(extracted_dir2, f"r{i}.json", chunk_id=f"r{i}", deadline="10 Aug 2026",
                   programs="BS CS")  # constituent_college now null
        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert len(published) == 4
        assert published[0]["deadline"]["value"] == "10 Aug 2026"

    def test_allow_coverage_drop_true_lets_a_big_drop_through_anyway(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write(extracted_dir, "giki.json", chunk_id="giki", deadline="10 Aug 2026", programs="BS CS")
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0

        extracted_dir2 = tmp_path / "extracted2"
        _write(extracted_dir2, "giki.json", chunk_id="giki")  # coverage collapses to 0

        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir, allow_coverage_drop=True)

        assert rc2 == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert published[0]["deadline"]["value"] is None

    def test_previous_coverage_zero_never_blocks_new_publish(self, tmp_path):
        # Previous run published successfully but with all-null coverage
        # fields (e.g. every source was blank that week) -- there's nothing
        # to regress from, so a subsequent, even-worse-looking run must not
        # be blocked either (previous_coverage > 0 guards this explicitly).
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write(extracted_dir, "giki.json", chunk_id="giki")  # all null -> coverage 0
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0

        extracted_dir2 = tmp_path / "extracted2"
        _write(extracted_dir2, "giki.json", chunk_id="giki")  # still all null
        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert published[0]["deadline"]["value"] is None

    def test_malformed_existing_records_json_is_skipped_not_fatal(self, tmp_path):
        # publish_dir already has a records.json, but it's corrupt (e.g. a
        # prior write was interrupted or hand-edited badly). This must be
        # treated as "no previous coverage to compare against" -- the guard
        # is skipped, not treated as a crash or a hard refusal.
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        publish_dir.mkdir(parents=True)
        (publish_dir / "records.json").write_text("{not valid json,,,", encoding="utf-8")
        _write(extracted_dir, "giki.json", chunk_id="giki")  # all-null new run

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        published = json.loads((publish_dir / "records.json").read_text(encoding="utf-8"))
        assert published[0]["chunk_id"] == "giki"

    def test_malformed_existing_records_json_wrong_shape_is_skipped_not_fatal(self, tmp_path):
        # A records.json that parses as valid JSON but isn't a list of
        # record dicts (e.g. truncated to `{}` or a list of scalars) must
        # also degrade gracefully rather than raising inside
        # ExtractedRecord.from_dict().
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        publish_dir.mkdir(parents=True)
        (publish_dir / "records.json").write_text(json.dumps([{"unexpected": "shape"}]), encoding="utf-8")
        _write(extracted_dir, "giki.json", chunk_id="giki")

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0

    def test_mass_scrape_failure_refused_even_when_fill_rate_holds(self, tmp_path):
        # Field coverage alone can't catch "most institutions failed to
        # scrape but the survivors extracted cleanly" -- fill-rate can stay
        # flat or even improve while institution count collapses. This is
        # exactly the gap code review flagged: coverage-only would let this
        # through since both runs have 100% fill-rate on the records that
        # exist.
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        for inst in ("giki", "uet", "pieas", "lums", "itu", "nust"):
            _write(extracted_dir, f"{inst}.json", chunk_id=inst, institution_id=inst,
                   deadline="10 Aug 2026", programs="BS CS", constituent_college=None)
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0
        prior_records = (publish_dir / "records.json").read_text(encoding="utf-8")

        # Run 2: only 1 of the 6 institutions survives (a mass outage), but
        # that one record has full field coverage -- fill-rate is 100% on
        # both runs.
        extracted_dir2 = tmp_path / "extracted2"
        _write(extracted_dir2, "giki.json", chunk_id="giki", institution_id="giki",
               deadline="11 Aug 2026", programs="BS CS", constituent_college=None)
        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 1
        assert (publish_dir / "records.json").read_text(encoding="utf-8") == prior_records

    def test_institution_count_drop_within_threshold_is_allowed_through(self, tmp_path):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        for inst in ("giki", "uet", "pieas", "lums"):
            _write(extracted_dir, f"{inst}.json", chunk_id=inst, institution_id=inst,
                   deadline="10 Aug 2026", programs="BS CS", constituent_college=None)
        rc1 = run_full.stage_5_publish(extracted_dir, publish_dir)
        assert rc1 == 0

        # 3 of 4 institutions survive (75% >= the 50% floor) -- allowed.
        extracted_dir2 = tmp_path / "extracted2"
        for inst in ("giki", "uet", "pieas"):
            _write(extracted_dir2, f"{inst}.json", chunk_id=inst, institution_id=inst,
                   deadline="10 Aug 2026", programs="BS CS", constituent_college=None)
        rc2 = run_full.stage_5_publish(extracted_dir2, publish_dir)

        assert rc2 == 0
