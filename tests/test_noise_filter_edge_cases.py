"""QA pass (Phase T Task 5.1 follow-up) -- additional edge-case coverage for
extraction/run.py::build_extracted_records's noise filter (drop-all-null +
dedup + priority_chunk_ids), beyond what
tests/test_extraction.py::TestBuildExtractedRecordsNoiseFilter already
covers. Written by the qa subagent; does not modify the code under test.
"""
from __future__ import annotations

from extraction.chunker import chunk_scraped_record
from extraction.run import build_extracted_records
from extraction.schema import DegreeLevel


def _scraped_record(institution_id="giki", campus=None, pdfs=None, html="<p>Last date to apply: 10 August 2026.</p>"):
    return {
        "institution_id": institution_id,
        "campus": campus,
        "source_url": "https://admissions.giki.edu.pk",
        "fetched_at": "2026-07-09T00:00:00Z",
        "html": html,
        "pdfs": pdfs or [],
    }


class TestThreeWayDedupTies:
    def test_three_identical_content_chunks_lowest_chunk_id_wins(self):
        # HTML chunk ("giki") + two PDF twins with identical extractable text
        # -- three candidates in the same (institution_id, campus, content_hash)
        # group. No priority set, so the lowest chunk_id must win.
        record = _scraped_record(
            pdfs=[
                {"url": "https://admissions.giki.edu.pk/notice_a.pdf", "text": "Last date to apply: 10 August 2026."},
                {"url": "https://admissions.giki.edu.pk/notice_b.pdf", "text": "Last date to apply: 10 August 2026."},
            ]
        )
        chunk_ids = sorted(c.id for c in chunk_scraped_record(record))
        assert len(chunk_ids) == 3  # sanity: really is a 3-way tie

        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records([record], degree_levels, stats=stats)

        assert [chunk_id for chunk_id, _ in built] == [chunk_ids[0]]
        assert stats["deduplicated"] == 2

    def test_three_way_tie_with_one_priority_survivor(self):
        record = _scraped_record(
            pdfs=[
                {"url": "https://admissions.giki.edu.pk/notice_a.pdf", "text": "Last date to apply: 10 August 2026."},
                {"url": "https://admissions.giki.edu.pk/notice_b.pdf", "text": "Last date to apply: 10 August 2026."},
            ]
        )
        chunk_ids = sorted(c.id for c in chunk_scraped_record(record))
        # Pick the highest-sorting (i.e. the one lowest-chunk_id logic would
        # normally lose) as the priority survivor.
        priority_survivor = chunk_ids[-1]
        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(
            [record], degree_levels, stats=stats,
            priority_chunk_ids=frozenset({priority_survivor}),
        )

        assert [chunk_id for chunk_id, _ in built] == [priority_survivor]
        assert stats["deduplicated"] == 2


class TestPriorityVsPriorityTie:
    def test_two_priority_chunk_ids_in_same_group_falls_back_to_lowest_chunk_id(self):
        # Both the HTML chunk and one PDF twin are (independently) priority --
        # e.g. both happen to have separate Firestore overrides/decisions.
        # Neither should be dropped in favor of the other arbitrarily; the
        # documented tiebreak (lowest chunk_id) must still apply among ties.
        record = _scraped_record(
            pdfs=[{"url": "https://admissions.giki.edu.pk/notice.pdf", "text": "Last date to apply: 10 August 2026."}]
        )
        chunk_ids = sorted(c.id for c in chunk_scraped_record(record))
        assert len(chunk_ids) == 2
        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(
            [record], degree_levels, stats=stats,
            priority_chunk_ids=frozenset(chunk_ids),  # both are priority
        )

        assert [chunk_id for chunk_id, _ in built] == [chunk_ids[0]]
        assert stats["deduplicated"] == 1

    def test_three_way_priority_vs_priority_vs_nonpriority(self):
        # Two of the three candidates are priority; the non-priority one must
        # never win regardless of its chunk_id, and among the two priority
        # candidates the lowest chunk_id must win.
        record = _scraped_record(
            pdfs=[
                {"url": "https://admissions.giki.edu.pk/notice_a.pdf", "text": "Last date to apply: 10 August 2026."},
                {"url": "https://admissions.giki.edu.pk/notice_b.pdf", "text": "Last date to apply: 10 August 2026."},
            ]
        )
        chunk_ids = sorted(c.id for c in chunk_scraped_record(record))
        priority_ids = frozenset({chunk_ids[1], chunk_ids[2]})  # skip the lowest
        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(
            [record], degree_levels, stats=stats, priority_chunk_ids=priority_ids,
        )

        assert [chunk_id for chunk_id, _ in built] == [chunk_ids[1]]
        assert stats["deduplicated"] == 2


class TestSingleNonNullFieldKept:
    def test_only_admissions_open_set_is_not_dropped(self):
        # deadline/programs/constituent_college all null; only
        # admissions_open extracts a value -- must NOT be treated as
        # all-null. Regex extractor: "Applications are open" -> admissions_open="Open".
        record = _scraped_record(html="<p>Applications are open for Fall 2026.</p>")
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records([record], degree_levels, stats=stats)

        assert len(built) == 1
        _, extracted = built[0]
        assert extracted.admissions_open.value == "Open"
        assert extracted.deadline.value is None
        assert extracted.programs.value is None
        assert extracted.constituent_college.value is None
        assert stats["dropped_all_null"] == 0


class TestCampusDistinguishesGroups:
    def test_same_institution_different_campus_identical_content_not_deduplicated(self):
        records = [
            _scraped_record(institution_id="uet", campus="Lahore (Main)"),
            _scraped_record(institution_id="uet", campus="Taxila"),
        ]
        degree_levels = {"uet": DegreeLevel(value="Undergraduate")}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(records, degree_levels, stats=stats)

        # Both chunks share chunk_id "uet" (campus isn't part of base_chunk_id
        # unless it differs across two separately-chunked records here, but
        # institution_id + campus IS part of the dedup key) -- confirm both
        # survive since campus differs.
        assert len(built) == 2
        assert stats["deduplicated"] == 0
        campuses = {record.campus for _, record in built}
        assert campuses == {"Lahore (Main)", "Taxila"}


class TestStatsNoneDoesNotCrash:
    def test_stats_none_with_all_null_and_dedup_present(self):
        record_with_pdf = _scraped_record(
            pdfs=[{"url": "https://admissions.giki.edu.pk/notice.pdf", "text": "Last date to apply: 10 August 2026."}]
        )
        records = [
            _scraped_record(html="<p>No signal here at all.</p>"),
            record_with_pdf,
        ]
        # Every chunk_id classified the same -- degree_level.value is part of
        # the dedup group key (Phase T follow-up fix), so leaving the PDF
        # chunk_id unclassified would default it to Ambiguous and (correctly)
        # stop it from deduping against the HTML chunk here.
        chunk_ids = {c.id for c in chunk_scraped_record(record_with_pdf)}
        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}

        # Must not raise despite hitting both the all-null-drop path and the
        # dedup path with stats=None (the function's default).
        built, skipped, excluded_pg = build_extracted_records(records, degree_levels, stats=None)

        assert len(built) == 1
        assert skipped == 0
        assert excluded_pg == 0


class TestPriorityChunkIdsNoneVsOmitted:
    def test_omitted_and_explicit_none_behave_identically(self):
        record = _scraped_record(
            pdfs=[{"url": "https://admissions.giki.edu.pk/notice.pdf", "text": "Last date to apply: 10 August 2026."}]
        )
        chunk_ids = sorted(c.id for c in chunk_scraped_record(record))
        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}

        built_omitted, _, _ = build_extracted_records([record], degree_levels)
        built_explicit_none, _, _ = build_extracted_records([record], degree_levels, priority_chunk_ids=None)

        assert [cid for cid, _ in built_omitted] == [chunk_ids[0]]
        assert [cid for cid, _ in built_explicit_none] == [chunk_ids[0]]
        assert built_omitted[0][0] == built_explicit_none[0][0]

    def test_empty_frozenset_behaves_like_none(self):
        record = _scraped_record(
            pdfs=[{"url": "https://admissions.giki.edu.pk/notice.pdf", "text": "Last date to apply: 10 August 2026."}]
        )
        chunk_ids = sorted(c.id for c in chunk_scraped_record(record))
        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}

        built, _, _ = build_extracted_records(
            [record], degree_levels, priority_chunk_ids=frozenset()
        )

        assert [cid for cid, _ in built] == [chunk_ids[0]]
