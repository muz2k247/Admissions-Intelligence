"""Headless-browser fetch path for sources confirmed JS-gated (config-driven
via a source's render: "js" flag — see config/institutions.yaml's header
comment and docs/js_rendering_audit.md for which sources qualify and why).

Only imported when actually needed (scraper/fetch.py lazy-imports this
module), so sources that don't need JS rendering never require Playwright
to be installed.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from scraper.config import Source
from scraper.fetch import DEFAULT_TIMEOUT, USER_AGENT, FetchResult, build_session
from scraper.pdf_fallback import fetch_linked_pdfs

# How long to wait after domcontentloaded for client-side content (e.g. a
# Next.js RSC stream) to finish rendering, before snapshotting page.content().
# Verified live against ist.edu.pk (the only render:"js" source as of
# Phase N): a bare domcontentloaded snapshot captures ~3KB of nav/footer
# text with no real announcement content; waiting this long captures the
# actual admissions widget, including deadline text.
_JS_RENDER_SETTLE_MS = 8000

# A blind fixed-duration settle wait can't tell "rendered fine, page just has
# little content right now" apart from "the JS render never actually
# populated the DOM" -- without a check, the latter would silently return
# ok=True with a near-empty page, exactly the ist bug this fetch path exists
# to fix (its broken render produced ~54 visible characters). 200 is chosen
# comfortably below any real admissions page's nav+content (even ist's own
# nav-only domcontentloaded snapshot, before the settle wait, is ~3000
# characters) and comfortably above that known-broken case.
_MIN_VISIBLE_TEXT_CHARS = 200


def _visible_text_length(page) -> int | None:
    """Best-effort: None (not a failure) if the JS evaluation itself errors
    or returns something other than an int, since this check is an
    additional safety net, not the primary source of truth for fetch
    success/failure."""
    try:
        result = page.evaluate("() => document.body ? document.body.innerText.length : 0")
    except PlaywrightError:
        return None
    return result if isinstance(result, int) else None


def fetch_source_js(
    source: Source, session: requests.Session | None = None, timeout: int = DEFAULT_TIMEOUT
) -> FetchResult:
    """Render source.url in headless Chromium and return the same FetchResult
    shape fetch_source() produces, so downstream stages need zero awareness
    of which fetch path ran."""
    session = session or build_session()
    fetched_at = datetime.now(timezone.utc).isoformat()

    html: str | None = None
    status_error: str | None = None
    try:
        with sync_playwright() as p:
            # --no-sandbox: the pipeline runs in an isolated cloud sandbox /
            # CI runner where Chromium's own sandbox often can't set up (no
            # privilege to do so); harmless on a normal dev machine too.
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                # domcontentloaded, not "load" or "networkidle": verified
                # live against ist.edu.pk (Phase N, 2026-07) that "load"
                # simply times out (some lingering resource never finishes)
                # and "networkidle" carries the same documented risk. The
                # real admissions content here is a client-side RSC stream
                # that finishes shortly after DOMContentLoaded, not before
                # it -- confirmed by fetching with a fixed settle wait after
                # domcontentloaded and finding real deadline text ("Closing
                # Date: ...") that a bare domcontentloaded snapshot misses.
                response = page.goto(source.url, wait_until="domcontentloaded", timeout=timeout * 1000)
                # Chromium completes TLS chains (AIA) natively, unlike
                # Python's ssl module -- no need to duplicate scraper.tls's
                # AIA recovery here.
                if response is None or response.status >= 400:
                    status_error = f"HTTP {response.status if response else 'no response'}"
                else:
                    page.wait_for_timeout(_JS_RENDER_SETTLE_MS)
                    visible_chars = _visible_text_length(page)
                    if visible_chars is not None and visible_chars < _MIN_VISIBLE_TEXT_CHARS:
                        status_error = (
                            f"rendered content suspiciously short ({visible_chars} visible "
                            "chars) -- the page likely didn't finish client-side rendering "
                            "within the settle wait"
                        )
                    else:
                        html = page.content()
            finally:
                browser.close()
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        status_error = str(exc)

    if status_error is not None:
        return FetchResult(
            institution_id=source.institution_id,
            campus=source.campus,
            source_url=source.url,
            fetched_at=fetched_at,
            html=None,
            error=f"fetch failed (headless render): {status_error}",
        )

    pdfs = []
    if source.has_pdf_fallback:
        pdfs = fetch_linked_pdfs(html, source.url, session)

    return FetchResult(
        institution_id=source.institution_id,
        campus=source.campus,
        source_url=source.url,
        fetched_at=fetched_at,
        html=html,
        pdfs=pdfs,
    )
