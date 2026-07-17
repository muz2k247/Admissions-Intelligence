"""Tests for extraction/run.py's Phase-P deadline plausibility gate:
_validate_deadline_field, and its integration into build_extracted_records
for the LLM-sourced field path. The LLM field-extractor subagent's contract
has no plausibility rule of its own, so this is the only date-sanity check
an LLM-produced deadline ever goes through.

No live network calls -- purely in-memory dicts/Fields, matching the style
of tests/test_build_extracted_records_stats.py.
"""
from __future__ import annotations

import datetime as dt

from extraction.schema import DegreeLevel, Field, NULL_FIELD
from extraction.run import build_extracted_records, _validate_llm_deadline_field as _validate_deadline_field


def _scraped_record(institution_id, **overrides):
    record = {
        "institution_id": institution_id,
        "campus": None,
        "source_url": f"https://{institution_id}.edu.pk/admissions",
        "fetched_at": "2026-07-09T00:00:00Z",
        "html": "<p>No deadline keyword content here.</p>",
        "pdfs": [],
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# _validate_deadline_field — module-level function, called directly
# ---------------------------------------------------------------------------

class TestValidateDeadlineField:
    def test_null_field_passes_through_unchanged(self):
        assert _validate_deadline_field(NULL_FIELD) == NULL_FIELD

    def test_plausible_scalar_deadline_passes_through_unchanged(self):
        field = Field(value="2026-08-10", confidence=0.9)
        assert _validate_deadline_field(field) == field

    def test_implausible_scalar_deadline_is_nulled(self):
        field = Field(value="1998-08-10", confidence=0.9)
        result = _validate_deadline_field(field)
        assert result.value is None
        assert result.confidence is None
        assert result.note is not None
        assert "implausible" in result.note.lower()

    def test_far_future_scalar_deadline_is_nulled(self):
        field = Field(value="2099-08-10", confidence=0.9)
        result = _validate_deadline_field(field)
        assert result.value is None
        assert result.confidence is None

    def test_plausible_labeled_list_passes_through_unchanged(self):
        field = Field(
            value=[
                {"label": "BS Computer Science", "date": "2026-08-01"},
                {"label": "BS Electrical Engineering", "date": "2026-08-15"},
            ],
            confidence=0.75,
        )
        assert _validate_deadline_field(field) == field

    def test_labeled_list_with_one_implausible_entry_nulls_whole_field(self):
        field = Field(
            value=[
                {"label": "BS Computer Science", "date": "2026-08-01"},
                {"label": "BS Electrical Engineering", "date": "1998-08-15"},
            ],
            confidence=0.75,
        )
        result = _validate_deadline_field(field)
        assert result.value is None
        assert result.confidence is None
        assert result.note is not None
        assert "implausible" in result.note.lower()

    def test_non_dict_list_entries_do_not_crash_and_null_the_field(self):
        # Malformed LLM output: list entries that aren't dicts at all.
        field = Field(value=["2026-08-01", "2026-08-15"], confidence=0.75)
        result = _validate_deadline_field(field)
        assert result.value is None
        assert result.confidence is None

    def test_list_entries_missing_date_key_do_not_crash_and_null_the_field(self):
        field = Field(
            value=[{"label": "BS Computer Science"}, {"label": "BS EE", "date": "2026-08-15"}],
            confidence=0.75,
        )
        result = _validate_deadline_field(field)
        assert result.value is None
        assert result.confidence is None

    def test_empty_list_value_nulls_the_field(self):
        # Field construction requires confidence when value is non-None, so
        # an empty list is a valid (if odd) non-null Field to defend against.
        field = Field(value=[], confidence=0.5)
        result = _validate_deadline_field(field)
        assert result.value is None
        assert result.confidence is None

    def test_scalar_non_string_value_does_not_crash_and_nulls(self):
        field = Field(value=12345, confidence=0.9)
        result = _validate_deadline_field(field)
        assert result.value is None
        assert result.confidence is None


# ---------------------------------------------------------------------------
# build_extracted_records — LLM-sourced deadline runs through the same gate
# ---------------------------------------------------------------------------

class TestBuildExtractedRecordsValidatesLlmDeadline:
    def test_implausible_llm_deadline_is_nulled_before_reaching_final_record(self):
        records = [_scraped_record("giki")]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        llm_fields = {
            "giki": {
                "deadline": Field(value="1998-08-10", confidence=0.95),
                "constituent_college": NULL_FIELD,
                "programs": Field(value=["BS"], confidence=0.9),
            }
        }

        built, _, _ = build_extracted_records(records, degree_levels, llm_fields)

        giki_record = next(r for cid, r in built if cid == "giki")
        assert giki_record.deadline.value is None
        assert giki_record.deadline.confidence is None
        assert giki_record.deadline.note is not None
        assert "implausible" in giki_record.deadline.note.lower()

    def test_plausible_llm_deadline_passes_through_unchanged(self):
        records = [_scraped_record("giki")]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        llm_fields = {
            "giki": {
                "deadline": Field(value="2026-08-10", confidence=0.95),
                "constituent_college": NULL_FIELD,
                "programs": Field(value=["BS"], confidence=0.9),
            }
        }

        built, _, _ = build_extracted_records(records, degree_levels, llm_fields)

        giki_record = next(r for cid, r in built if cid == "giki")
        assert giki_record.deadline.value == "2026-08-10"
        assert giki_record.deadline.confidence == 0.95

    def test_malformed_llm_deadline_list_does_not_crash_the_build(self):
        records = [_scraped_record("giki")]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        llm_fields = {
            "giki": {
                "deadline": Field(
                    value=["not-a-dict", {"label": "BS CS"}],
                    confidence=0.75,
                ),
                "constituent_college": NULL_FIELD,
                "programs": NULL_FIELD,
            }
        }

        stats: dict[str, int] = {}
        built, _, _ = build_extracted_records(records, degree_levels, llm_fields, stats=stats)

        # The malformed deadline is safely nulled, not raised -- with every
        # other field also null on this fixture, the record has nothing left
        # to publish, so Phase T's noise filter drops it (hard-rule-1 safe:
        # this asserts no value, it doesn't alter one). The real assertion
        # here is that build_extracted_records didn't crash.
        assert built == []
        assert stats["dropped_all_null"] == 1
