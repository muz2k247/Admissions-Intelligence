"""Phase N tests: scraper/js_fetch.py's wait_until="domcontentloaded" switch
and the new post-goto settle wait (_JS_RENDER_SETTLE_MS), plus
scraper/fetch.py's USER_AGENT plumbing into build_session() and js_fetch.py.

No real browser is launched -- sync_playwright/chromium is mocked, matching
tests/test_js_fetch.py's pattern. CLAUDE.md/QA policy: no test may hit a live
university website.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("playwright")

from scraper.config import Source
from scraper.fetch import USER_AGENT, build_session
from scraper.js_fetch import _JS_RENDER_SETTLE_MS, _MIN_VISIBLE_TEXT_CHARS, fetch_source_js


def _mock_playwright(html: str | None = None, status: int = 200, visible_chars: int = 1500):
    """Same shape as tests/test_js_fetch.py's helper, but returns the `page`
    mock too so tests here can assert on page.goto / page.wait_for_timeout /
    page.content directly.

    page.evaluate is configured to return visible_chars (default well above
    _MIN_VISIBLE_TEXT_CHARS) since fetch_source_js now calls it as a
    content-readiness check after the settle wait -- an unconfigured
    MagicMock return value there raises TypeError when compared to an int
    (`'<' not supported between instances of 'MagicMock' and 'int'`), which
    is a test-mock gap, not a production bug (production hardens against a
    non-int return by treating it as inconclusive -- see
    scraper.js_fetch._visible_text_length)."""
    page = MagicMock()
    page.goto.return_value.status = status
    page.content.return_value = html
    page.evaluate.return_value = visible_chars

    browser = MagicMock()
    browser.new_page.return_value = page

    chromium = MagicMock()
    chromium.launch.return_value = browser

    playwright_instance = MagicMock()
    playwright_instance.chromium = chromium

    context_manager = MagicMock()
    context_manager.__enter__.return_value = playwright_instance
    context_manager.__exit__.return_value = False
    return context_manager, browser, page


SOURCE = Source(
    institution_id="ist", campus=None, url="https://ist.edu.pk/admission",
    format="html", render="js",
)


class TestGotoWaitUntil:
    def test_goto_uses_domcontentloaded_not_load(self):
        ctx, _, page = _mock_playwright(html="<html>rendered</html>")

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            fetch_source_js(SOURCE, timeout=30)

        assert page.goto.call_count == 1
        _, kwargs = page.goto.call_args
        assert kwargs["wait_until"] == "domcontentloaded"
        assert kwargs["wait_until"] != "load"
        assert kwargs["timeout"] == 30 * 1000


class TestSettleWaitOrdering:
    def test_wait_for_timeout_called_once_on_success_with_correct_duration(self):
        ctx, _, page = _mock_playwright(html="<html>rendered</html>", status=200)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is True
        page.wait_for_timeout.assert_called_once_with(_JS_RENDER_SETTLE_MS)

    def test_wait_for_timeout_happens_after_goto_and_before_content(self):
        ctx, _, page = _mock_playwright(html="<html>rendered</html>", status=200)

        call_order: list[str] = []
        page.goto.side_effect = lambda *a, **k: call_order.append("goto") or page.goto.return_value
        page.wait_for_timeout.side_effect = lambda *a, **k: call_order.append("wait_for_timeout")
        page.content.side_effect = lambda *a, **k: call_order.append("content") or "<html>rendered</html>"

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            fetch_source_js(SOURCE)

        assert call_order == ["goto", "wait_for_timeout", "content"]

    def test_wait_for_timeout_not_called_when_response_is_none(self):
        ctx, _, page = _mock_playwright(html="<html>irrelevant</html>")
        page.goto.return_value = None

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is False
        page.wait_for_timeout.assert_not_called()
        page.content.assert_not_called()

    def test_wait_for_timeout_not_called_on_http_error_status(self):
        ctx, _, page = _mock_playwright(html="<html>cloudflare challenge</html>", status=403)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is False
        assert result.error is not None
        assert "403" in result.error
        page.wait_for_timeout.assert_not_called()
        page.content.assert_not_called()

    def test_wait_for_timeout_not_called_on_boundary_status_400(self):
        # status >= 400 is the exact boundary in scraper/js_fetch.py;
        # confirm 400 itself (not just 403) is treated as an error and
        # skips the settle wait.
        ctx, _, page = _mock_playwright(html="<html>irrelevant</html>", status=400)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is False
        page.wait_for_timeout.assert_not_called()


class TestUserAgentPlumbing:
    def test_build_session_sets_user_agent_header(self):
        session = build_session()
        assert session.headers["User-Agent"] == USER_AGENT

    def test_js_fetch_page_created_with_same_user_agent(self):
        ctx, browser, page = _mock_playwright(html="<html>rendered</html>")

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            fetch_source_js(SOURCE)

        browser.new_page.assert_called_once_with(user_agent=USER_AGENT)

    def test_user_agent_looks_like_a_browser_not_a_bot(self):
        # Phase N: USER_AGENT switched from a self-identifying bot string to
        # a generic browser UA. Don't assert the exact literal (that's what
        # the task explicitly said not to do) -- just confirm the shape
        # changed away from a self-identifying bot string.
        assert "Bot" not in USER_AGENT
        assert "Mozilla" in USER_AGENT


class TestContentReadinessCheck:
    """A fixed settle wait alone can't tell "rendered fine, page just has
    little content" apart from "the JS render never actually populated the
    DOM" -- this is the check that turns the latter into a loud failure
    instead of a silently-successful near-empty result (the exact shape of
    the original ist bug: ~54 visible characters treated as ok=True)."""

    def test_suspiciously_short_visible_text_is_treated_as_a_failure(self):
        ctx, _, page = _mock_playwright(html="<html>skeleton shell</html>", visible_chars=54)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is False
        assert result.html is None
        assert "54" in result.error
        assert "suspiciously short" in result.error

    def test_boundary_at_min_visible_text_chars_is_not_a_failure(self):
        ctx, _, page = _mock_playwright(html="<html>ok</html>", visible_chars=_MIN_VISIBLE_TEXT_CHARS)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is True

    def test_just_below_boundary_is_a_failure(self):
        ctx, _, page = _mock_playwright(html="<html>ok</html>", visible_chars=_MIN_VISIBLE_TEXT_CHARS - 1)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is False

    def test_healthy_page_with_ample_visible_text_succeeds(self):
        ctx, _, page = _mock_playwright(html="<html>real content</html>", visible_chars=3000)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is True
        assert result.html == "<html>real content</html>"

    def test_non_int_evaluate_result_does_not_crash_and_is_treated_as_inconclusive(self):
        # page.evaluate() returning something other than an int (a browser
        # quirk, or a mocked test double someone forgets to configure) must
        # never raise -- it's treated as "can't tell," which lets the fetch
        # succeed rather than crashing the whole pipeline run over a
        # best-effort safety-net check.
        ctx, _, page = _mock_playwright(html="<html>ok</html>")
        page.evaluate.return_value = None

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is True

    def test_evaluate_raising_playwright_error_does_not_crash_and_is_treated_as_inconclusive(self):
        from playwright.sync_api import Error as PlaywrightError

        ctx, _, page = _mock_playwright(html="<html>ok</html>")
        page.evaluate.side_effect = PlaywrightError("evaluate failed")

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is True

    def test_visible_text_check_not_evaluated_on_http_error_status(self):
        ctx, _, page = _mock_playwright(html="<html>irrelevant</html>", status=403, visible_chars=54)

        with patch("scraper.js_fetch.sync_playwright", return_value=ctx):
            result = fetch_source_js(SOURCE)

        assert result.ok is False
        assert "403" in result.error
        page.evaluate.assert_not_called()
