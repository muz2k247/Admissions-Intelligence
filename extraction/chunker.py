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

from dataclasses import dataclass

from bs4 import BeautifulSoup


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
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def chunk_scraped_record(record: dict) -> list[Chunk]:
    """record is a dict matching scraper/run.py's per-source JSON output."""
    text_parts = [_html_to_text(record.get("html"))]
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
