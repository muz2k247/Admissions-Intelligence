"""Tests for extraction/llm_fields.py (parsing field-extractor subagent
output) and build_extracted_records' LLM-vs-regex precedence in
extraction/run.py.
"""
from __future__ import annotations

import json

from extraction.llm_fields import load_llm_field_results
from extraction.run import build_extracted_records, run_build
from extraction.schema import DegreeLevel, Field, NULL_FIELD


class TestLoadLlmFieldResults:
    def test_parses_well_formed_output(self, tmp_path):
        path = tmp_path / "llm_fields.json"
        path.write_text(json.dumps({
            "giki": {
                "deadline": {"value": "2026-08-15", "confidence": 0.9, "note": None},
                "programs": {"value": ["BS Computer Science"], "confidence": 0.85, "note": None},
                "constituent_college": None,
            }
        }), encoding="utf-8")

        results = load_llm_field_results(path)

        assert results["giki"]["deadline"] == Field(value="2026-08-15", confidence=0.9, note=None)
        assert results["giki"]["programs"] == Field(value=["BS Computer Science"], confidence=0.85, note=None)
        assert results["giki"]["constituent_college"] == NULL_FIELD

    def test_missing_field_key_defaults_to_null(self, tmp_path):
        path = tmp_path / "llm_fields.json"
        path.write_text(json.dumps({"giki": {"deadline": {"value": "2026-08-15", "confidence": 0.9, "note": None}}}), encoding="utf-8")

        results = load_llm_field_results(path)

        assert results["giki"]["deadline"].value == "2026-08-15"
        assert results["giki"]["programs"] == NULL_FIELD
        assert results["giki"]["constituent_college"] == NULL_FIELD

    def test_invalid_field_invariant_degrades_to_null_not_crash(self, tmp_path):
        # value present but confidence missing violates Field's dataclass
        # invariant -- must degrade to NULL_FIELD with a warning, not raise
        # and crash the whole load.
        path = tmp_path / "llm_fields.json"
        path.write_text(json.dumps({
            "giki": {
                "deadline": {"value": "2026-08-15", "confidence": None, "note": None},
                "programs": {"value": "not a list", "confidence": 1.5, "note": None},  # out of [0,1] range
            }
        }), encoding="utf-8")

        results = load_llm_field_results(path)

        assert results["giki"]["deadline"] == NULL_FIELD
        assert results["giki"]["programs"] == NULL_FIELD
        # other chunks/fields in the same file are unaffected
        assert results["giki"]["constituent_college"] == NULL_FIELD

    def test_malformed_chunk_entry_is_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "llm_fields.json"
        path.write_text(json.dumps({
            "broken": "not an object",
            "giki": {"deadline": {"value": "2026-08-15", "confidence": 0.9, "note": None}},
        }), encoding="utf-8")

        results = load_llm_field_results(path)

        assert "broken" not in results
        assert results["giki"]["deadline"].value == "2026-08-15"

    def test_empty_file_returns_empty_dict(self, tmp_path):
        path = tmp_path / "llm_fields.json"
        path.write_text("{}", encoding="utf-8")
        assert load_llm_field_results(path) == {}

    def test_non_dict_field_value_degrades_to_null_not_crash(self, tmp_path):
        # A bare string/number/list/bool in place of a {value, confidence,
        # note} object has no .get() method -- Field.from_dict would raise
        # AttributeError (not ValueError/TypeError) if called on it directly;
        # this must be caught before that call, not crash the whole load.
        path = tmp_path / "llm_fields.json"
        path.write_text(json.dumps({
            "giki": {
                "deadline": "2026-08-15",  # bare string, not an object
                "programs": ["BS"],  # bare list
                "constituent_college": True,  # bare bool
            }
        }), encoding="utf-8")

        results = load_llm_field_results(path)

        assert results["giki"]["deadline"] == NULL_FIELD
        assert results["giki"]["programs"] == NULL_FIELD
        assert results["giki"]["constituent_college"] == NULL_FIELD


class TestRunBuildLlmFallback:
    def _write_scraped_record(self, scraped_dir, filename, **overrides):
        scraped_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "institution_id": "giki",
            "campus": None,
            "source_url": "https://admissions.giki.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Last date to apply: 10 August 2026. BS programs offered.</p>",
            "pdfs": [],
        }
        record.update(overrides)
        (scraped_dir / filename).write_text(json.dumps(record), encoding="utf-8")

    def test_missing_llm_extracted_file_degrades_not_fatal(self, tmp_path):
        # The field-extractor's zero-cost fallback path (a missing/corrupt
        # output file, e.g. because it timed out before writing anything)
        # must behave like --llm-extracted was never passed at all, not like
        # a fatal error -- the pipeline's graceful-degradation guarantee
        # shouldn't depend on the caller correctly omitting the flag.
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(scraped_dir, "giki.json")

        classified_path = tmp_path / "classified.json"
        classified_path.write_text(json.dumps({"Undergraduate": ["giki"]}), encoding="utf-8")

        out_dir = tmp_path / "extracted"
        rc = run_build(scraped_dir, classified_path, out_dir, llm_extracted_path=tmp_path / "does_not_exist.json")

        assert rc == 0
        extracted = json.loads((out_dir / "giki.json").read_text(encoding="utf-8"))
        assert extracted["deadline"]["value"] is not None  # regex fallback found it


class TestBuildExtractedRecordsLlmPrecedence:
    def _scraped_record(self, **overrides):
        record = {
            "institution_id": "giki",
            "campus": None,
            "source_url": "https://admissions.giki.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Last date to apply: 10 August 2026. BS programs offered.</p>",
            "pdfs": [],
        }
        record.update(overrides)
        return record

    def test_llm_fields_win_when_present_for_chunk(self):
        records = [self._scraped_record()]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        llm_fields = {
            "giki": {
                "deadline": Field(value="2026-09-01", confidence=0.95, note="llm-extracted"),
                "programs": NULL_FIELD,
                "constituent_college": NULL_FIELD,
            }
        }

        built, _, _ = build_extracted_records(records, degree_levels, llm_fields)

        assert len(built) == 1
        _, extracted = built[0]
        # LLM's deadline value wins outright over what the regex extractor
        # would have found in the same text (10 August 2026)
        assert extracted.deadline.value == "2026-09-01"
        assert extracted.deadline.note == "llm-extracted"

    def test_regex_fallback_when_llm_fields_is_none(self):
        records = [self._scraped_record()]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}

        built, _, _ = build_extracted_records(records, degree_levels, llm_fields=None)

        assert len(built) == 1
        _, extracted = built[0]
        assert extracted.deadline.value is not None
        assert extracted.programs.value == ["BS"]

    def test_regex_fallback_when_chunk_missing_from_llm_fields(self):
        # llm_fields step ran but this specific chunk wasn't covered by it
        # (e.g. subagent omitted it) -- must fall back to regex for just
        # this chunk, not null everything.
        records = [self._scraped_record()]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        llm_fields = {"some_other_chunk": {"deadline": NULL_FIELD, "programs": NULL_FIELD, "constituent_college": NULL_FIELD}}

        built, _, _ = build_extracted_records(records, degree_levels, llm_fields)

        assert len(built) == 1
        _, extracted = built[0]
        assert extracted.deadline.value is not None  # regex found it

    def test_default_llm_fields_none_matches_prior_regex_only_behavior(self):
        # build_extracted_records must still work when called with the old
        # 2-argument signature (llm_fields omitted entirely).
        records = [self._scraped_record()]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}

        built, _, _ = build_extracted_records(records, degree_levels)

        assert len(built) == 1
        _, extracted = built[0]
        assert extracted.deadline.value is not None
