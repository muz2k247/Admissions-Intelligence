"""Tests for extraction/normalize.py::validate_deadline_value -- the shared
deadline plausibility bar used by both the regex extractor
(extraction/fields.py) and the LLM-output validation path
(extraction/run.py::_validate_deadline_field).

No live network calls -- pure function, in-memory only.
"""
from __future__ import annotations

import datetime as dt

from extraction.normalize import validate_deadline_value


class TestValidateDeadlineValue:
    def test_current_year_iso_date_is_valid(self):
        assert validate_deadline_value("2026-08-10", today=dt.date(2026, 7, 15)) is True

    def test_near_future_iso_date_is_valid(self):
        # +2 years from "today" is within the +3 tolerance.
        assert validate_deadline_value("2028-08-10", today=dt.date(2026, 7, 15)) is True

    def test_invalid_calendar_date_feb_30_is_rejected(self):
        assert validate_deadline_value("2026-02-30", today=dt.date(2026, 7, 15)) is False

    def test_invalid_calendar_date_month_13_is_rejected(self):
        assert validate_deadline_value("2026-13-01", today=dt.date(2026, 7, 15)) is False

    def test_year_four_years_in_future_is_rejected(self):
        # today.year + 4 is outside the +3 tolerance.
        assert validate_deadline_value("2030-08-10", today=dt.date(2026, 7, 15)) is False

    def test_year_at_max_offset_boundary_is_valid(self):
        # today.year + 3 is exactly the upper bound -- inclusive.
        assert validate_deadline_value("2029-08-10", today=dt.date(2026, 7, 15)) is True

    def test_year_two_years_in_past_is_rejected(self):
        # today.year - 2 is outside the -1 tolerance.
        assert validate_deadline_value("2024-08-10", today=dt.date(2026, 7, 15)) is False

    def test_year_at_min_offset_boundary_is_valid(self):
        # today.year - 1 is exactly the lower bound -- inclusive.
        assert validate_deadline_value("2025-08-10", today=dt.date(2026, 7, 15)) is True

    def test_non_iso_string_with_month_name_is_rejected(self):
        assert validate_deadline_value("15 August 2026", today=dt.date(2026, 7, 15)) is False

    def test_non_iso_slash_format_is_rejected(self):
        assert validate_deadline_value("15/08/2026", today=dt.date(2026, 7, 15)) is False

    def test_empty_string_is_rejected(self):
        assert validate_deadline_value("", today=dt.date(2026, 7, 15)) is False

    def test_none_value_is_rejected(self):
        assert validate_deadline_value(None, today=dt.date(2026, 7, 15)) is False

    def test_non_string_value_is_rejected(self):
        assert validate_deadline_value(12345, today=dt.date(2026, 7, 15)) is False

    def test_lowercased_fallback_string_is_rejected(self):
        # _normalize_date's honest "couldn't canonicalize" fallback (a
        # lowercased raw string) has no calendar date to validate.
        assert validate_deadline_value("some unparseable date text", today=dt.date(2026, 7, 15)) is False

    def test_whitespace_is_stripped_before_matching(self):
        assert validate_deadline_value("  2026-08-10  ", today=dt.date(2026, 7, 15)) is True

    def test_default_today_uses_wall_clock_and_accepts_current_year(self):
        # No today= passed -- must not crash and must accept the actual
        # current calendar year (deterministic regardless of *when* the
        # test suite runs, since it only checks the current year is valid).
        this_year = dt.date.today().year
        assert validate_deadline_value(f"{this_year}-01-01") is True

    def test_implausibly_old_year_1998_is_rejected(self):
        assert validate_deadline_value("1998-06-01", today=dt.date(2026, 7, 15)) is False

    def test_implausibly_future_year_2099_is_rejected(self):
        assert validate_deadline_value("2099-06-01", today=dt.date(2026, 7, 15)) is False
