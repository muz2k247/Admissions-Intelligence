"""Shared date normalization + plausibility validation.

Both extraction paths (extraction/fields.py's regex extractor and the
field-extractor LLM subagent, merged in extraction/run.py) can produce a
deadline string in varying formats, and either can produce one that parses
cleanly as a date but is still wrong -- a typo'd year, an unrelated date on
the page, a hallucination. This module is the single place that canonicalizes
a raw date string and decides whether the result is plausible, so both paths
normalize and reject the same way instead of drifting.
"""
from __future__ import annotations

import datetime as dt
import re

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DMY_NAMED = re.compile(
    r"^(\d{1,2})(?:st|nd|rd|th)?[\s\-/]+([A-Za-z]+)[\s\-/,]+(\d{2,4})$",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# This pipeline only extracts near-term admission-cycle deadlines. A parsed
# date outside [this year - 1, this year + 3] is far more likely a
# mis-extraction (wrong century, stray year on the page, hallucination) than
# a genuine deadline -- the -1 tolerates a still-posted stale notice, +3
# covers a couple of future admission cycles without accepting typos like
# "2099" or "1998".
_MIN_YEAR_OFFSET = -1
_MAX_YEAR_OFFSET = 3


def normalize_date_string(raw: str) -> str:
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


def validate_deadline_value(value: str, today: dt.date | None = None) -> bool:
    """True if `value` is a real, plausibly-dated YYYY-MM-DD deadline.

    Expects an already-normalized value (see normalize_date_string) — a
    value that fell back to a lowercased raw string (couldn't be
    canonicalized at all) is rejected here too, since there's no calendar
    date to validate."""
    if not isinstance(value, str):
        return False
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value.strip())
    if not m:
        return False
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        parsed = dt.date(year, month, day)
    except ValueError:
        return False
    today = today or dt.date.today()
    return (today.year + _MIN_YEAR_OFFSET) <= parsed.year <= (today.year + _MAX_YEAR_OFFSET)
