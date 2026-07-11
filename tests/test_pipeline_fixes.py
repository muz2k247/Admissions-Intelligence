"""QA tests for three targeted correctness fixes:

1. pipeline/run_full.py stage_4_build — stale-output cleanup: a leftover
   extracted-record JSON file for an institution no longer present in the
   current scraped-data batch must not survive a stage_4_build run.
2. extraction/chunker.py _html_to_text — switched from the stdlib
   html.parser to lxml because html.parser silently mis-nests real-world
   malformed markup (e.g. an unclosed <title> tag ahead of nested Bootstrap
   modal markup), swallowing the rest of the document as literal RCDATA text
   instead of parsing it, which drops/garbles visible text before extraction
   ever runs.
3. extraction/chunker.py _DATE_ATTR_PATTERN + chunk_scraped_record — some
   admissions pages only encode their real deadline in a machine-readable
   data-attribute (e.g. data-end-date="...") on a JS countdown widget, with
   no nearby plain-text keyword sentence for the keyword-anchored field
   extractor to find. The chunker now synthesizes a keyword-anchored sentence
   from that attribute so extract_deadline() can still find it.

Fixtures for the malformed-HTML scenario live under
tests/fixtures/generic/ (institution-agnostic behavior, not tied to any one
scraped source) per project QA conventions.

No live network calls. No original code modified.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.run_full import stage_4_build
from extraction.chunker import _html_to_text, chunk_scraped_record
from extraction.fields import extract_deadline

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "generic"


# ---------------------------------------------------------------------------
# 1. Stale-file cleanup in stage_4_build
# ---------------------------------------------------------------------------

class TestStaleFileCleanup:
    def _write_scraped_record(self, scraped_dir: Path, filename: str, institution_id: str):
        scraped_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "institution_id": institution_id,
            "campus": None,
            "source_url": f"https://{institution_id}.example.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Last date to apply: 10 August 2026.</p>",
            "pdfs": [],
            "error": None,
        }
        (scraped_dir / filename).write_text(json.dumps(record), encoding="utf-8")

    def test_leftover_json_for_absent_institution_is_deleted(self, tmp_path):
        scraped_dir = tmp_path / "scraped"
        # Current batch only contains "giki" — NOT "foo".
        self._write_scraped_record(scraped_dir, "giki.json", "giki")

        classified_path = tmp_path / "classified.json"
        classified_path.write_text(json.dumps({"Undergraduate": ["giki"]}), encoding="utf-8")

        out_dir = tmp_path / "extracted"
        out_dir.mkdir(parents=True, exist_ok=True)
        stale_file = out_dir / "foo.json"
        stale_file.write_text(json.dumps({"institution_id": "foo"}), encoding="utf-8")
        assert stale_file.exists()  # sanity check before running

        rc = stage_4_build(scraped_dir, classified_path, out_dir)

        assert rc == 0
        assert not stale_file.exists(), "stale foo.json from a prior run must not survive stage_4_build"
        assert (out_dir / "giki.json").exists(), "current batch's record should still be written"

    def test_stale_cleanup_does_not_remove_non_json_files(self, tmp_path):
        """Cleanup should be scoped to *.json in the output dir, not a
        wholesale wipe — a stray non-JSON file (e.g. a README) should survive."""
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(scraped_dir, "giki.json", "giki")

        classified_path = tmp_path / "classified.json"
        classified_path.write_text(json.dumps({"Undergraduate": ["giki"]}), encoding="utf-8")

        out_dir = tmp_path / "extracted"
        out_dir.mkdir(parents=True, exist_ok=True)
        keep_file = out_dir / "README.txt"
        keep_file.write_text("not a json output", encoding="utf-8")

        stage_4_build(scraped_dir, classified_path, out_dir)

        assert keep_file.exists()

    def test_empty_out_dir_with_no_stale_files_still_succeeds(self, tmp_path):
        scraped_dir = tmp_path / "scraped"
        self._write_scraped_record(scraped_dir, "giki.json", "giki")

        classified_path = tmp_path / "classified.json"
        classified_path.write_text(json.dumps({"Undergraduate": ["giki"]}), encoding="utf-8")

        out_dir = tmp_path / "extracted"  # does not exist yet

        rc = stage_4_build(scraped_dir, classified_path, out_dir)

        assert rc == 0
        assert (out_dir / "giki.json").exists()


# ---------------------------------------------------------------------------
# 2. lxml parser correctness for _html_to_text
# ---------------------------------------------------------------------------

class TestLxmlParserCorrectness:
    def test_malformed_bootstrap_modal_html_extracted_cleanly(self):
        """Fixture reproduces a real-world defect: an unclosed <title> tag
        ahead of a nested Bootstrap modal. Under the stdlib html.parser this
        causes the rest of the document to be swallowed as literal RCDATA
        text of <title> (verified empirically: html.parser returns the
        entire remaining markup, including raw '<div', '<body', '</html>'
        substrings, as plain text rather than parsing it). lxml correctly
        resumes normal element parsing and extracts clean visible text."""
        html = (FIXTURES_DIR / "malformed_bootstrap_modal.html").read_text(encoding="utf-8")

        text = _html_to_text(html)

        # The deadline sentence buried inside the nested modal divs must be
        # present, and present as clean text — not accompanied by literal
        # tag markup that a broken parse would have left behind.
        assert "Last date to apply: 10 August 2026." in text
        assert "Admission Notice" in text
        assert "GIKI Admissions" in text
        assert "Menu" in text

        # No leftover raw tag markup — proves the whole document was parsed
        # as elements, not swallowed as literal RCDATA text of a single tag.
        for leaked_tag in ("<div", "<body", "<html", "<p>", "</div>", "<nav"):
            assert leaked_tag not in text, f"found leaked raw markup {leaked_tag!r} in extracted text"

    def test_html_to_text_still_strips_script_and_style(self):
        html = (
            "<html><body>"
            "<style>.x{color:red}</style>"
            "<script>var x = 1;</script>"
            "<p>Visible content only.</p>"
            "</body></html>"
        )
        text = _html_to_text(html)
        assert text == "Visible content only."

    def test_html_to_text_empty_and_none_input(self):
        assert _html_to_text(None) == ""
        assert _html_to_text("") == ""

    def test_basic_well_formed_html_unaffected_by_parser_change(self):
        """Regression guard: simple, well-formed markup (the common case)
        must extract identically regardless of which parser backs bs4."""
        html = "<html><body><p>Last date to apply: 10 August 2026.</p></body></html>"
        text = _html_to_text(html)
        assert text == "Last date to apply: 10 August 2026."


# ---------------------------------------------------------------------------
# 3. Data-attribute deadline extraction
# ---------------------------------------------------------------------------

class TestDataAttributeDeadlineExtraction:
    def test_data_end_date_attribute_with_no_nearby_keyword_is_extracted(self):
        record = {
            "institution_id": "fast",
            "campus": None,
            "source_url": "https://admissions.fast.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": (
                '<div class="countdown-widget" '
                'data-end-date="2026-07-15T00:00:00.0000000">'
                '<span class="days"></span><span class="hours"></span>'
                "</div>"
            ),
            "pdfs": [],
        }

        chunks = chunk_scraped_record(record)
        assert len(chunks) == 1
        chunk = chunks[0]

        # The synthetic keyword-anchored sentence must be present in the
        # chunk's raw_text so downstream extraction can find it.
        assert "2026-07-15" in chunk.raw_text

        field = extract_deadline(chunk.raw_text)
        assert field.value == "2026-07-15"
        assert field.confidence is not None
        assert 0.0 <= field.confidence <= 1.0

    @pytest.mark.parametrize("attr_name", ["data-end-date", "data-deadline"])
    def test_supported_data_attribute_names_are_recognized(self, attr_name):
        # Only deadline/end-oriented attribute names qualify. The bare
        # `data-date` is deliberately NOT supported (see the sibling test) —
        # it's too generic to assume it means an admissions deadline.
        html = f'<div {attr_name}="2026-08-20T12:00:00.0000000"></div>'
        record = {
            "institution_id": "x",
            "campus": None,
            "source_url": "https://example.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": html,
            "pdfs": [],
        }
        chunks = chunk_scraped_record(record)
        field = extract_deadline(chunks[0].raw_text)
        assert field.value == "2026-08-20"
        assert field.confidence is not None

    def test_generic_data_date_attribute_is_not_treated_as_deadline(self):
        # `data-date` is used by calendars, blog "published" stamps, event
        # dates, etc. Treating it as an admissions deadline would be inference
        # (hard rule 1), so the chunker must not inject it.
        record = {
            "institution_id": "x",
            "campus": None,
            "source_url": "https://example.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": '<div data-date="2026-08-20T12:00:00.0000000"></div>',
            "pdfs": [],
        }
        chunks = chunk_scraped_record(record)
        # No usable text at all -> no chunk emitted, or a chunk with no
        # deadline. Either way, no date must be surfaced.
        if chunks:
            assert extract_deadline(chunks[0].raw_text).value is None

    def test_data_attribute_deadline_reinforces_matching_plain_text(self):
        """When the widget date and the plain-text-stated date are the same
        real day (differently spelled), the extractor normalizes both and
        treats them as one reinforced value — NOT a false conflict."""
        record = {
            "institution_id": "fast",
            "campus": None,
            "source_url": "https://admissions.fast.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": (
                "<p>Last date to apply: 15 July 2026.</p>"
                '<div data-end-date="2026-07-15T00:00:00.0000000"></div>'
            ),
            "pdfs": [],
        }
        chunks = chunk_scraped_record(record)
        field = extract_deadline(chunks[0].raw_text)
        # "15 July 2026" and "2026-07-15" normalize to the same day, so this
        # is a single reinforced value, not a conflict.
        assert field.value is not None
        assert field.confidence == 0.95
        assert field.note is None

    def test_no_data_attribute_and_no_keyword_returns_null_field_no_crash(self):
        record = {
            "institution_id": "x",
            "campus": None,
            "source_url": "https://example.edu.pk",
            "fetched_at": "2026-07-09T00:00:00Z",
            "html": "<p>Nothing deadline related here.</p>",
            "pdfs": [],
        }
        chunks = chunk_scraped_record(record)
        field = extract_deadline(chunks[0].raw_text)
        assert field.value is None
        assert field.confidence is None
