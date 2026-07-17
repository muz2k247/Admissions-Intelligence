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

    def test_to_dict_omits_nulls(self):
        f = Field(value="10 Aug", confidence=0.8, note="date note")
        d = f.to_dict()
        assert d == {"value": "10 Aug", "confidence": 0.8, "note": "date note"}
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
            "Please note the application deadline: 10 August 2026 firmly."
        )
        f = extract_deadline(text)
        assert f.value is not None
        assert f.confidence == 0.95

    def test_single_mention_lower_confidence(self):
        text = "Last date to apply: 10 August 2026."
        f = extract_deadline(text)
        assert f.confidence == 0.85

    def test_conflicting_deadlines_yield_null_field_not_a_guess(self):
        # Two PRIMARY-keyword mentions with genuinely different dates -- a
        # real conflict between two equally-authoritative signals, not the
        # false-conflict-with-an-unrelated-deadline pattern (see
        # TestExtractDeadlinePrimaryKeywordTiering below).
        text = (
            "Last date to apply: 10 August 2026. "
            "Application deadline: 15 September 2026."
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
# fields.py — extract_deadline: plausibility validation (extraction/normalize.py
# validate_deadline_value integration). An implausible year (typo, hallucination,
# stray unrelated date) must null the whole field with a note, not be extracted
# as if it were trustworthy -- see extraction/fields.py's use of
# validate_deadline_value in both the single-match and multi-label branches.
# ---------------------------------------------------------------------------

class TestExtractDeadlinePlausibilityValidation:
    def test_implausibly_old_year_nulls_the_field(self):
        text = "Application deadline: 10 August 1998 for all programs."
        f = extract_deadline(text)
        assert f.value is None
        assert f.confidence is None
        assert f.note is not None
        assert "implausible" in f.note.lower()

    def test_implausibly_future_year_nulls_the_field(self):
        text = "Application deadline: 10 August 2099 for all programs."
        f = extract_deadline(text)
        assert f.value is None
        assert f.confidence is None
        assert f.note is not None
        assert "implausible" in f.note.lower()

    def test_valid_near_term_deadline_still_extracts_normally(self):
        # Regression check: the plausibility check must not break the
        # existing happy path for a normal near-future deadline.
        text = "Application deadline: 10 August 2026 for all programs."
        f = extract_deadline(text)
        assert f.value is not None
        assert "2026" in f.value or "August" in f.value
        assert f.confidence == 0.85
        assert f.note is None

    def test_one_implausible_date_among_two_labeled_candidates_nulls_whole_field(self):
        # A genuine multi-label case (two distinct tracks/programs) where one
        # of the two dates is implausible -- the whole field must null, not
        # just drop the bad entry and keep the other as a single value.
        text = (
            r"NUST Entry Test (Series-4) Application Form" "\n"
            r"Last Date: 18 Jun 2026" "\n"
            r"ACT/SAT Basis" "\n"
            r"Application Form" "\n"
            r"Last Date: 25 Jul 1998"
        )
        f = extract_deadline(text)
        assert f.value is None
        assert f.confidence is None
        assert f.note is not None
        assert "implausible" in f.note.lower()

    def test_both_plausible_labeled_candidates_still_resolve_to_list(self):
        # Regression check: two plausible distinct dates must still resolve
        # to the labeled list, not be affected by the new validation step.
        text = (
            r"NUST Entry Test (Series-4) Application Form" "\n"
            r"Last Date: 18 Jun 2026" "\n"
            r"ACT/SAT Basis" "\n"
            r"Application Form" "\n"
            r"Last Date: 25 Jul 2026"
        )
        f = extract_deadline(text)
        assert isinstance(f.value, list)
        assert len(f.value) == 2
        assert f.confidence == 0.75


# ---------------------------------------------------------------------------
# fields.py — extract_deadline: primary/secondary keyword
# tiering (fixes false conflicts between the real application deadline
# and an unrelated same-page deadline, without suppressing genuine
# conflicts -- root-caused against real scraped GIKI/UET-Taxila/NUST text,
# see extraction/fields.py's _DEADLINE_KEYWORDS_PRIMARY
# comments)
# ---------------------------------------------------------------------------

class TestExtractDeadlinePrimaryKeywordTiering:
    def test_primary_deadline_wins_over_unrelated_secondary_deadline(self):
        # Real pattern from GIKI: "Application Deadline" (the real one) vs
        # "Last Date for Receipt of Financial Assistance Documents" (a
        # different administrative step) -- both match generic "deadline"/
        # "last date" keywords, but only the first is actually the
        # application deadline.
        text = (
            "Application Deadline\n15-June-2026\n"
            "Last Date for Receipt of Financial Assistance Documents\n20-June-2026"
        )
        f = extract_deadline(text)
        assert f.value == "15-June-2026"
        assert f.confidence == 0.85
        assert f.note is None

    def test_single_primary_match_ignores_unrelated_secondary_entirely(self):
        text = "Application deadline: 1 January 2027. Some other last date: 5 February 2027 for a different thing."
        f = extract_deadline(text)
        assert f.value is not None
        assert "2027" in f.value
        assert f.confidence == 0.85


# ---------------------------------------------------------------------------
# fields.py — extract_deadline: labeled multi-deadline resolution.
# When multiple genuinely different deadlines exist (e.g. different entry-
# test tracks or BS programs), list each against its own page-authored
# label instead of nulling — but only when that's honestly recoverable,
# never a guess (see extraction/fields.py's _nearby_label/_MAX_LABELED_*
# comments). Root-caused against real scraped NUST (genuine, resolvable
# case) and UHS (schedule-table dump, must NOT resolve) data.
# ---------------------------------------------------------------------------

class TestExtractDeadlineLabeledMultiResolution:
    def test_two_labeled_tracks_resolve_to_list_not_null(self):
        # Real pattern from NUST: two different entry-test tracks, each
        # with its own genuinely different "Last Date:", each preceded by
        # a real page heading naming the track.
        text = (
            "NUST Entry Test (Series-4) Application Form\nLast Date: 18 Jun 2026\n"
            "ACT/SAT Basis\nApplication Form\nLast Date: 25 Jul 2026"
        )
        f = extract_deadline(text)
        assert f.value == [
            {"label": "NUST Entry Test (Series-4) Application Form", "date": "18 Jun 2026"},
            {"label": "ACT/SAT Basis Application Form", "date": "25 Jul 2026"},
        ]
        assert f.confidence == 0.75
        assert "multiple distinct deadlines" in f.note.lower()

    def test_generic_program_labels_still_generalizes_beyond_nust(self):
        # Same mechanism must work for any institution, not just NUST --
        # no per-institution logic exists, so a differently-worded pair of
        # program headings must resolve the same way.
        text = (
            "BS Computer Science\nApplication deadline: 1 August 2026\n"
            "BS Electrical Engineering\nApplication deadline: 15 August 2026"
        )
        f = extract_deadline(text)
        assert isinstance(f.value, list)
        assert len(f.value) == 2
        labels = {entry["label"] for entry in f.value}
        assert labels == {"BS Computer Science", "BS Electrical Engineering"}

    def test_no_distinguishing_label_stays_null(self):
        # Two conflicting dates with no usable preceding text at all (label
        # extraction finds nothing) -- must stay the honest null, not guess.
        text = "Last date: 10 August 2026. Last date: 15 September 2026."
        f = extract_deadline(text)
        assert f.value is None
        assert "conflicting" in f.note.lower()

    def test_identical_labels_stay_null(self):
        # Two candidates whose nearby text is identical (can't actually
        # distinguish them) must not be shown as two suspiciously-identical
        # "labeled" entries.
        text = (
            "Fall 2026 Admissions\nLast date: 10 August 2026.\n"
            "Fall 2026 Admissions\nLast date: 15 September 2026."
        )
        f = extract_deadline(text)
        assert f.value is None
        assert "conflicting" in f.note.lower()

    def test_too_many_candidates_stays_null_not_a_schedule_table_dump(self):
        # Real pattern from UHS: an admissions schedule table with many
        # different milestone dates (application start, closing, merit
        # list, across several cycles) -- not "different programs," just
        # page noise. Must stay null rather than show a long garbled list.
        text = "\n".join(
            f"Cycle {i} Online Application Schedule\nLast date: {10 + i} August 2026."
            for i in range(5)
        )
        f = extract_deadline(text)
        assert f.value is None
        assert "conflicting" in f.note.lower()

    def test_overlong_label_stays_null(self):
        # A label recovered from the page can be real text and still be
        # too long/messy to be a genuine short program heading (real UHS
        # labels ran 80-140+ chars from concatenated schedule-row text).
        long_label = "Description Date Online Application State Date 4th February online application processing schedule details"
        text = (
            f"{long_label}\nLast date: 10 August 2026.\n"
            "BS Computer Science\nLast date: 15 September 2026."
        )
        f = extract_deadline(text)
        assert f.value is None
        assert "conflicting" in f.note.lower()



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

    def test_pdf_produces_its_own_chunk_with_its_own_source_url(self):
        # Each PDF gets its own chunk with the PDF's own source_url, not
        # the page's -- a fact extracted from this chunk must be attributed
        # to the actual document it came from (CLAUDE.md hard rule 4).
        record = {
            "institution_id": "pu",
            "campus": None,
            "source_url": "https://pu.edu.pk/admissions",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Main page content.</p>",
            "pdfs": [{"url": "https://pu.edu.pk/x.pdf", "text": "Merit list PDF content."}],
        }
        chunks = chunk_scraped_record(record)
        assert len(chunks) == 2

        html_chunk = next(c for c in chunks if c.source_url == "https://pu.edu.pk/admissions")
        assert html_chunk.raw_text.strip() == "Main page content."

        pdf_chunk = next(c for c in chunks if c.source_url == "https://pu.edu.pk/x.pdf")
        assert pdf_chunk.raw_text == "Merit list PDF content."
        assert pdf_chunk.id != html_chunk.id
        assert pdf_chunk.institution_id == "pu"
        assert pdf_chunk.campus is None

    def test_multiple_pdfs_each_produce_a_distinct_chunk(self):
        record = {
            "institution_id": "uhs",
            "campus": None,
            "source_url": "https://uhs.edu.pk/admissions.php",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Landing page.</p>",
            "pdfs": [
                {"url": "https://uhs.edu.pk/notices/merit1.pdf", "text": "Merit list 1."},
                {"url": "https://uhs.edu.pk/notices/merit2.pdf", "text": "Merit list 2."},
                {"url": "https://uhs.edu.pk/notices/datesheet.pdf", "text": "Date sheet."},
            ],
        }
        chunks = chunk_scraped_record(record)
        assert len(chunks) == 4  # 1 html + 3 pdfs

        pdf_chunks = [c for c in chunks if c.source_url != record["source_url"]]
        assert len(pdf_chunks) == 3
        assert {c.source_url for c in pdf_chunks} == {p["url"] for p in record["pdfs"]}
        # every chunk id is distinct -- no accidental collisions
        assert len({c.id for c in chunks}) == 4

    def test_pdf_chunk_id_matches_hand_computed_hash(self):
        # Pins the actual algorithm (sha256 of scheme+netloc+path, not just
        # self-consistency within one process) -- a regression that swapped
        # in a process-salted hash (e.g. Python's built-in hash()) would
        # still pass a same-process "call it twice and compare" test, since
        # that kind of salting is consistent within a single interpreter
        # session but not across separate pipeline runs.
        import hashlib as _hashlib

        record = {
            "institution_id": "pu",
            "campus": None,
            "source_url": "https://pu.edu.pk/admissions",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Main page content.</p>",
            "pdfs": [{"url": "https://pu.edu.pk/notices/merit_list.pdf", "text": "Merit list content."}],
        }
        chunks = chunk_scraped_record(record)
        pdf_chunk = next(c for c in chunks if c.source_url.endswith(".pdf"))

        expected_digest = _hashlib.sha256(b"https://pu.edu.pk/notices/merit_list.pdf").hexdigest()[:10]
        assert pdf_chunk.id == f"pu__pdf_notices_merit_list_pdf_{expected_digest}"

    def test_pdf_chunk_id_ignores_volatile_query_string(self):
        # Real scraped PDF links (e.g. Punjab University's) carry
        # cache-busting query params like "?v=1783709854" that change on
        # every scrape for the same underlying document -- these must not
        # affect the id, or the stability guarantee is broken in practice
        # for exactly the sources (pu, uhs) this fix targets.
        record_a = {
            "institution_id": "pu", "campus": None, "source_url": "https://pu.edu.pk/admissions",
            "fetched_at": "2026-07-09T00:00:00Z", "html": "<p>Page.</p>",
            "pdfs": [{"url": "https://pu.edu.pk/faqs.pdf?v=1783709854", "text": "FAQ content."}],
        }
        record_b = {**record_a, "pdfs": [{"url": "https://pu.edu.pk/faqs.pdf?v=1783799999", "text": "FAQ content."}]}

        id_a = next(c.id for c in chunk_scraped_record(record_a) if c.source_url.startswith("https://pu.edu.pk/faqs"))
        id_b = next(c.id for c in chunk_scraped_record(record_b) if c.source_url.startswith("https://pu.edu.pk/faqs"))
        assert id_a == id_b

    def test_pdf_chunk_id_is_position_independent(self):
        # Two runs where a PDF moves position in the list (e.g. scrape order
        # changed) must still produce the same id for the same PDF URL --
        # chunk_id is the key curator overrides are stored against, so a
        # positional id would silently orphan those corrections.
        record_a = {
            "institution_id": "pu", "campus": None, "source_url": "https://pu.edu.pk/admissions",
            "fetched_at": "2026-07-09T00:00:00Z", "html": "<p>Page.</p>",
            "pdfs": [
                {"url": "https://pu.edu.pk/a.pdf", "text": "A."},
                {"url": "https://pu.edu.pk/b.pdf", "text": "B."},
            ],
        }
        record_b = {**record_a, "pdfs": list(reversed(record_a["pdfs"]))}

        chunks_a = {c.source_url: c.id for c in chunk_scraped_record(record_a)}
        chunks_b = {c.source_url: c.id for c in chunk_scraped_record(record_b)}
        assert chunks_a == chunks_b

    def test_only_pdf_chunk_produced_when_html_is_empty(self):
        # A JS-gated page with no real HTML text (e.g. ist before the
        # headless-render fix) shouldn't produce a spurious empty HTML chunk
        # if a linked PDF still has real text.
        record = {
            "institution_id": "x",
            "campus": None,
            "source_url": "https://example.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "",
            "pdfs": [{"url": "https://example.edu.pk/notice.pdf", "text": "Notice content."}],
        }
        chunks = chunk_scraped_record(record)
        assert len(chunks) == 1
        assert chunks[0].source_url == "https://example.edu.pk/notice.pdf"

    def test_pdf_with_only_whitespace_text_is_skipped(self):
        record = {
            "institution_id": "pu",
            "campus": None,
            "source_url": "https://pu.edu.pk/admissions",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Main page content.</p>",
            "pdfs": [{"url": "https://pu.edu.pk/blank.pdf", "text": "   \n  "}],
        }
        chunks = chunk_scraped_record(record)
        assert len(chunks) == 1
        assert chunks[0].source_url == "https://pu.edu.pk/admissions"

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
            "html": "<p>Last date to apply: 10 August 2026. BS programs offered.</p>",
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


class TestBuildExtractedRecordsNoiseFilter:
    """Phase T Task 5.1: drop all-null records, dedup same-content records
    sharing (institution_id, campus), preferring a priority_chunk_ids
    survivor (an existing Firestore override/review_decision) over the
    lowest-chunk_id tiebreak."""

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

    def test_all_null_record_dropped_and_counted(self):
        # No deadline/programs/constituent_college/admissions_open signal
        # anywhere in the text -- every REVIEW_FIELDS value is null, so hard
        # rule 1 makes this a safe drop (asserting no value, not altering one).
        records = [self._scraped_record(html="<p>Some announcement text with no deadline keyword.</p>")]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(records, degree_levels, stats=stats)

        assert built == []
        assert stats["dropped_all_null"] == 1
        assert stats["deduplicated"] == 0

    def test_record_with_any_review_field_set_is_kept(self):
        records = [self._scraped_record()]  # HTML has an extractable deadline
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(records, degree_levels, stats=stats)

        assert len(built) == 1
        assert stats["dropped_all_null"] == 0

    def test_duplicate_content_within_same_institution_campus_deduplicated_to_lowest_chunk_id(self):
        # An HTML chunk (id "giki") and a PDF mirror of the exact same text
        # (id "giki__pdf_...") -- both extract identical field values, so
        # they dedup to one survivor. "giki" sorts lower than "giki__pdf_...".
        records = [
            self._scraped_record(pdfs=[{"url": "https://admissions.giki.edu.pk/notice.pdf", "text": "Last date to apply: 10 August 2026."}])
        ]
        # Both chunk_ids classified the same -- degree_level.value is part of
        # the dedup group key, so a divergent classification here would
        # (correctly) stop them from being seen as duplicates at all.
        chunk_ids = [c.id for c in chunk_scraped_record(records[0])]
        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(records, degree_levels, stats=stats)

        assert [chunk_id for chunk_id, _ in built] == ["giki"]
        assert stats["deduplicated"] == 1
        assert stats["dropped_all_null"] == 0

    def test_priority_chunk_id_wins_dedup_tiebreak_over_lower_chunk_id(self):
        # Same duplicate-content setup as above, but the PDF twin (the
        # higher-sorting chunk_id) has an existing Firestore override --
        # losing it to the plain lowest-chunk_id rule would silently orphan
        # a curator's correction, so priority must override the tiebreak.
        records = [
            self._scraped_record(pdfs=[{"url": "https://admissions.giki.edu.pk/notice.pdf", "text": "Last date to apply: 10 August 2026."}])
        ]
        chunk_ids = [c.id for c in chunk_scraped_record(records[0])]
        degree_levels = {cid: DegreeLevel(value="Undergraduate") for cid in chunk_ids}
        pdf_chunk_id = next(cid for cid in chunk_ids if cid != "giki")
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(
            records, degree_levels, stats=stats, priority_chunk_ids=frozenset({pdf_chunk_id})
        )

        assert [chunk_id for chunk_id, _ in built] == [pdf_chunk_id]
        assert stats["deduplicated"] == 1

    def test_different_institutions_with_identical_content_not_deduplicated(self):
        records = [
            self._scraped_record(institution_id="giki"),
            self._scraped_record(institution_id="nums"),
        ]
        degree_levels = {
            "giki": DegreeLevel(value="Undergraduate"),
            "nums": DegreeLevel(value="Undergraduate"),
        }
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(records, degree_levels, stats=stats)

        assert {chunk_id for chunk_id, _ in built} == {"giki", "nums"}
        assert stats["deduplicated"] == 0

    def test_priority_vs_priority_tie_resolves_to_lowest_chunk_id_not_insertion_order(self):
        # Regression test: when two candidates in the same dedup group are
        # BOTH in priority_chunk_ids, the tiebreak must still fall through to
        # lowest-chunk_id rather than "whichever was inserted into `built`
        # first" -- those two things aren't the same thing in general (PDF
        # chunk_ids are derived from a URL hash, not insertion order). These
        # two PDF URLs are picked (verified via extraction.chunker._pdf_chunk_id)
        # so the SECOND-inserted PDF's chunk_id sorts LOWER than the first --
        # an insertion-order-based tiebreak would pick the wrong one here.
        record = self._scraped_record(
            pdfs=[
                {"url": "https://x1.giki.edu.pk/n1.pdf", "text": "Last date to apply: 10 August 2026."},
                {"url": "https://x10.giki.edu.pk/n10.pdf", "text": "Last date to apply: 10 August 2026."},
            ]
        )
        chunks = chunk_scraped_record(record)
        pdf_chunks = [c for c in chunks if c.id != "giki"]
        assert len(pdf_chunks) == 2
        inserted_first, inserted_second = pdf_chunks[0].id, pdf_chunks[1].id
        assert inserted_second < inserted_first, "fixture must have insertion order reversed from chunk_id sort order"

        degree_levels = {c.id: DegreeLevel(value="Undergraduate") for c in chunks}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(
            [record], degree_levels, stats=stats,
            priority_chunk_ids=frozenset({inserted_first, inserted_second}),
        )

        assert [chunk_id for chunk_id, _ in built] == [inserted_second]

    def test_different_content_within_same_institution_not_deduplicated(self):
        records = [
            self._scraped_record(
                pdfs=[{"url": "https://admissions.giki.edu.pk/notice.pdf", "text": "Last date to apply: 20 September 2026."}]
            )
        ]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(records, degree_levels, stats=stats)

        assert len(built) == 2
        assert stats["deduplicated"] == 0

    def test_priority_chunk_id_exempt_from_all_null_drop(self):
        # Code-reviewer regression (Phase T Task 5.1 follow-up): a chunk_id
        # with an existing Firestore override/review_decision must survive
        # the all-null drop even when its OWN raw extraction is entirely
        # null -- otherwise it never reaches extracted/*.json for stage 5's
        # merge_overrides() to apply the correction to, silently orphaning
        # it. This is the exact failure priority_chunk_ids exists to
        # prevent, so it must apply to the drop step too, not just dedup.
        records = [self._scraped_record(html="<p>Some announcement text with no deadline keyword.</p>")]
        degree_levels = {"giki": DegreeLevel(value="Undergraduate")}
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(
            records, degree_levels, stats=stats, priority_chunk_ids=frozenset({"giki"})
        )

        assert [chunk_id for chunk_id, _ in built] == ["giki"]
        assert built[0][1].deadline.value is None  # still honestly null -- not fabricated
        assert stats["dropped_all_null"] == 0

    def test_non_priority_all_null_chunk_still_dropped_alongside_priority_one(self):
        # A priority chunk_id's exemption must not leak into a DIFFERENT,
        # non-priority all-null chunk in the same batch.
        records = [
            self._scraped_record(institution_id="giki", html="<p>Some announcement text with no deadline keyword.</p>"),
            self._scraped_record(institution_id="nums", html="<p>Some announcement text with no deadline keyword.</p>"),
        ]
        degree_levels = {
            "giki": DegreeLevel(value="Undergraduate"),
            "nums": DegreeLevel(value="Undergraduate"),
        }
        stats: dict[str, int] = {}

        built, _, _ = build_extracted_records(
            records, degree_levels, stats=stats, priority_chunk_ids=frozenset({"giki"})
        )

        assert [chunk_id for chunk_id, _ in built] == ["giki"]
        assert stats["dropped_all_null"] == 1


# ---------------------------------------------------------------------------
# fields.py — extract_admissions_open (Phase P chunk 2)
# ---------------------------------------------------------------------------

from extraction.fields import extract_admissions_open  # noqa: E402


class TestExtractAdmissionsOpen:
    def test_empty_text_returns_null_field(self):
        assert extract_admissions_open("") == NULL_FIELD
        assert extract_admissions_open(None) == NULL_FIELD

    def test_only_open_phrase_returns_open(self):
        text = "Applications are open for Fall 2026 admissions."
        f = extract_admissions_open(text)
        assert f.value == "Open"
        assert f.confidence == 0.8
        assert f.note is None

    def test_only_closed_phrase_returns_closed(self):
        text = "Admissions closed for the Fall 2026 intake."
        f = extract_admissions_open(text)
        assert f.value == "Closed"
        assert f.confidence == 0.8
        assert f.note is None

    def test_both_open_and_closed_phrases_conflict_nulls_with_note(self):
        text = (
            "Applications are open for Spring 2027. "
            "Admissions closed for Fall 2026 cycle."
        )
        f = extract_admissions_open(text)
        assert f.value is None
        assert f.confidence is None
        assert f.note is not None
        assert "conflict" in f.note.lower()

    def test_neither_phrase_present_nulls_with_no_note(self):
        # Critical: absence of signal must never be treated as "Closed" --
        # this is "no signal", not a negative finding, so note must be
        # exactly None, not an empty string or any other falsy stand-in.
        text = "Welcome to our university. Programs offered include BS Computer Science."
        f = extract_admissions_open(text)
        assert f.value is None
        assert f.confidence is None
        assert f.note is None

    def test_value_is_literal_string_not_boolean(self):
        f = extract_admissions_open("Applications are open for the upcoming semester.")
        assert f.value == "Open"
        assert isinstance(f.value, str)
        assert f.value is not True


# ---------------------------------------------------------------------------
# fields.py — extract_admissions_open: phrase-coverage follow-up (post-
# verification precision tuning of the Phase P chunk 2 heuristic)
# ---------------------------------------------------------------------------

class TestExtractAdmissionsOpenNewPhrases:
    """Each newly-added phrase, one representative sentence per phrase family
    (not every literal entry) -- mechanical tense/subject variants of the
    already-trusted "X are open"/"X closed" structures."""

    def test_application_is_open_singular(self):
        assert extract_admissions_open("Application is open for BS Computer Science.").value == "Open"

    def test_admissions_open_bare_plural(self):
        # Previously a real gap -- only the singular "admission open" existed.
        assert extract_admissions_open("Admissions open for the Fall 2026 intake.").value == "Open"

    def test_admissions_are_now_open(self):
        assert extract_admissions_open("Admissions are now open for the Fall 2026 semester.").value == "Open"

    def test_applications_now_open(self):
        assert extract_admissions_open("Applications now open for all engineering programs.").value == "Open"

    def test_admissions_remain_open(self):
        assert extract_admissions_open("Admissions remain open until seats are filled.").value == "Open"

    def test_admissions_have_opened(self):
        assert extract_admissions_open("Admissions have opened for the new academic year.").value == "Open"

    def test_admissions_have_commenced(self):
        # Only the "admissions"-subject form is kept -- "applications have
        # commenced" is excluded (see TestExtractAdmissionsOpenExclusions).
        assert extract_admissions_open("Admissions have commenced for the new academic year.").value == "Open"

    def test_admissions_have_closed(self):
        # Existing asymmetry: "applications have closed" existed, "admissions
        # have closed" didn't.
        assert extract_admissions_open("Admissions have closed for this cycle.").value == "Closed"

    def test_admissions_are_no_longer_open(self):
        assert extract_admissions_open("Admissions are no longer open for Fall 2026.").value == "Closed"

    def test_applications_have_ended(self):
        assert extract_admissions_open("Applications have ended for the Spring intake.").value == "Closed"

    def test_admission_portal_is_closed(self):
        assert extract_admissions_open("The admission portal is closed until next cycle.").value == "Closed"


class TestExtractAdmissionsOpenExclusions:
    """Phrases deliberately NOT added, per the module-level comment above the
    phrase lists -- confirms the exclusions actually hold at runtime."""

    def test_apply_now_no_longer_matches(self):
        # Generic marketing language, removed as a precision fix.
        f = extract_admissions_open("Apply now for the upcoming semester.")
        assert f.value is None
        assert f.confidence is None

    def test_applications_are_invited_is_not_recognized(self):
        # Structurally identical to scholarship/job/tender language; left to
        # the LLM field-extractor's contextual judgment.
        f = extract_admissions_open("Applications are invited from intending candidates for BS programs.")
        assert f.value is None

    def test_applications_invited_headline_form_not_recognized(self):
        f = extract_admissions_open("Applications Invited for Fall 2026 Admissions")
        assert f.value is None

    def test_register_now_stays_null(self):
        f = extract_admissions_open("Register now to secure your spot in our workshop.")
        assert f.value is None

    def test_enrol_now_stays_null(self):
        f = extract_admissions_open("Enrol now and start your journey with us.")
        assert f.value is None

    def test_join_us_stays_null(self):
        f = extract_admissions_open("Join us for an exciting academic year ahead.")
        assert f.value is None

    def test_applications_have_commenced_stays_null(self):
        # Unlike "admissions have commenced" (kept), "applications" alone has
        # the same scholarship/job/tender ambiguity as "applications are
        # invited".
        f = extract_admissions_open("Scholarship applications have commenced for eligible students.")
        assert f.value is None

    def test_registration_is_open_no_longer_matches(self):
        # Removed: as generic as "apply now" -- course/event registration,
        # not necessarily admissions.
        f = extract_admissions_open("Course registration is open for continuing students.")
        assert f.value is None

    def test_registration_closed_no_longer_matches(self):
        f = extract_admissions_open("Registration closed for the workshop.")
        assert f.value is None


class TestExtractAdmissionsOpenScheduledDateFalsePositive:
    """"Open from/on [date]" states a scheduled date, not a current status --
    the pattern can't tell whether that date is past or future, so it must
    null rather than guess "Open". But the lookahead requires an actual
    date-shaped continuation, so ordinary non-date uses of "on" (location,
    scope) must NOT be suppressed."""

    def test_open_on_date_nulls(self):
        f = extract_admissions_open("Admissions open on August 1, 2026.")
        assert f.value is None
        assert f.confidence is None

    def test_open_from_date_nulls(self):
        f = extract_admissions_open("Applications open from September 15, 2026.")
        assert f.value is None

    def test_open_from_iso_date_nulls(self):
        f = extract_admissions_open("Admissions open from 2026-08-15.")
        assert f.value is None

    def test_admissions_open_from_colon_ordinal_info_box_style_nulls(self):
        # Regression check for the ordinal-suffix boundary bug: "1st" is a
        # digit directly followed by word-char letters, so a trailing \b
        # right after the digit would never hold and silently defeat this
        # whole branch if reintroduced.
        f = extract_admissions_open("Admissions Open From: 1st August 2026")
        assert f.value is None

    def test_plain_open_without_from_or_on_still_matches(self):
        # Regression check: the lookahead must not over-suppress the
        # ordinary bare-phrase match when no scheduling word follows.
        f = extract_admissions_open("Admissions open for all BS programs this year.")
        assert f.value == "Open"

    def test_open_followed_by_unrelated_word_still_matches(self):
        # "on"/"from" only excluded when followed by something date-shaped --
        # confirms the lookahead isn't accidentally too broad.
        f = extract_admissions_open("Admissions open now, apply before seats fill up.")
        assert f.value == "Open"

    def test_open_on_campus_not_a_date_still_matches(self):
        # "on" introducing a location/scope, not a schedule -- must NOT be
        # suppressed (this was a real false negative in an earlier version
        # of the lookahead that excluded any "on" unconditionally).
        f = extract_admissions_open("Admissions are open on all campuses.")
        assert f.value == "Open"

    def test_open_on_the_following_programs_not_a_date_still_matches(self):
        f = extract_admissions_open("Admission is open on the following programs.")
        assert f.value == "Open"

    def test_open_from_today_not_a_scheduled_future_date_still_matches(self):
        # "from today" unambiguously means starting now, unlike "from
        # [future date]" -- the narrower date-shaped lookahead correctly
        # lets this one through where the earlier blanket "from" exclusion
        # would not have.
        f = extract_admissions_open("Applications are open from today.")
        assert f.value == "Open"


class TestExtractAdmissionsOpenTenseEdgeCases:
    """Future-tense and historical phrasing must stay null -- confirms these
    already-correct cases still hold after the phrase-list expansion above
    (more phrases = more chances for an unintended new match)."""

    def test_admissions_will_open_future_tense_nulls(self):
        assert extract_admissions_open("Admissions will open on August 1, 2026.").value is None

    def test_admission_opens_next_month_future_tense_nulls(self):
        assert extract_admissions_open("Admission opens next month, stay tuned for updates.").value is None

    def test_applications_begin_on_future_tense_nulls(self):
        assert extract_admissions_open("Applications begin on September 1, 2026.").value is None

    def test_admissions_were_open_historical_nulls(self):
        assert extract_admissions_open("Admissions were open last year for this program.").value is None

    def test_admissions_opened_last_year_simple_past_nulls(self):
        # Simple past ("opened", no "have") is tense-ambiguous -- deliberately
        # not in the phrase list, unlike "admissions have opened".
        assert extract_admissions_open("Admissions opened last year and the cycle has since ended.").value is None


class TestExtractAdmissionsOpenConflictWithNewPhrases:
    def test_new_open_phrase_conflicts_with_new_closed_phrase(self):
        text = "Admissions remain open for Spring 2027. Applications have closed for Fall 2026."
        f = extract_admissions_open(text)
        assert f.value is None
        assert f.note is not None
        assert "conflict" in f.note.lower()

    def test_multiple_cycles_one_open_one_closed_on_same_page_nulls(self):
        # Known simplification (see extract_admissions_open's docstring): no
        # per-program breakdown, unlike extract_deadline's labeled multi-
        # track list -- a mixed page still nulls the whole field.
        text = (
            "MBBS admissions are closed for this cycle. "
            "BS Computer Science admissions are now open."
        )
        f = extract_admissions_open(text)
        assert f.value is None


# ---------------------------------------------------------------------------
# schema.py — ExtractedRecord.admissions_open (Phase P chunk 2)
# ---------------------------------------------------------------------------

class TestExtractedRecordAdmissionsOpen:
    def _make_record(self, admissions_open=None):
        kwargs = dict(
            institution_id="giki",
            campus=None,
            source_url="https://admissions.giki.edu.pk",
            fetched_at="2026-07-09T00:00:00Z",
            chunk_id="giki",
            degree_level=DegreeLevel(value="Undergraduate"),
            constituent_college=NULL_FIELD,
            deadline=Field(value="10 Aug 2026", confidence=0.85),
            programs=Field(value=["BS"], confidence=0.6),
        )
        if admissions_open is not None:
            kwargs["admissions_open"] = admissions_open
        return ExtractedRecord(**kwargs)

    def test_default_admissions_open_is_null_field_when_not_passed(self):
        record = self._make_record()
        assert record.admissions_open == NULL_FIELD

    def test_round_trip_with_non_null_admissions_open(self):
        record = self._make_record(admissions_open=Field(value="Open", confidence=0.8))
        d = record.to_dict()
        assert d["admissions_open"] == {"value": "Open", "confidence": 0.8, "note": None}
        record2 = ExtractedRecord.from_dict(d)
        assert record2 == record
        assert record2.admissions_open.value == "Open"

    def test_from_dict_missing_admissions_open_key_defaults_to_null(self):
        minimal = {
            "institution_id": "giki",
            "source_url": "https://admissions.giki.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "chunk_id": "giki",
        }
        record = ExtractedRecord.from_dict(minimal)
        assert record.admissions_open == NULL_FIELD
