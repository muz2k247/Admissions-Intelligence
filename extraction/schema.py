"""Extraction schema.

Field-level confidence, not record-level (CLAUDE.md hard rule 2): every
extracted attribute carries its own (value, confidence) pair independently.
A field that wasn't explicitly stated on the source page is null with no
confidence score, never a guessed value (hard rule 1).

Every ExtractedRecord retains institution_id, campus, and source_url (hard
rule 4), copied straight from the scraper's FetchResult.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_DEGREE_LEVELS = {"Undergraduate", "Postgraduate"}


@dataclass(frozen=True)
class Field:
    """A single extracted attribute. value=None implies confidence=None —
    there is nothing to be confident about when nothing was extracted.

    value is usually a scalar (str, or list[str] for programs), but
    extract_deadline can also return a list[{"label": str, "date": str}]
    when a page genuinely has multiple distinct deadlines (e.g. different
    entry-test tracks) that are honestly, individually labelable — one
    Field.confidence still covers the whole extraction, not per-entry."""

    value: Any = None
    confidence: float | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.value is None and self.confidence is not None:
            raise ValueError("Field with value=None must have confidence=None")
        if self.value is not None and self.confidence is None:
            raise ValueError("Field with a non-null value must carry a confidence score")
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence}")

    @property
    def is_null(self) -> bool:
        return self.value is None

    def to_dict(self) -> dict:
        return {"value": self.value, "confidence": self.confidence, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Field":
        if not d:
            return NULL_FIELD
        return cls(value=d.get("value"), confidence=d.get("confidence"), note=d.get("note"))


NULL_FIELD = Field()


@dataclass(frozen=True)
class DegreeLevel:
    """UG/PG routing result. Set only from a content-classifier chunk-output
    file (hard rule 3) — never derived from a source's URL or its
    ug_pg_mixed config flag, which is informational only."""

    value: str | None = None  # "Undergraduate" | "Postgraduate" | None (Ambiguous)
    reason: str | None = None  # reason code, required when value is None

    def __post_init__(self) -> None:
        if self.value is not None and self.value not in VALID_DEGREE_LEVELS:
            raise ValueError(f"degree_level.value must be one of {VALID_DEGREE_LEVELS} or None, got {self.value!r}")
        if self.value is None and self.reason is None:
            raise ValueError("Ambiguous degree_level (value=None) must carry a reason code")

    def to_dict(self) -> dict:
        return {"value": self.value, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict | None) -> "DegreeLevel":
        if not d:
            return cls(value=None, reason="no-signal")
        value = d.get("value")
        reason = d.get("reason") or "no-signal" if value is None else None
        return cls(value=value, reason=reason)


@dataclass(frozen=True)
class ExtractedRecord:
    institution_id: str
    campus: str | None
    source_url: str
    fetched_at: str
    chunk_id: str
    degree_level: DegreeLevel
    constituent_college: Field
    deadline: Field
    programs: Field
    admissions_open: Field = NULL_FIELD

    def to_dict(self) -> dict:
        return {
            "institution_id": self.institution_id,
            "campus": self.campus,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "chunk_id": self.chunk_id,
            "degree_level": self.degree_level.to_dict(),
            "constituent_college": self.constituent_college.to_dict(),
            "deadline": self.deadline.to_dict(),
            "programs": self.programs.to_dict(),
            "admissions_open": self.admissions_open.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExtractedRecord":
        return cls(
            institution_id=d["institution_id"],
            campus=d.get("campus"),
            source_url=d["source_url"],
            fetched_at=d["fetched_at"],
            chunk_id=d["chunk_id"],
            degree_level=DegreeLevel.from_dict(d.get("degree_level")),
            constituent_college=Field.from_dict(d.get("constituent_college")),
            deadline=Field.from_dict(d.get("deadline")),
            programs=Field.from_dict(d.get("programs")),
            admissions_open=Field.from_dict(d.get("admissions_open")),
        )
