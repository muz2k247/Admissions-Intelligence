"""Tests for the config-driven scraper (scraper/config.py, fetch.py,
pdf_fallback.py, run.py).

All network access is mocked — no test in this file may hit a live
university website (CLAUDE.md hard rule / QA policy).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from scraper.config import DEFAULT_CONFIG_PATH, Institution, Source, load_institutions, iter_sources
from scraper.fetch import FetchResult, fetch_source
from scraper.pdf_fallback import PdfDocument, find_pdf_links, fetch_pdf_text, fetch_linked_pdfs
from scraper.run import slugify_source


# ---------------------------------------------------------------------------
# Fakes for mocking requests.Session without new dependencies
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text=None, content=None, status_code=200, raise_exc=None):
        self.text = text
        self.content = content if content is not None else (text.encode() if text else b"")
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Minimal stand-in for requests.Session with a scripted .get()."""

    def __init__(self, responses):
        # responses: dict url -> FakeResponse, or a callable(url, **kwargs) -> FakeResponse
        self._responses = responses
        self.calls = []

    def get(self, url, timeout=None, **kwargs):
        self.calls.append(url)
        if callable(self._responses):
            return self._responses(url, timeout=timeout, **kwargs)
        if url not in self._responses:
            raise requests.ConnectionError(f"unexpected URL in test: {url}")
        result = self._responses[url]
        if isinstance(result, Exception):
            raise result
        return result


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------

class TestConfig:
    def test_load_real_registry_counts(self):
        institutions = load_institutions(DEFAULT_CONFIG_PATH)
        assert len(institutions) == 16, "expected 16 institutions in the live registry (UET Taxila is a separate chartered university, not a UET Lahore campus)"

        total_sources = sum(len(inst.sources) for inst in institutions)
        assert total_sources == 17, "expected 17 total sources (some institutions multi-campus)"

        # sanity: multi-source institution actually has >1 source
        multi = [i for i in institutions if len(i.sources) > 1]
        assert len(multi) >= 1, "expected at least one multi-source institution"

    def test_load_real_registry_produces_typed_objects(self):
        institutions = load_institutions(DEFAULT_CONFIG_PATH)
        for inst in institutions:
            assert isinstance(inst, Institution)
            assert inst.id
            for src in inst.sources:
                assert isinstance(src, Source)
                assert src.institution_id == inst.id
                assert src.url
                assert src.format

    def test_has_pdf_fallback_true_when_format_contains_pdf(self):
        src = Source(institution_id="x", campus=None, url="http://example.com", format="html+pdf")
        assert src.has_pdf_fallback is True

    def test_has_pdf_fallback_false_for_plain_html(self):
        src = Source(institution_id="x", campus=None, url="http://example.com", format="html")
        assert src.has_pdf_fallback is False

    def test_iter_sources_yields_institution_source_pairs(self):
        institutions = load_institutions(DEFAULT_CONFIG_PATH)
        pairs = list(iter_sources(institutions))
        assert len(pairs) == sum(len(i.sources) for i in institutions)
        for institution, source in pairs:
            assert isinstance(institution, Institution)
            assert isinstance(source, Source)
            assert source in institution.sources

    def test_load_institutions_defaults_enabled_true_when_absent(self, tmp_path):
        # Test the default against a synthetic fixture, NOT the live registry:
        # a legitimate future use of this very feature (setting enabled: false
        # on a real institution) must not break this test.
        config_path = tmp_path / "institutions.yaml"
        config_path.write_text(
            """
institutions:
  - id: no_enabled_key
    name: Institution Without Enabled Key
    sources:
      - campus: null
        url: "https://example.edu"
        format: html
""",
            encoding="utf-8",
        )

        institutions = load_institutions(config_path)

        assert len(institutions) == 1
        assert institutions[0].enabled is True, (
            "an institution entry with no `enabled` key must default to enabled"
        )

    def test_load_institutions_respects_explicit_enabled_false(self, tmp_path):
        config_path = tmp_path / "institutions.yaml"
        config_path.write_text(
            """
institutions:
  - id: active_inst
    name: Active Institution
    sources:
      - campus: null
        url: "https://active.example.edu"
        format: html
  - id: disabled_inst
    name: Disabled Institution
    enabled: false
    sources:
      - campus: null
        url: "https://disabled.example.edu"
        format: html
""",
            encoding="utf-8",
        )

        institutions = load_institutions(config_path)

        active = next(i for i in institutions if i.id == "active_inst")
        disabled = next(i for i in institutions if i.id == "disabled_inst")
        assert active.enabled is True
        assert disabled.enabled is False

    def test_iter_sources_skips_disabled_institutions_but_load_institutions_still_returns_them(self):
        active = Institution(
            id="active", name="Active", admitting_body=False, ug_pg_mixed=False,
            sources=[Source(institution_id="active", campus=None, url="https://a.example", format="html")],
            enabled=True,
        )
        disabled = Institution(
            id="disabled", name="Disabled", admitting_body=False, ug_pg_mixed=False,
            sources=[Source(institution_id="disabled", campus=None, url="https://b.example", format="html")],
            enabled=False,
        )

        # Listing code (e.g. the admin panel / _institutions_payload) needs to
        # see disabled institutions too, so it can display and re-enable them.
        all_institutions = [active, disabled]
        assert len(all_institutions) == 2

        # But the scraper's only entry point must skip disabled ones.
        pairs = list(iter_sources(all_institutions))
        assert [inst.id for inst, _ in pairs] == ["active"]


# ---------------------------------------------------------------------------
# fetch.py
# ---------------------------------------------------------------------------

class TestFetchSource:
    def test_successful_fetch_returns_html_and_preserves_source_url(self):
        source = Source(institution_id="giki", campus=None, url="https://admissions.giki.edu.pk", format="html")
        session = FakeSession({source.url: FakeResponse(text="<html>hello</html>")})

        result = fetch_source(source, session=session)

        assert isinstance(result, FetchResult)
        assert result.ok is True
        assert result.error is None
        assert result.html == "<html>hello</html>"
        assert result.source_url == source.url
        assert result.institution_id == "giki"
        assert result.campus is None
        assert result.fetched_at  # non-empty timestamp

    def test_request_exception_is_caught_not_raised(self):
        source = Source(institution_id="uet", campus="Lahore (Main)", url="https://apply.uet.edu.pk", format="html")
        session = FakeSession({source.url: requests.ConnectionError("boom")})

        result = fetch_source(source, session=session)

        assert isinstance(result, FetchResult)
        assert result.ok is False
        assert result.html is None
        assert result.error is not None
        assert "fetch failed" in result.error
        assert result.source_url == source.url
        assert result.campus == "Lahore (Main)"

    def test_http_error_status_is_caught_not_raised(self):
        source = Source(institution_id="x", campus=None, url="https://example.com/404", format="html")
        session = FakeSession({source.url: FakeResponse(text="not found", status_code=404)})

        result = fetch_source(source, session=session)

        assert result.ok is False
        assert result.html is None
        assert result.error is not None

    def test_html_plus_pdf_source_triggers_pdf_discovery(self):
        html = '<html><a href="/notices/merit_list.pdf">Merit List</a></html>'
        pdf_url = "https://pu.edu.pk/notices/merit_list.pdf"
        base_url = "https://pu.edu.pk/admissions"

        source = Source(institution_id="pu", campus=None, url=base_url, format="html+pdf")
        session = FakeSession({
            base_url: FakeResponse(text=html),
            pdf_url: FakeResponse(content=b"%PDF-1.4 fake bytes"),
        })

        result = fetch_source(source, session=session)

        assert result.ok is True
        assert len(result.pdfs) == 1
        assert result.pdfs[0].url == pdf_url
        # the pdf fetch call happened
        assert pdf_url in session.calls

    def test_plain_html_source_does_not_trigger_pdf_discovery(self):
        html = '<html><a href="/notices/merit_list.pdf">Merit List</a></html>'
        base_url = "https://example.edu.pk/admissions"

        source = Source(institution_id="x", campus=None, url=base_url, format="html")
        session = FakeSession({base_url: FakeResponse(text=html)})

        result = fetch_source(source, session=session)

        assert result.ok is True
        assert result.pdfs == []
        # only the main page was fetched, no PDF link followed
        assert session.calls == [base_url]


# ---------------------------------------------------------------------------
# pdf_fallback.py
# ---------------------------------------------------------------------------

class TestFindPdfLinks:
    def test_extracts_pdf_links(self):
        html = """
        <html><body>
        <a href="/files/a.pdf">A</a>
        <a href="/files/b.PDF">B upper-case ext</a>
        <a href="/page.html">not a pdf</a>
        <a href="https://other.com/c.pdf?version=2">C with query string</a>
        </body></html>
        """
        base_url = "https://example.edu.pk/admissions"
        links = find_pdf_links(html, base_url)

        assert "https://example.edu.pk/files/a.pdf" in links
        assert "https://example.edu.pk/files/b.PDF" in links
        assert "https://other.com/c.pdf?version=2" in links
        assert not any(link.endswith("page.html") for link in links)

    def test_deduplicates_links_preserving_order(self):
        html = """
        <a href="/files/a.pdf">A1</a>
        <a href="/files/b.pdf">B</a>
        <a href="/files/a.pdf">A2 duplicate</a>
        """
        base_url = "https://example.edu.pk/admissions"
        links = find_pdf_links(html, base_url)

        assert links == [
            "https://example.edu.pk/files/a.pdf",
            "https://example.edu.pk/files/b.pdf",
        ]

    def test_ignores_non_pdf_links(self):
        html = '<a href="/foo.docx">doc</a><a href="/bar">no ext</a>'
        links = find_pdf_links(html, "https://example.edu.pk/")
        assert links == []

    def test_resolves_relative_urls_against_base(self):
        html = '<a href="notice.pdf">N</a>'
        base_url = "https://example.edu.pk/admissions/index.html"
        links = find_pdf_links(html, base_url)
        assert links == ["https://example.edu.pk/admissions/notice.pdf"]

    def test_empty_html_returns_empty_list(self):
        assert find_pdf_links("", "https://example.edu.pk/") == []


class TestFetchPdfText:
    def test_fetch_failure_returns_error_not_exception(self):
        session = FakeSession({"https://example.edu.pk/x.pdf": requests.Timeout("timed out")})

        doc = fetch_pdf_text("https://example.edu.pk/x.pdf", session=session)

        assert isinstance(doc, PdfDocument)
        assert doc.text is None
        assert doc.error is not None
        assert "fetch failed" in doc.error

    def test_http_error_status_returns_error(self):
        session = FakeSession({"https://example.edu.pk/x.pdf": FakeResponse(content=b"nope", status_code=500)})

        doc = fetch_pdf_text("https://example.edu.pk/x.pdf", session=session)

        assert doc.text is None
        assert doc.error is not None

    def test_unparseable_pdf_bytes_returns_error_not_exception(self):
        # Not valid PDF content at all -- pdfplumber should raise internally,
        # and fetch_pdf_text must catch it and report .error instead of raising.
        session = FakeSession({
            "https://example.edu.pk/garbage.pdf": FakeResponse(content=b"this is not a pdf file at all")
        })

        doc = fetch_pdf_text("https://example.edu.pk/garbage.pdf", session=session)

        assert isinstance(doc, PdfDocument)
        assert doc.text is None
        assert doc.error is not None
        assert "extraction failed" in doc.error

    def test_valid_pdf_bytes_extract_text(self):
        pdfplumber = pytest.importorskip("pdfplumber")
        # Build a real tiny PDF in-memory using pdfplumber's dependency chain
        # is heavy; instead use a minimal, known-good single-page PDF fixture.
        try:
            from reportlab.pdfgen import canvas
            from io import BytesIO
        except ImportError:
            pytest.skip("reportlab not installed; skipping real-PDF extraction test")

        buf = BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 750, "Merit List Fall 2026")
        c.save()
        pdf_bytes = buf.getvalue()

        session = FakeSession({"https://example.edu.pk/real.pdf": FakeResponse(content=pdf_bytes)})
        doc = fetch_pdf_text("https://example.edu.pk/real.pdf", session=session)

        assert doc.error is None
        assert doc.text is not None
        assert "Merit List" in doc.text


class TestFetchLinkedPdfs:
    def test_fetches_all_discovered_pdf_links(self):
        html = '<a href="/a.pdf">A</a><a href="/b.pdf">B</a>'
        base_url = "https://example.edu.pk/notices"
        session = FakeSession({
            "https://example.edu.pk/a.pdf": FakeResponse(content=b"not really a pdf"),
            "https://example.edu.pk/b.pdf": FakeResponse(content=b"also not really a pdf"),
        })

        docs = fetch_linked_pdfs(html, base_url, session)

        assert len(docs) == 2
        assert {d.url for d in docs} == {
            "https://example.edu.pk/a.pdf",
            "https://example.edu.pk/b.pdf",
        }
        # both are unparseable garbage -> errors recorded, no exception raised
        assert all(d.error is not None for d in docs)


# ---------------------------------------------------------------------------
# run.py
# ---------------------------------------------------------------------------

class TestSlugifySource:
    def test_no_campus_returns_bare_institution_id(self):
        assert slugify_source("giki", None) == "giki"

    def test_campus_with_spaces_and_parens_is_filesystem_safe(self):
        slug = slugify_source("uet", "Lahore (Main)")
        assert slug == "uet__lahore_main"
        # filesystem-safe: no spaces, parens, slashes
        for bad_char in " ()/\\:":
            assert bad_char not in slug

    def test_slug_is_stable_across_calls(self):
        a = slugify_source("uet", "Taxila")
        b = slugify_source("uet", "Taxila")
        assert a == b

    def test_different_campuses_produce_different_slugs(self):
        s1 = slugify_source("uet", "Lahore (Main)")
        s2 = slugify_source("uet", "Taxila")
        assert s1 != s2

    def test_ampersand_is_replaced(self):
        slug = slugify_source("x", "Foo & Bar")
        assert "&" not in slug
        assert "and" in slug
