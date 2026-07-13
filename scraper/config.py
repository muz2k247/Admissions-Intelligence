"""Loads config/institutions.yaml into typed structures.

Every scraper/extraction module reads institution structure through this
module — never hardcode a URL, selector, or campus list in code (CLAUDE.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "institutions.yaml"


@dataclass(frozen=True)
class Source:
    institution_id: str
    campus: str | None
    url: str
    format: str  # "html" | "html+pdf"

    @property
    def has_pdf_fallback(self) -> bool:
        return "pdf" in self.format


@dataclass(frozen=True)
class Institution:
    id: str
    name: str
    admitting_body: bool
    ug_pg_mixed: bool
    sources: list[Source]
    enabled: bool = True


def load_institutions(config_path: Path | str = DEFAULT_CONFIG_PATH) -> list[Institution]:
    config_path = Path(config_path)
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    institutions = []
    for entry in raw["institutions"]:
        sources = [
            Source(
                institution_id=entry["id"],
                campus=src.get("campus"),
                url=src["url"],
                format=src["format"],
            )
            for src in entry["sources"]
        ]
        institutions.append(
            Institution(
                id=entry["id"],
                name=entry["name"],
                admitting_body=entry.get("admitting_body", False),
                ug_pg_mixed=entry.get("ug_pg_mixed", False),
                sources=sources,
                enabled=entry.get("enabled", True),
            )
        )
    return institutions


def iter_sources(institutions: list[Institution]):
    """Yield (institution, source) pairs for scraping. Disabled institutions
    (admin-toggled off via config/institutions.yaml's `enabled: false`) are
    skipped here -- the scraper's only real entry point -- but
    load_institutions() itself still returns them, so listing/admin code can
    still see and re-enable a disabled institution."""
    for institution in institutions:
        if not institution.enabled:
            continue
        for source in institution.sources:
            yield institution, source
