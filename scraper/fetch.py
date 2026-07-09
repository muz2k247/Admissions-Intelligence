"""Config-driven HTML fetching, with PDF fallback for sources that need it.

Every fetch result retains the source URL and campus it came from (CLAUDE.md
hard rule 4) so downstream extraction never has to reconstruct provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from scraper.config import Source
from scraper.pdf_fallback import PdfDocument, fetch_linked_pdfs

USER_AGENT = "AdmissionsIntelligenceBot/0.1 (+https://github.com/muz2k247/Admissions-Intelligence)"
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


def fetch_source(source: Source, session: requests.Session | None = None, timeout: int = DEFAULT_TIMEOUT) -> FetchResult:
    session = session or build_session()
    fetched_at = datetime.now(timezone.utc).isoformat()

    try:
        resp = session.get(source.url, timeout=timeout)
        resp.raise_for_status()
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
        pdfs = fetch_linked_pdfs(html, source.url, session)

    return FetchResult(
        institution_id=source.institution_id,
        campus=source.campus,
        source_url=source.url,
        fetched_at=fetched_at,
        html=html,
        pdfs=pdfs,
    )
