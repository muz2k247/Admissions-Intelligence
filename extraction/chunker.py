"""Turns a scraped record (scraper/run.py output) into chunk(s) for the
content-classifier subagent and for field extraction.

Chunking is one chunk per HTML page plus one chunk per linked PDF with
extracted text — not one blob per source. Concatenating everything into a
single chunk (the prior design) meant a PDF-heavy source's per-fact
provenance was lost (a fact from PDF #14 got attributed to the page's
source_url, not the PDF it actually came from — a real gap against
CLAUDE.md hard rule 4) and produced unwieldy blobs (pu: 240K+ chars, uhs:
207K+ chars) that neither the regex extractor nor an LLM extractor can
usefully work with. Splitting by document, each with its own accurate
source_url, is a structural fix, not a guess at content boundaries within
a single document — real per-announcement splitting within one HTML page
or one PDF is future work that needs real content to design against
(CLAUDE.md: don't build ahead of scope).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

# Some admissions pages encode their real deadline only as a machine-readable
# data-attribute on a JS countdown widget (e.g. data-end-date="2026-07-15T...")
# with no matching plain-text sentence nearby for the keyword-anchored field
# extractor in extraction/fields.py to find. The site itself names the
# attribute after a deadline/end concept, so surfacing it as a synthetic
# keyword-anchored sentence here is a structured read of the page, not a guess
# — it just runs through the same text-based extractor as everything else.
# Deliberately excludes the bare `data-date` attribute: it's too generic
# (calendars, blog "published" stamps, event dates) to assume it means an
# admissions deadline — assuming that would be inference, which hard rule 1
# forbids. Only `data-deadline` and `data-end-date`, whose names are explicitly
# deadline/end-oriented, qualify. Captures only the YYYY-MM-DD prefix so it
# lines up with fields.py's date pattern regardless of a trailing time.
_DATE_ATTR_PATTERN = re.compile(
    r'data-(?:end-date|deadline)\s*=\s*["\'](\d{4}-\d{2}-\d{2})',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Chunk:
    id: str
    institution_id: str
    campus: str | None
    source_url: str
    fetched_at: str
    raw_text: str

    def to_classifier_dict(self) -> dict:
        """Shape expected by the content-classifier subagent's input file."""
        return {
            "id": self.id,
            "institution": self.institution_id,
            "source_url": self.source_url,
            "raw_text": self.raw_text,
        }


def _html_to_text(html: str | None) -> str:
    if not html:
        return ""
    # lxml (not the stdlib html.parser): on certain malformed real-world
    # markup — e.g. an unclosed <title> — html.parser leaks raw tag soup into
    # the extracted text, whereas lxml recovers and parses it cleanly. lxml
    # correctly ignores content inside HTML comments (<!-- ... -->), so a
    # section a site has deliberately commented out is not surfaced as live
    # text (verified against COMSATS, whose stale Fall-2025 announcement modal
    # is wrapped in a comment).
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _pdf_chunk_id(base_chunk_id: str, pdf_url: str) -> str:
    """Stable across runs -- derived from the PDF's own URL, not an index or
    position in the pdfs list, since chunk_id is also the key curator
    overrides are stored against (extraction/schema.py, pipeline/overrides.py)
    and a run-to-run-unstable id would silently orphan those corrections.
    A short hash disambiguates PDFs that happen to share a filename across
    different paths; the readable path fragment is just for humans skimming
    filenames, not relied on for uniqueness.

    Deliberately hashes scheme+netloc+path only, NOT the query string: real
    scraped PDF links (e.g. Punjab University's) carry cache-busting query
    params like "?v=1783709854" (a Unix timestamp) that change on every
    scrape for what is the same underlying document -- including them would
    silently break the stability guarantee this function exists for."""
    parts = urlsplit(pdf_url)
    stable_url = f"{parts.scheme}://{parts.netloc}{parts.path}"
    path_slug = re.sub(r"[^a-z0-9]+", "_", parts.path.lower()).strip("_")
    digest = hashlib.sha256(stable_url.encode("utf-8")).hexdigest()[:10]
    if path_slug:
        return f"{base_chunk_id}__pdf_{path_slug[-40:]}_{digest}"
    return f"{base_chunk_id}__pdf_{digest}"


def chunk_scraped_record(record: dict) -> list[Chunk]:
    """record is a dict matching scraper/run.py's per-source JSON output.

    Returns one Chunk for the HTML page (if it has any real text, including
    the JS-countdown-widget synthetic line below) plus one Chunk per linked
    PDF that has extracted text -- each carrying its own actual source_url,
    not the page's."""
    html = record.get("html")
    institution_id = record["institution_id"]
    campus = record.get("campus")

    base_chunk_id = institution_id
    if campus:
        campus_slug = campus.lower().replace(" ", "_")
        base_chunk_id = f"{base_chunk_id}__{campus_slug}"

    chunks: list[Chunk] = []

    html_text_parts = [_html_to_text(html)]
    for date_str in _DATE_ATTR_PATTERN.findall(html or ""):
        # "Application deadline" (not bare "Deadline"): fields.py treats
        # that phrase as an unambiguous primary signal, not generic noise
        # that a genuinely different same-page deadline could false-conflict
        # against -- this widget value deserves the same authority as an
        # explicit "Application Deadline:" sentence would, since it's
        # curated to mean exactly that (see _DATE_ATTR_PATTERN above).
        html_text_parts.append(f"Application deadline (site countdown widget): {date_str}")
    html_text = "\n\n".join(part for part in html_text_parts if part).strip()

    if html_text:
        chunks.append(
            Chunk(
                id=base_chunk_id,
                institution_id=institution_id,
                campus=campus,
                source_url=record["source_url"],
                fetched_at=record["fetched_at"],
                raw_text=html_text,
            )
        )

    for pdf in record.get("pdfs") or []:
        pdf_text = pdf.get("text")
        if not pdf_text or not pdf_text.strip():
            continue
        chunks.append(
            Chunk(
                id=_pdf_chunk_id(base_chunk_id, pdf["url"]),
                institution_id=institution_id,
                campus=campus,
                source_url=pdf["url"],
                fetched_at=record["fetched_at"],
                raw_text=pdf_text.strip(),
            )
        )

    return chunks
