"""PDF fallback path — called by scraper.fetch when a source's format
includes PDF (currently Punjab University and UHS/NUMS). Not a separate
parallel pipeline: the HTML scraper finds PDF links on the page and hands
them here for text extraction (CLAUDE.md: "Format handling").
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup
from io import BytesIO


@dataclass(frozen=True)
class PdfDocument:
    url: str
    text: str | None  # None if the PDF was found but text extraction failed
    error: str | None = None


def find_pdf_links(html: str, base_url: str) -> list[str]:
    # Matches only literal ".pdf" hrefs. Some sites serve PDFs through
    # extensionless download/redirect endpoints, which this will miss
    # silently — acceptable for now, revisit if PU/UHS/NUMS coverage gaps
    # trace back to this.
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().split("?")[0].endswith(".pdf"):
            links.append(urljoin(base_url, href))
    # de-duplicate while preserving order
    seen = set()
    deduped = []
    for link in links:
        if link not in seen:
            seen.add(link)
            deduped.append(link)
    return deduped


def fetch_pdf_text(url: str, session: requests.Session, timeout: int = 30) -> PdfDocument:
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return PdfDocument(url=url, text=None, error=f"fetch failed: {exc}")

    try:
        with pdfplumber.open(BytesIO(resp.content)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages).strip()
        return PdfDocument(url=url, text=text or None)
    except Exception as exc:  # pdfplumber/pdfminer raise varied exception types
        return PdfDocument(url=url, text=None, error=f"extraction failed: {exc}")


def fetch_linked_pdfs(html: str, base_url: str, session: requests.Session) -> list[PdfDocument]:
    return [fetch_pdf_text(link, session) for link in find_pdf_links(html, base_url)]
