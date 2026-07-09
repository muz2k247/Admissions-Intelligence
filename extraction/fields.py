"""Heuristic, regex-based field extraction from plain text.

No LLM calls here by design (CLAUDE.md: cost minimization, prefer lighter
methods). A field is only ever populated when a keyword-anchored pattern
matches unambiguously in the text; anything else stays null with no
confidence score (hard rule 1: never infer/guess a missing field).
"""
from __future__ import annotations

import re

from extraction.schema import Field, NULL_FIELD

_WINDOW = 80  # characters searched after a keyword for a value pattern

_DEADLINE_KEYWORDS = [
    "last date", "deadline", "closing date", "apply by",
    "due date", "last day to apply",
]

_DATE_PATTERN = re.compile(
    r"\b(\d{1,2}(st|nd|rd|th)?[\s\-/]+(January|February|March|April|May|June|July|"
    r"August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|"
    r"Oct|Nov|Dec)[\s\-/,]+\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    re.IGNORECASE,
)

_FEE_KEYWORDS = ["application fee", "processing fee", "admission fee", "fee"]

_FEE_PATTERN = re.compile(
    r"\b(Rs\.?|PKR)\s?[\d,]+(/-)?",
    re.IGNORECASE,
)

_PROGRAM_TOKENS = [
    "BS", "BE", "B.Sc", "BSc", "BBA", "MBBS", "BDS", "ADP",
    "MS", "MPhil", "M.Phil", "PhD", "Ph.D",
]
_PROGRAM_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _PROGRAM_TOKENS) + r")\b",
)


def _keyword_anchored_matches(text: str, keywords: list[str], value_pattern: re.Pattern) -> list[str]:
    """Find value_pattern matches that occur within _WINDOW characters after
    a keyword occurrence. Returns the matched substrings (not normalized).

    Keywords are tried longest-first and a claimed span is not re-matched by
    a shorter keyword contained within it (e.g. "application fee" already
    anchors a match — the "fee" inside it shouldn't count as a second,
    independent signal and inflate confidence)."""
    lower = text.lower()
    claimed: list[tuple[int, int]] = []
    found = []
    for kw in sorted(keywords, key=len, reverse=True):
        start = 0
        while True:
            idx = lower.find(kw, start)
            if idx == -1:
                break
            end = idx + len(kw)
            start = end
            if any(idx < c_end and end > c_start for c_start, c_end in claimed):
                continue
            window = text[idx: idx + len(kw) + _WINDOW]
            m = value_pattern.search(window)
            if m:
                found.append(m.group(0).strip())
                claimed.append((idx, end))
    return found


def extract_deadline(text: str) -> Field:
    if not text:
        return NULL_FIELD
    matches = _keyword_anchored_matches(text, _DEADLINE_KEYWORDS, _DATE_PATTERN)
    if not matches:
        return NULL_FIELD
    distinct = set(matches)
    if len(distinct) == 1:
        confidence = 0.95 if len(matches) > 1 else 0.85
        return Field(value=matches[0], confidence=confidence)
    return Field(value=None, confidence=None, note="conflicting deadline candidates found — left null rather than guessed")


def extract_fee(text: str) -> Field:
    if not text:
        return NULL_FIELD
    matches = _keyword_anchored_matches(text, _FEE_KEYWORDS, _FEE_PATTERN)
    if not matches:
        return NULL_FIELD
    distinct = set(matches)
    if len(distinct) == 1:
        confidence = 0.9 if len(matches) > 1 else 0.8
        return Field(value=matches[0], confidence=confidence)
    return Field(value=None, confidence=None, note="conflicting fee candidates found — left null rather than guessed")


def extract_constituent_college(text: str) -> Field:
    """Always null for now. config/institutions.yaml's constituent_colleges
    field is unstructured prose (e.g. "King Edward, Allama Iqbal, Nishtar,
    etc."), not a clean enumerable list — matching against it would mean
    hardcoding a name whitelist outside the config file (breaking the
    config-driven rule) or guessing from partial name fragments (breaking
    hard rule 1). Leaving this null and documented beats forcing a value;
    revisit once the registry's constituent-college data is structured."""
    return NULL_FIELD


def extract_programs(text: str) -> Field:
    if not text:
        return NULL_FIELD
    found = sorted(set(m.group(0) for m in _PROGRAM_PATTERN.finditer(text)))
    if not found:
        return NULL_FIELD
    return Field(value=found, confidence=0.6, note="keyword-spotted program tokens, not exhaustive")
