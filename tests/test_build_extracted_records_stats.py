"""Tests for extraction/run.py::build_extracted_records's Phase M `stats`
parameter: llm_chunks/regex_chunks counters so a caller can distinguish a
healthy LLM field-extraction step from one that silently produced nothing
(both look identical from the written-record-count alone, since the regex
fallback still yields a normal-looking run).

No live network calls -- purely in-memory dicts, matching the existing
style in tests/test_extraction.py.
"""
from __future__ import annotations

from extraction.schema import DegreeLevel, Field
from extraction.run import build_extracted_records


def _scraped_record(institution_id, **overrides):
    record = {
        "institution_id": institution_id,
        "campus": None,
        "source_url": f"https://{institution_id}.edu.pk/admissions",
        "fetched_at": "2026-07-09T00:00:00Z",
        "html": "<p>Last date to apply: 10 August 2026.</p>",
        "pdfs": [],
    }
    record.update(overrides)
    return record


class TestBuildExtractedRecordsStats:
    def test_stats_none_default_does_not_change_existing_behavior(self):
        # Every pre-existing call site omits `stats` -- confirm the function
        # still runs to completion and returns the same 3-tuple shape with
        # no attempt to write into a stats dict that doesn't exist.
        records = [_scraped_record("giki")]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}

        built, skipped, excluded_postgraduate = build_extracted_records(records, degree_levels)

        assert len(built) == 1
        assert skipped == 0
        assert excluded_postgraduate == 0

    def test_stats_dict_counts_mixed_llm_and_regex_chunks(self):
        # Two institutions -> two chunks. "giki" has an llm_fields entry
        # (counts as llm_chunks); "uet" has no entry (falls back to regex,
        # counts as regex_chunks).
        records = [
            _scraped_record("giki"),
            _scraped_record("uet"),
        ]
        degree_levels = {
            "giki": DegreeLevel(value="Undergraduate"),
            "uet": DegreeLevel(value="Undergraduate"),
        }
        llm_fields = {
            "giki": {
                "deadline": Field(value="15 Sep 2026", confidence=0.95),
                "constituent_college": Field(),
                "programs": Field(value="BS Computer Science", confidence=0.9),
            }
        }
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(records, degree_levels, llm_fields, stats=stats)

        assert stats == {"llm_chunks": 1, "regex_chunks": 1, "dropped_all_null": 0, "deduplicated": 0}
        giki_record = next(r for cid, r in built if cid == "giki")
        uet_record = next(r for cid, r in built if cid == "uet")
        # giki used the LLM value verbatim...
        assert giki_record.deadline.value == "15 Sep 2026"
        # ...while uet fell back to the regex extractor and picked up the
        # date embedded in its own scraped HTML.
        assert uet_record.deadline.value is not None

    def test_stats_dict_all_llm_covered_yields_zero_regex_chunks(self):
        records = [_scraped_record("giki")]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        llm_fields = {
            "giki": {
                "deadline": Field(value="15 Sep 2026", confidence=0.95),
                "constituent_college": Field(),
                "programs": Field(),
            }
        }
        stats: dict[str, int] = {}

        build_extracted_records(records, degree_levels, llm_fields, stats=stats)

        assert stats == {"llm_chunks": 1, "regex_chunks": 0, "dropped_all_null": 0, "deduplicated": 0}

    def test_stats_dict_llm_fields_none_yields_zero_llm_chunks(self):
        # llm_fields=None (the step never ran) -- every chunk must be
        # attributed to regex_chunks, none to llm_chunks.
        records = [_scraped_record("giki"), _scraped_record("uet")]
        degree_levels = {
            "giki": DegreeLevel(value="Undergraduate"),
            "uet": DegreeLevel(value="Undergraduate"),
        }
        stats: dict[str, int] = {}

        build_extracted_records(records, degree_levels, llm_fields=None, stats=stats)

        assert stats == {"llm_chunks": 0, "regex_chunks": 2, "dropped_all_null": 0, "deduplicated": 0}

    def test_stats_dict_excludes_postgraduate_chunks_from_either_counter(self):
        # A Postgraduate-classified chunk is dropped entirely (hard rule 3)
        # before field extraction happens at all -- it must not be counted
        # toward llm_chunks or regex_chunks either.
        records = [_scraped_record("giki")]
        degree_levels = {"giki": DegreeLevel(value="Postgraduate")}
        stats: dict[str, int] = {}

        built, _, excluded_postgraduate = build_extracted_records(
            records, degree_levels, llm_fields=None, stats=stats
        )

        assert built == []
        assert excluded_postgraduate == 1
        assert stats == {"llm_chunks": 0, "regex_chunks": 0, "dropped_all_null": 0, "deduplicated": 0}

    def test_stats_dict_preexisting_keys_are_not_reset_to_zero(self):
        # setdefault means a caller who pre-seeds the dict (e.g. accumulating
        # across multiple build_extracted_records calls) keeps their running
        # total rather than having it silently reset.
        records = [_scraped_record("giki")]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        stats: dict[str, int] = {"llm_chunks": 5, "regex_chunks": 7}

        build_extracted_records(records, degree_levels, llm_fields=None, stats=stats)

        assert stats == {"llm_chunks": 5, "regex_chunks": 8, "dropped_all_null": 0, "deduplicated": 0}
