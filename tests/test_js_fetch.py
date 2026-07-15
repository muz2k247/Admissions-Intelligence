"""Tests for the headless-browser fetch path (scraper/js_fetch.py).

No real browser is launched here — sync_playwright/chromium is mocked.
CLAUDE.md/QA policy: no test may hit a live university website, and this
also keeps the suite fast (no Chromium download/launch needed to run tests).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scraper.config import Source
from scraper.fetch import FetchResult, fetch_source
from scraper.js_fetch import fetch_source_js


def _mock_playwright(html: str | None = None, goto_exc: Exception | None = None, status: int = 200):
    """Builds a MagicMock standing in for sync_playwright()'s context manager,
    wired so page.content() returns `html` (or page.goto() raises `goto_exc`),
    and page.goto()'s returned response reports `status`."""
    page = MagicMock()
    if goto_exc is not None:
        page.goto.side_effect = goto_exc
    else:
        page.goto.return_value.status = status
    page.content.return_value = html

    browser = MagicMock()
    browser.new_page.return_value = page

    chromium = MagicMock()
    chromium.launch.return_value = browser

    playwright_instance = MagicMock()
    playwright_instance.chromium = chromium

    context_manager = MagicMock()
    context_manager.__enter__.return_value = playwright_instance
    context_manager.__exit__.return_value = False
    return context_manager, browser


class TestFetchSourceJs:
    def test_successful_render_returns_html_and_preserves_source_url(self):
        source = Source(institution_id="ist", campus=None, url="https://ist.edu.pk/admission", format="html", render="js")
        ctx, browser = _mock_playwright(html="<html>rendered content</html>")

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(source)

        assert isinstance(result, FetchResult)
        assert result.ok is True
        assert result.html == "<html>rendered content</html>"
        assert result.source_url == source.url
        assert result.institution_id == "ist"
        assert result.fetched_at
        browser.close.assert_called_once()

    def test_timeout_is_caught_not_raised(self):
        source = Source(institution_id="ist", campus=None, url="https://ist.edu.pk/admission", format="html", render="js")
        ctx, browser = _mock_playwright(goto_exc=PlaywrightTimeoutError("timed out"))

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(source, timeout=5)

        assert result.ok is False
        assert result.html is None
        assert result.error is not None
        assert "fetch failed" in result.error
        # browser must still be closed even though goto() raised
        browser.close.assert_called_once()

    def test_playwright_error_is_caught_not_raised(self):
        source = Source(institution_id="ist", campus=None, url="https://ist.edu.pk/admission", format="html", render="js")
        ctx, browser = _mock_playwright(goto_exc=PlaywrightError("navigation failed"))

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(source)

        assert result.ok is False
        assert result.error is not None

    def test_http_error_status_is_treated_as_failure_not_success(self):
        # docs/js_rendering_audit.md documents ist.edu.pk returning HTTP 403
        # (Cloudflare) in a prior check -- a 403/challenge page loads
        # successfully from Playwright's point of view (no exception), so
        # this must be caught by inspecting the response status, not just
        # exceptions, or a blocked fetch would silently report ok=True with
        # challenge-page HTML as if it were the real page.
        source = Source(institution_id="ist", campus=None, url="https://ist.edu.pk/admission", format="html", render="js")
        ctx, browser = _mock_playwright(html="<html>cloudflare challenge</html>", status=403)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(source)

        assert result.ok is False
        assert result.html is None
        assert result.error is not None
        assert "403" in result.error
        browser.close.assert_called_once()

    def test_no_response_is_treated_as_failure(self):
        source = Source(institution_id="ist", campus=None, url="https://ist.edu.pk/admission", format="html", render="js")
        ctx, browser = _mock_playwright(html="<html>irrelevant</html>")
        ctx.__enter__.return_value.chromium.launch.return_value.new_page.return_value.goto.return_value = None

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(source)

        assert result.ok is False
        assert result.error is not None

    def test_html_plus_pdf_source_triggers_pdf_discovery(self):
        html = '<html><a href="/notices/merit_list.pdf">Merit List</a></html>'
        source = Source(institution_id="ist", campus=None, url="https://ist.edu.pk/admission", format="html+pdf", render="js")
        ctx, _ = _mock_playwright(html=html)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx), \
             patch("scraper.js_fetch.fetch_linked_pdfs", return_value=["fake-pdf-doc"]) as mock_fetch_pdfs:
            result = fetch_source_js(source)

        assert result.ok is True
        assert result.pdfs == ["fake-pdf-doc"]
        mock_fetch_pdfs.assert_called_once()

    def test_static_source_does_not_use_js_render(self):
        source = Source(institution_id="giki", campus=None, url="https://giki.edu.pk", format="html")
        assert source.needs_js_render is False

    def test_js_source_flags_needs_js_render(self):
        source = Source(institution_id="ist", campus=None, url="https://ist.edu.pk/admission", format="html", render="js")
        assert source.needs_js_render is True


class TestFetchSourceDispatch:
    """fetch_source() (scraper/fetch.py) must dispatch to the JS-render path
    for render="js" sources, and never touch plain requests.get() for them."""

    def test_fetch_source_dispatches_to_js_path_for_js_sources(self):
        source = Source(institution_id="ist", campus=None, url="https://ist.edu.pk/admission", format="html", render="js")

        with patch("scraper.js_fetch.fetch_source_js") as mock_js_fetch:
            mock_js_fetch.return_value = FetchResult(
                institution_id="ist", campus=None, source_url=source.url,
                fetched_at="2026-01-01T00:00:00+00:00", html="<html>js</html>",
            )
            result = fetch_source(source)

        mock_js_fetch.assert_called_once()
        assert result.html == "<html>js</html>"

    def test_fetch_source_does_not_import_js_fetch_for_static_sources(self, monkeypatch):
        # A static source must go through requests, never touch scraper.js_fetch
        # at all -- this is what lets environments without Playwright installed
        # still scrape every source that doesn't need it.
        #
        # monkeypatch.delitem (not a bare sys.modules.pop) so pytest restores
        # the popped module back into sys.modules at teardown: other test
        # files (e.g. tests/test_js_fetch_phase_n.py) import
        # scraper.js_fetch.fetch_source_js once at collection time and later
        # patch("scraper.js_fetch.sync_playwright", ...) by name. If this
        # test's pop is never undone, that patch call re-imports a *fresh*
        # scraper.js_fetch module object (since sys.modules no longer has it
        # cached) and patches sync_playwright on that new object -- while the
        # already-bound fetch_source_js function from those other test files
        # still points at the *old* module's globals, so the patch silently
        # never takes effect and the real, unpatched sync_playwright runs
        # against the live site instead of the mock.
        import sys

        source = Source(institution_id="giki", campus=None, url="https://giki.edu.pk", format="html")

        class FakeResponse:
            text = "<html>static</html>"

            def raise_for_status(self):
                pass

        class FakeSession:
            def get(self, url, timeout=None):
                return FakeResponse()

        monkeypatch.delitem(sys.modules, "scraper.js_fetch", raising=False)
        result = fetch_source(source, session=FakeSession())

        assert result.html == "<html>static</html>"
        assert "scraper.js_fetch" not in sys.modules
