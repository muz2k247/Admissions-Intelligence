"""Config-driven HTML fetching, with PDF fallback for sources that need it.

Every fetch result retains the source URL and campus it came from (CLAUDE.md
hard rule 4) so downstream extraction never has to reconstruct provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from scraper.config import Source
from scraper.pdf_fallback import PdfDocument, fetch_linked_pdfs
from scraper.tls import build_completed_bundle

# A self-identifying bot UA (the previous value here) is the more polite
# choice in the abstract, but in practice several sources' WAFs reject any
# UA that doesn't look like a real browser regardless of intent -- and this
# scraper only reads public, unauthenticated admissions pages meant for any
# applicant to read, at a modest, non-concurrent request rate (Phase N,
# 2026-07). A generic, current-looking desktop Chrome UA is used instead;
# it deliberately isn't kept in lockstep with the actual latest Chrome
# release, since WAF UA checks match a general recognized-browser pattern,
# not an exact build number.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
DEFAULT_TIMEOUT = 30


@dataclass(frozen=True)
class FetchResult:
    institution_id: str
    campus: str | None
    source_url: str
    fetched_at: str
    html: str | None
    pdfs: list[PdfDocument] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def pdf_failures(self) -> list[PdfDocument]:
        return [p for p in self.pdfs if p.error is not None]


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _fetch_with_aia_recovery(
    source: Source, session: requests.Session, timeout: int
) -> tuple[requests.Response, str] | None:
    """Retry source.url once against an AIA-completed CA bundle. Returns the
    successful Response paired with the bundle path (so the caller can reuse
    it for same-host PDF fallback fetches too), or None if the chain
    couldn't be completed or the retry still failed (caller then reports the
    SSL failure)."""
    parts = urlsplit(source.url)
    host = parts.hostname
    if not host:
        return None
    bundle = build_completed_bundle(host, session, port=parts.port or 443, timeout=timeout)
    if bundle is None:
        return None
    try:
        resp = session.get(source.url, timeout=timeout, verify=bundle)
        resp.raise_for_status()
        return resp, bundle
    except requests.RequestException:
        return None


def fetch_source(source: Source, session: requests.Session | None = None, timeout: int = DEFAULT_TIMEOUT) -> FetchResult:
    if source.needs_js_render:
        # Lazy import: sources that don't need JS rendering (the vast
        # majority) should never require Playwright to be installed.
        from scraper.js_fetch import fetch_source_js

        return fetch_source_js(source, session=session, timeout=timeout)

    session = session or build_session()
    fetched_at = datetime.now(timezone.utc).isoformat()
    aia_bundle: str | None = None

    try:
        resp = session.get(source.url, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.SSLError:
        # The server may be misconfigured to omit its intermediate CA (a chain
        # a browser completes via AIA, but Python does not). Try to complete
        # the chain and verify against it — still a real trust decision, not a
        # verify=False bypass. If recovery isn't possible, report the original
        # failure below rather than silently trusting an unverifiable cert.
        recovered = _fetch_with_aia_recovery(source, session, timeout)
        if recovered is not None:
            resp, aia_bundle = recovered
        else:
            return FetchResult(
                institution_id=source.institution_id,
                campus=source.campus,
                source_url=source.url,
                fetched_at=fetched_at,
                html=None,
                error="fetch failed: SSL verification failed and automatic chain recovery was unsuccessful",
            )
    except requests.RequestException as exc:
        return FetchResult(
            institution_id=source.institution_id,
            campus=source.campus,
            source_url=source.url,
            fetched_at=fetched_at,
            html=None,
            error=f"fetch failed: {exc}",
        )

    html = resp.text
    pdfs: list[PdfDocument] = []
    if source.has_pdf_fallback:
        # Reuse the AIA-completed bundle (a superset of certifi's roots plus
        # the fetched intermediate) for same-host PDF links too, so a host
        # with an incomplete chain doesn't recover its HTML page but then
        # fail every linked PDF for the identical reason.
        pdfs = fetch_linked_pdfs(html, source.url, session, verify=aia_bundle or True)

    return FetchResult(
        institution_id=source.institution_id,
        campus=source.campus,
        source_url=source.url,
        fetched_at=fetched_at,
        html=html,
        pdfs=pdfs,
    )
