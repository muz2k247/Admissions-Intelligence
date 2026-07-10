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
    "due date", "last day to apply", "submission deadline",
    "apply before", "closing on", "last date for",
]

# Primary keywords name the application deadline unambiguously. Secondary
# keywords ("deadline", "last date") are too generic — they match any
# deadline on the page (e.g. "Last Date for Receipt of Financial Assistance
# Documents"), which is a different thing entirely, not a second candidate
# for the same field. When a primary match exists, secondary matches are
# noise and get ignored rather than treated as a conflict (see
# extract_deadline). A genuine conflict between two primary matches, or
# between two secondary matches with no primary present, still nulls the
# field — this only suppresses false conflicts, never real ones.
_DEADLINE_KEYWORDS_PRIMARY = [
    "application deadline", "admission deadline", "last date to apply",
    "apply by", "apply before", "submission deadline",
]

_DATE_PATTERN = re.compile(
    r"\b(\d{1,2}(st|nd|rd|th)?[\s\-/]+(January|February|March|April|May|June|July|"
    r"August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|"
    r"Oct|Nov|Dec)[\s\-/,]+\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DMY_NAMED = re.compile(
    r"^(\d{1,2})(?:st|nd|rd|th)?[\s\-/]+([A-Za-z]+)[\s\-/,]+(\d{2,4})$",
    re.IGNORECASE,
)


def _normalize_date(raw: str) -> str:
    """Canonicalize a matched date string to YYYY-MM-DD when possible, so two
    different spellings of the same day ("15 July 2026", "2026-07-15") are
    recognized as one value and not mistaken for a conflict. Anything that
    doesn't parse cleanly falls back to its lowercased/stripped form — an
    honest "can't canonicalize" rather than a guess."""
    s = raw.strip()
    m = _ISO_DATE.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DMY_NAMED.match(s)
    if m:
        month = _MONTHS.get(m.group(2).lower()[:3])
        if month:
            day = int(m.group(1))
            year = m.group(3)
            if len(year) == 2:
                year = f"20{year}"
            return f"{year}-{month:02d}-{day:02d}"
    return s.lower()


_FEE_KEYWORDS = ["application fee", "processing fee", "admission fee", "fee"]

# Same primary/secondary split as deadlines: bare "fee" matches anything fee-
# shaped on the page (entry-test registration fee, semester fee, hostel
# fee, ...), which is not a second candidate for the application fee, just
# a different fee entirely. Primary keywords name the application fee
# specifically.
_FEE_KEYWORDS_PRIMARY = [
    "application fee", "admission fee", "processing fee",
    "application processing fee",
]

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


def _keyword_anchored_matches(text: str, keywords: list[str], value_pattern: re.Pattern) -> list[tuple[str, int]]:
    """Find value_pattern matches that occur within _WINDOW characters after
    a keyword occurrence. Returns (matched substring, keyword start index)
    pairs (values not normalized) — the index lets callers recover nearby
    page text (e.g. _nearby_label) to distinguish genuinely different
    candidates instead of just nulling on conflict.

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
                found.append((m.group(0).strip(), idx))
                claimed.append((idx, end))
    return found


_LABEL_LOOKBACK = 120
# A genuine multi-track case (e.g. NUST's NET vs ACT/SAT) is realistically a
# couple of tracks with short, clean headings. A page-wide schedule table
# (e.g. UHS's application-start/closing/merit-list dates across several
# admission cycles) produces many more candidates with much longer, messier
# row-fragment "labels" -- these caps stop that from ever being shown as a
# labeled list (verified against real scraped data: NUST is 2 candidates,
# 31-44 char labels; UHS is 7 candidates, 80-140+ char labels). Above either
# cap, extract_deadline falls through to the existing honest null instead.
_MAX_LABELED_CANDIDATES = 3
_MAX_LABEL_LENGTH = 70


def _nearby_label(text: str, keyword_idx: int, floor: int = 0) -> str | None:
    """Recover the page's own heading/context text immediately before a
    keyword match, to label genuinely different candidates (e.g. which
    entry-test track a "Last Date" belongs to) instead of guessing which
    one is "the" deadline. This reads literal source text, not an inferred
    judgment — same justification as the JS-widget synthesis in chunker.py.

    Looks back up to _LABEL_LOOKBACK chars, never earlier than `floor`
    (callers pass the previous candidate's keyword index here, so one
    candidate's label can never swallow an earlier candidate's own heading
    and date text). Drops a possibly-truncated leading fragment by starting
    after the first newline within that window (unless the window starts
    exactly at `floor`/the text start, where there's nothing to truncate),
    then joins the remaining non-empty lines with a space. Returns None if
    nothing usable precedes the keyword."""
    start = max(floor, keyword_idx - _LABEL_LOOKBACK)
    preceding = text[start:keyword_idx]
    if start > 0:
        first_nl = preceding.find("\n")
        if first_nl != -1:
            preceding = preceding[first_nl + 1:]
    label = " ".join(line.strip() for line in preceding.split("\n") if line.strip())
    return label or None


def extract_deadline(text: str) -> Field:
    if not text:
        return NULL_FIELD
    # Prefer unambiguous "application deadline"-style matches over generic
    # "last date"/"deadline" ones, which can belong to something else
    # entirely on the same page (see _DEADLINE_KEYWORDS_PRIMARY comment).
    # A conflict among primary matches, or among secondary matches when no
    # primary exists, still nulls the field below — only the false
    # primary-vs-secondary conflict is suppressed.
    matches = _keyword_anchored_matches(text, _DEADLINE_KEYWORDS_PRIMARY, _DATE_PATTERN)
    if not matches:
        matches = _keyword_anchored_matches(text, _DEADLINE_KEYWORDS, _DATE_PATTERN)
    if not matches:
        return NULL_FIELD
    distinct = {_normalize_date(v) for v, _ in matches}
    if len(distinct) == 1:
        confidence = 0.95 if len(matches) > 1 else 0.85
        return Field(value=matches[0][0], confidence=confidence)

    # Genuinely different dates. Before nulling, check whether each distinct
    # date has its own page-authored heading nearby (e.g. which entry-test
    # track it belongs to) -- if every one does, and the labels actually
    # distinguish them, list them labeled instead of discarding the
    # information. If any label is missing, too long, too many candidates
    # exist, or two labels collide, we genuinely can't tell what's what (or
    # this looks like a schedule-table dump, not a short program/track
    # list), so fall through to the honest null below rather than ever show
    # a partially- or wrongly-labeled list.
    by_date: dict[str, tuple[str, int]] = {}
    for value, idx in matches:
        norm = _normalize_date(value)
        by_date.setdefault(norm, (value, idx))
    labeled = []
    if len(by_date) <= _MAX_LABELED_CANDIDATES:
        # Sorted by position, each label's lookback is floored at the
        # previous candidate's own keyword position -- otherwise a later
        # candidate's label could swallow an earlier candidate's heading
        # and date text whole when the two sit close together in short
        # pages (see _nearby_label's floor parameter).
        ordered = sorted(by_date.values(), key=lambda pair: pair[1])
        floor = 0
        for value, idx in ordered:
            label = _nearby_label(text, idx, floor=floor)
            floor = idx
            if not label or len(label) > _MAX_LABEL_LENGTH:
                labeled = None
                break
            labeled.append((label, value))
    else:
        labeled = None
    if labeled is not None:
        labels = [label for label, _ in labeled]
        if len(set(labels)) == len(labeled):
            return Field(
                value=[{"label": label, "date": date} for label, date in labeled],
                confidence=0.75,
                note="multiple distinct deadlines found, one per program/track",
            )
    return Field(value=None, confidence=None, note="conflicting deadline candidates found — left null rather than guessed")


def extract_fee(text: str) -> Field:
    if not text:
        return NULL_FIELD
    matches = _keyword_anchored_matches(text, _FEE_KEYWORDS_PRIMARY, _FEE_PATTERN)
    if not matches:
        matches = _keyword_anchored_matches(text, _FEE_KEYWORDS, _FEE_PATTERN)
    if not matches:
        return NULL_FIELD
    values = [v for v, _ in matches]
    distinct = set(values)
    if len(distinct) == 1:
        confidence = 0.9 if len(values) > 1 else 0.8
        return Field(value=values[0], confidence=confidence)
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
