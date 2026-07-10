"""Tests for Phase C extraction code (extraction/schema.py, fields.py,
chunker.py, classify.py, run.py).

No live network calls — chunker/classify/run tests use in-memory dicts and
tmp_path-based fixture files only, matching the style of tests/test_scraper.py
(hand-rolled fakes, no requests_mock package).
"""
from __future__ import annotations

import json

import pytest

from extraction.schema import Field, NULL_FIELD, DegreeLevel, ExtractedRecord
from extraction.fields import (
    extract_deadline,
    extract_fee,
    extract_programs,
    extract_constituent_college,
)
from extraction.chunker import Chunk, chunk_scraped_record
from extraction.classify import load_classifier_results
from extraction.run import build_extracted_records, run_chunk, run_build


# ---------------------------------------------------------------------------
# schema.py — Field
# ---------------------------------------------------------------------------

class TestField:
    def test_null_field_has_none_value_and_confidence(self):
        f = Field()
        assert f.value is None
        assert f.confidence is None
        assert f.is_null is True

    def test_valid_field_with_value_and_confidence(self):
        f = Field(value="10 Aug 2026", confidence=0.85)
        assert f.is_null is False

    def test_raises_if_value_set_but_confidence_none(self):
        with pytest.raises(ValueError):
            Field(value="10 Aug 2026", confidence=None)

    def test_raises_if_confidence_set_but_value_none(self):
        with pytest.raises(ValueError):
            Field(value=None, confidence=0.9)

    def test_raises_if_confidence_out_of_range_high(self):
        with pytest.raises(ValueError):
            Field(value="x", confidence=1.5)

    def test_raises_if_confidence_out_of_range_low(self):
        with pytest.raises(ValueError):
            Field(value="x", confidence=-0.1)

    def test_confidence_boundary_values_are_valid(self):
        assert Field(value="x", confidence=0.0).confidence == 0.0
        assert Field(value="x", confidence=1.0).confidence == 1.0

    def test_to_dict_round_trip(self):
        f = Field(value="Rs. 2000/-", confidence=0.8, note="fee note")
        d = f.to_dict()
        assert d == {"value": "Rs. 2000/-", "confidence": 0.8, "note": "fee note"}
        f2 = Field.from_dict(d)
        assert f2 == f

    def test_from_dict_none_returns_null_field(self):
        assert Field.from_dict(None) == NULL_FIELD

    def test_from_dict_empty_dict_returns_null_field(self):
        assert Field.from_dict({}) == NULL_FIELD

    def test_field_is_frozen(self):
        f = Field(value="x", confidence=0.5)
        with pytest.raises(Exception):
            f.value = "y"


# ---------------------------------------------------------------------------
# schema.py — DegreeLevel
# ---------------------------------------------------------------------------

class TestDegreeLevel:
    def test_valid_undergraduate(self):
        d = DegreeLevel(value="Undergraduate")
        assert d.value == "Undergraduate"
        assert d.reason is None

    def test_valid_postgraduate(self):
        d = DegreeLevel(value="Postgraduate")
        assert d.value == "Postgraduate"

    def test_none_value_requires_reason(self):
        with pytest.raises(ValueError):
            DegreeLevel(value=None, reason=None)

    def test_none_value_with_reason_is_valid(self):
        d = DegreeLevel(value=None, reason="no-signal")
        assert d.value is None
        assert d.reason == "no-signal"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            DegreeLevel(value="Graduate")

    def test_invalid_value_lowercase_raises(self):
        with pytest.raises(ValueError):
            DegreeLevel(value="undergraduate")

    def test_to_dict_from_dict_round_trip(self):
        d = DegreeLevel(value="Undergraduate")
        d2 = DegreeLevel.from_dict(d.to_dict())
        assert d2 == d

    def test_from_dict_none_produces_ambiguous_with_reason(self):
        d = DegreeLevel.from_dict(None)
        assert d.value is None
        assert d.reason == "no-signal"

    def test_from_dict_empty_dict_produces_ambiguous_with_reason(self):
        d = DegreeLevel.from_dict({})
        assert d.value is None
        assert d.reason == "no-signal"

    def test_degree_level_is_frozen(self):
        d = DegreeLevel(value="Undergraduate")
        with pytest.raises(Exception):
            d.value = "Postgraduate"


# ---------------------------------------------------------------------------
# schema.py — ExtractedRecord
# ---------------------------------------------------------------------------

class TestExtractedRecord:
    def _make_record(self):
        return ExtractedRecord(
            institution_id="giki",
            campus=None,
            source_url="https://admissions.giki.edu.pk",
            fetched_at="2026-07-09T00:00:00Z",
            chunk_id="giki",
            degree_level=DegreeLevel(value="Undergraduate"),
            constituent_college=NULL_FIELD,
            deadline=Field(value="10 Aug 2026", confidence=0.85),
            fee=Field(value="Rs. 2000/-", confidence=0.8),
            programs=Field(value=["BS"], confidence=0.6),
        )

    def test_to_dict_from_dict_round_trip(self):
        record = self._make_record()
        d = record.to_dict()
        record2 = ExtractedRecord.from_dict(d)
        assert record2 == record

    def test_round_trip_retains_source_url(self):
        record = self._make_record()
        d = record.to_dict()
        record2 = ExtractedRecord.from_dict(d)
        assert record2.source_url == "https://admissions.giki.edu.pk"

    def test_from_dict_missing_optional_fields_default_to_null(self):
        minimal = {
            "institution_id": "giki",
            "source_url": "https://admissions.giki.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "chunk_id": "giki",
        }
        record = ExtractedRecord.from_dict(minimal)
        assert record.campus is None
        assert record.deadline == NULL_FIELD
        assert record.fee == NULL_FIELD
        assert record.programs == NULL_FIELD
        assert record.constituent_college == NULL_FIELD
        assert record.degree_level.value is None
        assert record.degree_level.reason == "no-signal"

    def test_record_is_frozen(self):
        record = self._make_record()
        with pytest.raises(Exception):
            record.source_url = "https://other.com"


# ---------------------------------------------------------------------------
# fields.py — extract_deadline
# ---------------------------------------------------------------------------

class TestExtractDeadline:
    def test_no_match_returns_null_field(self):
        f = extract_deadline("There is nothing date-related here at all.")
        assert f.value is None
        assert f.confidence is None

    def test_empty_text_returns_null_field(self):
        assert extract_deadline("") == NULL_FIELD
        assert extract_deadline(None) == NULL_FIELD

    def test_single_clear_deadline_extracted(self):
        text = "The last date to apply is 10 August 2026 for all programs."
        f = extract_deadline(text)
        assert f.value is not None
        assert "10" in f.value
        assert f.confidence is not None
        assert 0.0 <= f.confidence <= 1.0

    def test_repeated_consistent_deadline_boosts_confidence(self):
        text = (
            "Last date to apply: 10 August 2026. "
            "Please note the deadline: 10 August 2026 firmly."
        )
        f = extract_deadline(text)
        assert f.value is not None
        assert f.confidence == 0.95

    def test_single_mention_lower_confidence(self):
        text = "Last date to apply: 10 August 2026."
        f = extract_deadline(text)
        assert f.confidence == 0.85

    def test_conflicting_deadlines_yield_null_field_not_a_guess(self):
        text = (
            "Last date to apply: 10 August 2026. "
            "Deadline: 15 September 2026."
        )
        f = extract_deadline(text)
        assert f.value is None
        assert f.confidence is None
        assert f.note is not None
        assert "conflicting" in f.note.lower()

    def test_keyword_without_nearby_date_returns_null(self):
        text = "The deadline for something unrelated is announced separately elsewhere on this website with no digits nearby whatsoever, only words."
        f = extract_deadline(text)
        assert f.value is None


# ---------------------------------------------------------------------------
# fields.py — extract_fee
# ---------------------------------------------------------------------------

class TestExtractFee:
    def test_no_match_returns_null_field(self):
        f = extract_fee("No monetary information present in this text.")
        assert f.value is None
        assert f.confidence is None

    def test_empty_text_returns_null_field(self):
        assert extract_fee("") == NULL_FIELD
        assert extract_fee(None) == NULL_FIELD

    def test_single_clear_fee_extracted(self):
        text = "The application fee is Rs. 2000/- payable online."
        f = extract_fee(text)
        assert f.value is not None
        assert f.confidence is not None
        assert 0.0 <= f.confidence <= 1.0

    def test_conflicting_fees_yield_null_field(self):
        text = "The application fee is Rs. 2000/-. The admission fee is PKR 3500."
        f = extract_fee(text)
        assert f.value is None
        assert f.confidence is None
        assert f.note is not None
        assert "conflicting" in f.note.lower()

    def test_consistent_repeated_fee_high_confidence(self):
        text = "Application fee: Rs. 2000/-. Note the fee Rs. 2000/- is non-refundable."
        f = extract_fee(text)
        assert f.value is not None
        assert f.confidence == 0.9


# ---------------------------------------------------------------------------
# fields.py — extract_programs
# ---------------------------------------------------------------------------

class TestExtractPrograms:
    def test_no_match_returns_null_field(self):
        f = extract_programs("Nothing program-related in this sentence.")
        assert f.value is None
        assert f.confidence is None

    def test_empty_text_returns_null_field(self):
        assert extract_programs("") == NULL_FIELD
        assert extract_programs(None) == NULL_FIELD

    def test_finds_program_tokens_sorted_and_deduped(self):
        text = "We offer BS Computer Science, MS programs, and also BS Electrical Engineering."
        f = extract_programs(text)
        assert f.value == ["BS", "MS"]
        assert f.confidence == 0.6
        assert f.note is not None


# ---------------------------------------------------------------------------
# fields.py — extract_constituent_college (documented always-null behavior)
# ---------------------------------------------------------------------------

class TestExtractConstituentCollege:
    @pytest.mark.parametrize("text", [
        "",
        None,
        "King Edward Medical University is a constituent college.",
        "Allama Iqbal Medical College merit list published today.",
        "Random unrelated text with no institution names at all.",
    ])
    def test_always_returns_null_field_regardless_of_input(self, text):
        f = extract_constituent_college(text)
        assert f.value is None
        assert f.confidence is None
        assert f == NULL_FIELD


# ---------------------------------------------------------------------------
# chunker.py
# ---------------------------------------------------------------------------

class TestChunkScrapedRecord:
    def test_basic_html_record_produces_one_chunk(self):
        record = {
            "institution_id": "giki",
            "campus": None,
            "source_url": "https://admissions.giki.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<html><body><p>Last date to apply: 10 August 2026.</p></body></html>",
            "pdfs": [],
        }
        chunks = chunk_scraped_record(record)
        assert len(chunks) == 1
        chunk = chunks[0]
        assert isinstance(chunk, Chunk)
        assert chunk.institution_id == "giki"
        assert chunk.campus is None
        assert chunk.source_url == "https://admissions.giki.edu.pk"
        assert chunk.id == "giki"
        assert "Last date to apply" in chunk.raw_text
        # HTML tags stripped
        assert "<p>" not in chunk.raw_text
        assert "<html>" not in chunk.raw_text

    def test_campus_present_produces_slugged_chunk_id(self):
        record = {
            "institution_id": "uet",
            "campus": "Lahore (Main)",
            "source_url": "https://apply.uet.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Some content</p>",
            "pdfs": [],
        }
        chunks = chunk_scraped_record(record)
        assert chunks[0].id == "uet__lahore_(main)"
        assert chunks[0].campus == "Lahore (Main)"

    def test_pdf_text_is_appended_to_html_text(self):
        record = {
            "institution_id": "pu",
            "campus": None,
            "source_url": "https://pu.edu.pk/admissions",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Main page content.</p>",
            "pdfs": [{"url": "https://pu.edu.pk/x.pdf", "text": "Merit list PDF content."}],
        }
        chunks = chunk_scraped_record(record)
        assert "Main page content." in chunks[0].raw_text
        assert "Merit list PDF content." in chunks[0].raw_text

    def test_pdf_without_text_key_is_skipped(self):
        record = {
            "institution_id": "pu",
            "campus": None,
            "source_url": "https://pu.edu.pk/admissions",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Main page content.</p>",
            "pdfs": [{"url": "https://pu.edu.pk/broken.pdf", "text": None, "error": "extraction failed"}],
        }
        chunks = chunk_scraped_record(record)
        assert chunks[0].raw_text.strip() == "Main page content."

    def test_no_html_and_no_pdf_text_produces_no_chunks(self):
        record = {
            "institution_id": "x",
            "campus": None,
            "source_url": "https://example.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": None,
            "pdfs": [],
        }
        assert chunk_scraped_record(record) == []

    def test_empty_html_string_produces_no_chunks(self):
        record = {
            "institution_id": "x",
            "campus": None,
            "source_url": "https://example.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "",
            "pdfs": [],
        }
        assert chunk_scraped_record(record) == []

    def test_html_with_only_whitespace_produces_no_chunks(self):
        record = {
            "institution_id": "x",
            "campus": None,
            "source_url": "https://example.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<html><body>   </body></html>",
            "pdfs": [],
        }
        assert chunk_scraped_record(record) == []

    def test_to_classifier_dict_shape(self):
        chunk = Chunk(
            id="giki", institution_id="giki", campus=None,
            source_url="https://admissions.giki.edu.pk",
            fetched_at="2026-07-09T00:00:00Z",
            raw_text="Some text.",
        )
        d = chunk.to_classifier_dict()
        assert d == {
            "id": "giki",
            "institution": "giki",
            "source_url": "https://admissions.giki.edu.pk",
            "raw_text": "Some text.",
        }


# ---------------------------------------------------------------------------
# classify.py
# ---------------------------------------------------------------------------

class TestLoadClassifierResults:
    def test_reads_ug_pg_and_ambiguous_buckets(self, tmp_path):
        data = {
            "Undergraduate": ["giki", "pieas"],
            "Postgraduate": ["nums"],
            "Ambiguous": [{"id": "uhs", "reason": "mixed-ug-pg-content"}],
        }
        path = tmp_path / "classified.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        results = load_classifier_results(path)

        assert results["giki"] == DegreeLevel(value="Undergraduate")
        assert results["pieas"] == DegreeLevel(value="Undergraduate")
        assert results["nums"] == DegreeLevel(value="Postgraduate")
        assert results["uhs"].value is None
        assert results["uhs"].reason == "mixed-ug-pg-content"

    def test_ambiguous_item_missing_reason_defaults_to_no_signal(self, tmp_path):
        data = {"Ambiguous": [{"id": "x"}]}
        path = tmp_path / "classified.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        results = load_classifier_results(path)
        assert results["x"].value is None
        assert results["x"].reason == "no-signal"

    def test_empty_file_returns_empty_dict(self, tmp_path):
        path = tmp_path / "classified.json"
        path.write_text("{}", encoding="utf-8")
        assert load_classifier_results(path) == {}

    def test_accepts_str_or_path(self, tmp_path):
        data = {"Undergraduate": ["giki"]}
        path = tmp_path / "classified.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        results = load_classifier_results(str(path))
        assert results["giki"].value == "Undergraduate"


# ---------------------------------------------------------------------------
# run.py — chunk / build CLI functions
# ---------------------------------------------------------------------------

class TestRunChunkAndBuild:
    def _write_scraped_record(self, scraped_dir, filename, **overrides):
        scraped_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "institution_id": "giki",
            "campus": None,
            "source_url": "https://admissions.giki.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Last date to apply: 10 August 2026. Application fee is Rs. 2000/-. BS programs offered.</p>",
            "pdfs": [],
        }
        record.update(overrides)
        (scraped_dir / filename).write_text(json.dumps(record), encoding="utf-8")
        return record

    def test_run_chunk_writes_chunk_file(self, tmp_path):
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(scraped_dir, "giki.json")
        out_path = tmp_path / "chunks" / "chunks.json"

        rc = run_chunk(scraped_dir, out_path)

        assert rc == 0
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["id"] == "giki"
        assert data[0]["source_url"] == "https://admissions.giki.edu.pk"

    def test_run_chunk_skips_errored_records(self, tmp_path):
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(
            scraped_dir, "broken.json",
            institution_id="broken", html=None, error="fetch failed: timeout",
        )
        out_path = tmp_path / "chunks" / "chunks.json"

        rc = run_chunk(scraped_dir, out_path)

        assert rc == 0
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data == []

    def test_run_build_full_round_trip_retains_source_url_and_fields(self, tmp_path):
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(scraped_dir, "giki.json")

        classified_path = tmp_path / "classified.json"
        classified_path.write_text(
            json.dumps({"Undergraduate": ["giki"]}), encoding="utf-8"
        )

        out_dir = tmp_path / "extracted"
        rc = run_build(scraped_dir, classified_path, out_dir)

        assert rc == 0
        out_file = out_dir / "giki.json"
        assert out_file.exists()

        extracted = ExtractedRecord.from_dict(json.loads(out_file.read_text(encoding="utf-8")))
        assert extracted.source_url == "https://admissions.giki.edu.pk"
        assert extracted.institution_id == "giki"
        assert extracted.degree_level.value == "Undergraduate"
        assert extracted.deadline.value is not None
        assert extracted.fee.value is not None
        assert extracted.programs.value == ["BS"]
        assert extracted.constituent_college == NULL_FIELD

    def test_run_build_unclassified_chunk_defaults_to_ambiguous_no_signal(self, tmp_path):
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(scraped_dir, "giki.json")

        classified_path = tmp_path / "classified.json"
        classified_path.write_text(json.dumps({}), encoding="utf-8")

        out_dir = tmp_path / "extracted"
        run_build(scraped_dir, classified_path, out_dir)

        extracted = ExtractedRecord.from_dict(
            json.loads((out_dir / "giki.json").read_text(encoding="utf-8"))
        )
        assert extracted.degree_level.value is None
        assert extracted.degree_level.reason == "no-signal"

    def test_run_build_skips_errored_scraped_records(self, tmp_path):
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(
            scraped_dir, "broken.json",
            institution_id="broken", html=None, error="fetch failed: timeout",
        )
        classified_path = tmp_path / "classified.json"
        classified_path.write_text(json.dumps({}), encoding="utf-8")

        out_dir = tmp_path / "extracted"
        run_build(scraped_dir, classified_path, out_dir)

        assert not (out_dir / "broken.json").exists()
        assert list(out_dir.glob("*.json")) == []

    def test_run_build_excludes_postgraduate_records_from_output(self, tmp_path):
        # Project scope is undergrad-only: a Postgraduate-classified chunk
        # must never reach the extracted output directory at all.
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(scraped_dir, "giki.json")

        classified_path = tmp_path / "classified.json"
        classified_path.write_text(
            json.dumps({"Postgraduate": ["giki"]}), encoding="utf-8"
        )

        out_dir = tmp_path / "extracted"
        run_build(scraped_dir, classified_path, out_dir)

        assert not (out_dir / "giki.json").exists()
        assert list(out_dir.glob("*.json")) == []


# ---------------------------------------------------------------------------
# run.py — build_extracted_records (UG-only enforcement)
# ---------------------------------------------------------------------------

class TestBuildExtractedRecordsDegreeLevelFiltering:
    def _scraped_record(self, institution_id="giki", **overrides):
        record = {
            "institution_id": institution_id,
            "campus": None,
            "source_url": "https://admissions.giki.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Last date to apply: 10 August 2026.</p>",
            "pdfs": [],
        }
        record.update(overrides)
        return record

    def test_postgraduate_chunk_excluded_and_counted(self):
        records = [self._scraped_record()]
        degree_levels = {"giki": DegreeLevel(value="Postgraduate")}

        built, skipped, excluded_postgraduate = build_extracted_records(records, degree_levels)

        assert built == []
        assert skipped == 0
        assert excluded_postgraduate == 1

    def test_undergraduate_chunk_included(self):
        records = [self._scraped_record()]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}

        built, skipped, excluded_postgraduate = build_extracted_records(records, degree_levels)

        assert len(built) == 1
        assert built[0][1].degree_level.value == "Undergraduate"
        assert excluded_postgraduate == 0

    def test_ambiguous_chunk_included_not_treated_as_postgraduate(self):
        # CLAUDE.md hard rule 5: Ambiguous is a distinct, reviewable outcome,
        # not the same failure type as Postgraduate -- it must stay in the
        # output (unlike Postgraduate) so it can be inspected via its reason
        # code, even though the dashboard hides it from the default view.
        records = [self._scraped_record()]
        degree_levels = {"giki": DegreeLevel(value=None, reason="mixed-ug-pg-content")}

        built, skipped, excluded_postgraduate = build_extracted_records(records, degree_levels)

        assert len(built) == 1
        assert built[0][1].degree_level.value is None
        assert built[0][1].degree_level.reason == "mixed-ug-pg-content"
        assert excluded_postgraduate == 0

    def test_mixed_batch_only_excludes_postgraduate(self):
        records = [
            self._scraped_record(institution_id="giki"),
            self._scraped_record(institution_id="nums"),
            self._scraped_record(institution_id="uhs"),
        ]
        degree_levels = {
            "giki": DegreeLevel(value="Undergraduate"),
            "nums": DegreeLevel(value="Postgraduate"),
            "uhs": DegreeLevel(value=None, reason="no-signal"),
        }

        built, skipped, excluded_postgraduate = build_extracted_records(records, degree_levels)

        built_ids = {chunk_id for chunk_id, _ in built}
        assert built_ids == {"giki", "uhs"}
        assert excluded_postgraduate == 1
