"""Turns a scraped record (scraper/run.py output) into chunk(s) for the
content-classifier subagent and for field extraction.

Current chunking is one chunk per source: the whole page's text, plus any
PDF text pulled by the PDF fallback, concatenated. Real admissions pages
often mix multiple distinct announcements in one page, which a future
version of this chunker could split into smaller, more precisely-classified
pieces — but that requires real scraped content to design against, so it's
deliberately not built ahead of the data (CLAUDE.md: don't build ahead of
scope). One page = one chunk is a valid degenerate case of the same schema,
not a special case bolted on later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

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


def chunk_scraped_record(record: dict) -> list[Chunk]:
    """record is a dict matching scraper/run.py's per-source JSON output."""
    html = record.get("html")
    text_parts = [_html_to_text(html)]
    for date_str in _DATE_ATTR_PATTERN.findall(html or ""):
        # "Application deadline" (not bare "Deadline"): fields.py treats
        # that phrase as an unambiguous primary signal, not generic noise
        # that a genuinely different same-page deadline could false-conflict
        # against -- this widget value deserves the same authority as an
        # explicit "Application Deadline:" sentence would, since it's
        # curated to mean exactly that (see _DATE_ATTR_PATTERN above).
        text_parts.append(f"Application deadline (site countdown widget): {date_str}")
    for pdf in record.get("pdfs") or []:
        if pdf.get("text"):
            text_parts.append(pdf["text"])
    raw_text = "\n\n".join(part for part in text_parts if part).strip()

    if not raw_text:
        return []

    chunk_id = record["institution_id"]
    if record.get("campus"):
        campus_slug = record["campus"].lower().replace(" ", "_")
        chunk_id = f"{chunk_id}__{campus_slug}"

    return [
        Chunk(
            id=chunk_id,
            institution_id=record["institution_id"],
            campus=record.get("campus"),
            source_url=record["source_url"],
            fetched_at=record["fetched_at"],
            raw_text=raw_text,
        )
    ]
