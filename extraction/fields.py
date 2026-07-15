"""Heuristic, regex-based field extraction from plain text.

No LLM calls here by design (CLAUDE.md: cost minimization, prefer lighter
methods). A field is only ever populated when a keyword-anchored pattern
matches unambiguously in the text; anything else stays null with no
confidence score (hard rule 1: never infer/guess a missing field).
"""
from __future__ import annotations

import re

from extraction.normalize import normalize_date_string, validate_deadline_value
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

_normalize_date = normalize_date_string  # local alias, kept for call-site brevity below

# "admissions_open" is its own classified signal, read directly off the page's
# own open/closed language -- never derived from whether extract_deadline's
# date has passed (CLAUDE.md Phase P: "not derived arithmetically from the
# deadline"). Absence of either phrase list stays null; it is never inferred
# as "closed" just because nothing was said (hard rule 1).
#
# Deliberately excluded, and why (don't "helpfully" re-add these):
# - "applications are invited" / "applications invited" / "inviting
#   applications" -- extremely common in real admission notices, but the
#   phrase itself is structurally identical to scholarship/job/tender/event-
#   registration language ("Applications are invited for the position of...").
#   Nothing in the phrase anchors it to student admissions, and unlike
#   extract_deadline's primary/secondary keyword tiering there's no
#   unambiguous variant of this one to prefer. This is exactly the page-
#   context judgment call the LLM field-extractor subagent is for -- its
#   contract already lets it recognize this phrasing "in its own words"
#   without a fixed list, so leaving it out of the regex fallback is the
#   conservative choice.
# - "apply now" -- generic marketing language ("Apply now for our
#   scholarship", "Apply now -- 20% off") that doesn't mention admissions or
#   applications-as-a-process at all. Removed as a precision fix.
# - "registration is open"/"registration closed" and their variants --
#   removed for the same reason as "apply now": "registration" is just as
#   commonly course/semester registration for already-enrolled students, or
#   event/webinar registration, as it is admissions ("Course registration is
#   open for continuing students" is not an admissions signal). Left to the
#   LLM path, same as "applications are invited".
# - "applications have commenced" -- unlike "admissions have commenced"
#   (kept below; "admissions" alone reliably means student admissions),
#   "applications" alone has the same scholarship/job/tender ambiguity as
#   "applications are invited" above ("Scholarship applications have
#   commenced..."). Only the "admissions"-subject form is kept.
# - Bare simple-past forms ("admissions opened", "were open") -- genuinely
#   tense-ambiguous ("Admissions opened last year" vs "...yesterday and are
#   still running") with no reliable keyword-level disambiguation.
#   "admissions have opened" (present perfect) is the safe version: it
#   unambiguously means "and still are", so that's what's listed below.
_ADMISSIONS_OPEN_PHRASES = [
    "applications are open", "admissions are open", "admission is open",
    "application is open", "applications open", "admission open",
    "admissions open", "admissions are now open", "applications are now open",
    "admissions now open", "applications now open", "admissions remain open",
    "applications remain open", "admissions have opened",
    "applications have opened", "admissions have commenced",
    "now accepting applications",
]
_ADMISSIONS_CLOSED_PHRASES = [
    "admissions closed", "admissions are closed", "admission is closed",
    "applications closed", "applications are closed", "application is closed",
    "admissions have closed", "applications have closed",
    "admissions are no longer open", "applications are no longer open",
    "admissions have ended", "applications have ended",
    "admission portal is closed", "application portal is closed",
]

# Same month-name set _DATE_PATTERN already trusts, reused here so both
# "recognize a date" checks in this file stay in sync.
_MONTH_NAMES = (
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)

# Word-boundary matched, not raw substring containment -- a plain `in` check
# would let e.g. "admission open" match inside "admission opens next month"
# (future tense, not currently open) since "open" is a prefix of "opens".
#
# The OPEN pattern also rejects a match immediately followed by "from"/"on" +
# something date-shaped (a digit or month name, e.g. "Admissions Open From:
# 1st August 2026" or "Admissions open on August 1, 2026") -- that phrasing
# states a *scheduled* date, and without knowing whether it's past or future
# relative to today, treating it as a current "Open" signal would be a guess.
# Deliberately requires a date-shaped continuation, not bare "from"/"on":
# an earlier version excluded any "on"/"from" unconditionally, which also
# nulled clearly-current statements like "admissions are open on all
# campuses" or "registration is open on our website" -- a real recall cost
# with no matching precision benefit, since those aren't schedule
# announcements at all. This narrower check still won't catch every
# scheduling phrasing (e.g. "admissions open starting from August 1" slips
# through, since "starting" sits between "open" and "from") -- accepted as
# the same rare-wording tradeoff already made elsewhere in this module,
# not chased further to keep this a simple heuristic. Not applied to the
# CLOSED pattern -- those phrases are all unambiguous present-tense-closed
# forms with no equivalent scheduling risk.
_ADMISSIONS_OPEN_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _ADMISSIONS_OPEN_PHRASES) + r")\b"
    # \d has no trailing \b: an ordinal like "1st" is a digit directly
    # followed by word-char letters, so a boundary assertion right after
    # the digit would never hold and silently defeat this whole branch.
    r"(?!\s+(?:from|on)\b[:\s]*(?:\d|(?:" + _MONTH_NAMES + r")\b))",
    re.IGNORECASE,
)
_ADMISSIONS_CLOSED_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _ADMISSIONS_CLOSED_PHRASES) + r")\b",
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
    a shorter keyword contained within it (e.g. "admission deadline" already
    anchors a match — the "deadline" inside it shouldn't count as a second,
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
        normalized = next(iter(distinct))
        if not validate_deadline_value(normalized):
            return Field(value=None, confidence=None, note="implausible deadline date — left null rather than guessed")
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
    # One implausible date among several candidates means we can't trust the
    # rest of the extraction either -- null the whole field rather than
    # silently dropping just the bad entry (same reasoning as the
    # conflicting-candidates null below: partial confidence in one part of a
    # multi-part answer isn't confidence in the parts that passed).
    if not all(validate_deadline_value(norm) for norm in by_date):
        return Field(value=None, confidence=None, note="implausible deadline date among candidates — left null rather than guessed")
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




def extract_admissions_open(text: str) -> Field:
    """Whether the page itself states admissions are currently open or
    closed, read as literal page language, not inferred from the deadline
    field (see the module-level comment above the phrase lists). Both an
    open and a closed phrase present is a genuine conflict (e.g. a page
    describing last cycle's closing alongside this cycle's opening, or one
    program's status alongside another's on the same page) and nulls rather
    than guesses which one is current; neither phrase present is simply "no
    signal", not "closed". This is a known simplification -- unlike
    extract_deadline's labeled multi-track list, there's no per-program
    breakdown here, and no scoping to "only sentences about undergraduate
    admissions" (that's the content-classifier's job, hard rule 3; a mixed
    UG/PG page's PG-specific open/closed language can still surface here).
    Both are left to the LLM field-extractor's contextual judgment rather
    than attempted with keyword matching."""
    if not text:
        return NULL_FIELD
    is_open = bool(_ADMISSIONS_OPEN_PATTERN.search(text))
    is_closed = bool(_ADMISSIONS_CLOSED_PATTERN.search(text))
    if is_open and is_closed:
        return Field(value=None, confidence=None, note="conflicting open/closed signals found — left null rather than guessed")
    if is_open:
        return Field(value="Open", confidence=0.8)
    if is_closed:
        return Field(value="Closed", confidence=0.8)
    return NULL_FIELD


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
