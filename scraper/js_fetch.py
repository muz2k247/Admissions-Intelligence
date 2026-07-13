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
                # "load" rather than "networkidle": networkidle can hang
                # until timeout on pages with any lingering background
                # activity (analytics beacons, polling) -- a real risk here
                # per Playwright's own guidance, and unvalidated against the
                # actual JS-gated target (Cloudflare blocked a live check
                # during the audit -- see docs/js_rendering_audit.md).
                response = page.goto(source.url, wait_until="load", timeout=timeout * 1000)
                # Chromium completes TLS chains (AIA) natively, unlike
                # Python's ssl module -- no need to duplicate scraper.tls's
                # AIA recovery here.
                if response is None or response.status >= 400:
                    status_error = f"HTTP {response.status if response else 'no response'}"
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
