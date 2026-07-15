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


VALID_RENDER_MODES = {"static", "js"}


@dataclass(frozen=True)
class Source:
    institution_id: str
    campus: str | None
    url: str
    format: str  # "html" | "html+pdf"
    render: str = "static"  # "static" | "js" — see config/institutions.yaml's header comment

    @property
    def has_pdf_fallback(self) -> bool:
        return "pdf" in self.format

    @property
    def needs_js_render(self) -> bool:
        return self.render == "js"


@dataclass(frozen=True)
class Institution:
    id: str
    name: str
    admitting_body: bool
    ug_pg_mixed: bool
    sources: list[Source]
    enabled: bool = True


def campus_collision_key(campus: str | None) -> str:
    """Canonical key two sources' campus values collide under.

    Deliberately normalizes more aggressively than either downstream
    consumer strictly requires on its own -- extraction/chunker.py's
    chunk_id derivation (`institution_id__campus.lower().replace(" ", "_")`,
    or bare `institution_id` when campus is falsy) and pipeline/run_full.py's
    stage_1_scrape scraped-file naming (`institution_id__{campus or
    "default"}`, exact string, no case-folding) -- so this single check
    catches every real collision surface at once: two sources with no
    campus, two sources sharing the identical campus string, and two campus
    names that only differ by case or whitespace (which would still collide
    at the chunk_id layer even though their raw strings differ, and
    wouldn't be caught by an exact-string comparison alone).

    A blank/whitespace-only campus (e.g. "" or "   ") is bucketed together
    with a genuinely absent (None) campus -- note this is a deliberate
    simplification, not a claim that the two downstream consumers already
    collide on this exact pairing today (a whitespace-only string is
    truthy in Python, so as written neither chunker.py's `if campus:` nor
    stage_1_scrape's `campus or "default"` treats it as absent, and their
    literal outputs would in fact differ from the true-None case). Real
    campus content should never be pure whitespace, so treating it as
    equivalent to "no campus" here is the safer call regardless."""
    if campus is None or not campus.strip():
        return ""
    return campus.strip().lower().replace(" ", "_")


def find_campus_collision(sources: list[Source]) -> tuple[str, str] | None:
    """Returns (first_label, second_label) of the first two sources within
    one institution's `sources` list whose campus values collide (see
    campus_collision_key), or None if every source has a distinct campus
    key. A collision means the second-scraped source silently overwrites
    the first's scraped-file / extracted-record file on disk (same
    filename / chunk_id), discarding that source's data entirely with no
    warning anywhere in the pipeline -- so this must be checked wherever
    institution structure is built (the YAML loader below, and
    pipeline/institutions_registry.py's Firestore-doc validator), never
    just assumed valid. `label` is "(no campus)" for any campus that buckets
    into the blank key (None, "", or whitespace-only -- not just a raw
    falsy check, so a whitespace-only campus never leaks into an error/
    warning message as a literal, invisible run of spaces)."""
    seen: dict[str, str] = {}
    for src in sources:
        key = campus_collision_key(src.campus)
        label = src.campus.strip() if key else "(no campus)"
        if key in seen:
            return seen[key], label
        seen[key] = label
    return None


def load_institutions(config_path: Path | str = DEFAULT_CONFIG_PATH) -> list[Institution]:
    config_path = Path(config_path)
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    institutions = []
    for entry in raw["institutions"]:
        sources = []
        for src in entry["sources"]:
            render = src.get("render", "static")
            if render not in VALID_RENDER_MODES:
                raise ValueError(
                    f"{entry['id']}: source render={render!r} must be one of {VALID_RENDER_MODES}"
                )
            sources.append(
                Source(
                    institution_id=entry["id"],
                    campus=src.get("campus"),
                    url=src["url"],
                    format=src["format"],
                    render=render,
                )
            )
        collision = find_campus_collision(sources)
        if collision is not None:
            raise ValueError(
                f"{entry['id']}: sources {collision[0]!r} and {collision[1]!r} would collide on "
                "the same scraped-file name / chunk_id -- give each source a distinct campus "
                "(or make sure at most one source has no campus)."
            )
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
