"""Tests for pipeline/schedule_gate.py -- the Phase S admin-configurable
pipeline scheduling engine.

No live network / no live Firestore: mocked the same way test_review.py and
test_overrides.py do it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

from pipeline.schedule_gate import (
    DEFAULT_SCHEDULE,
    _validate_schedule,
    compute_is_due,
    fetch_pipeline_schedule,
    fetch_run_request,
    is_run_requested,
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, raise_exc=None, json_exc=None):
        self._payload = payload
        self.status_code = status_code
        self._raise_exc = raise_exc
        self._json_exc = json_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = responses if isinstance(responses, list) else [responses]
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        result = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def _fs_string(v):
    return {"stringValue": v}


def _schedule_doc(fields: dict):
    fs_fields = {}
    for k, v in fields.items():
        if isinstance(v, bool):
            fs_fields[k] = {"booleanValue": v}
        elif isinstance(v, int):
            fs_fields[k] = {"integerValue": str(v)}
        elif isinstance(v, float):
            fs_fields[k] = {"doubleValue": v}
        elif isinstance(v, str):
            fs_fields[k] = {"stringValue": v}
    return {
        "name": "projects/test-proj/databases/(default)/documents/settings/pipeline_schedule",
        "fields": fs_fields,
    }


def _request_doc(requested_at: str | None):
    fields = {}
    if requested_at is not None:
        fields["requested_at"] = {"stringValue": requested_at}
    return {
        "name": "projects/test-proj/databases/(default)/documents/settings/pipeline_run_request",
        "fields": fields,
    }


def _utc(*args, **kwargs):
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _validate_schedule
# ---------------------------------------------------------------------------

class TestValidateSchedule:
    def test_manual_mode(self):
        assert _validate_schedule({"mode": "manual"}) == {"mode": "manual"}

    def test_missing_mode_defaults_to_manual(self):
        assert _validate_schedule({}) == DEFAULT_SCHEDULE

    def test_unknown_mode_defaults_to_manual(self):
        assert _validate_schedule({"mode": "hourly-ish"}) == DEFAULT_SCHEDULE

    def test_non_dict_defaults_to_manual(self):
        assert _validate_schedule("not-a-dict") == DEFAULT_SCHEDULE

    def test_interval_hours_valid(self):
        result = _validate_schedule({"mode": "interval_hours", "interval_hours": 6})
        assert result == {"mode": "interval_hours", "interval_hours": 6.0}

    def test_interval_hours_zero_defaults_to_manual(self):
        assert _validate_schedule({"mode": "interval_hours", "interval_hours": 0}) == DEFAULT_SCHEDULE

    def test_interval_hours_negative_defaults_to_manual(self):
        assert _validate_schedule({"mode": "interval_hours", "interval_hours": -3}) == DEFAULT_SCHEDULE

    def test_interval_hours_missing_defaults_to_manual(self):
        assert _validate_schedule({"mode": "interval_hours"}) == DEFAULT_SCHEDULE

    def test_interval_hours_bool_rejected(self):
        # bool is an int subclass -- must not be read as a valid hour count.
        assert _validate_schedule({"mode": "interval_hours", "interval_hours": True}) == DEFAULT_SCHEDULE

    def test_interval_hours_nan_rejected(self):
        # Firestore's REST API can legitimately encode a doubleValue as the
        # string "NaN" -- decode_value() turns it into float("nan"), which
        # slips past a bare `hours <= 0` check (nan <= 0 is False) and would
        # otherwise raise inside timedelta(hours=...) later.
        assert _validate_schedule({"mode": "interval_hours", "interval_hours": float("nan")}) == DEFAULT_SCHEDULE

    def test_interval_hours_infinity_rejected(self):
        assert _validate_schedule({"mode": "interval_hours", "interval_hours": float("inf")}) == DEFAULT_SCHEDULE
        assert _validate_schedule({"mode": "interval_hours", "interval_hours": float("-inf")}) == DEFAULT_SCHEDULE

    def test_interval_weeks_extreme_value_rejected(self):
        # Bounded at 520 weeks (10 years) to avoid overflowing
        # timedelta(weeks=...) with an implausible curator/CMS-bug value.
        assert _validate_schedule({
            "mode": "interval_weeks", "interval_weeks": 10 ** 9,
            "weekly_time_utc": "06:00", "interval_anchor": "2026-07-06",
        }) == DEFAULT_SCHEDULE

    def test_weekly_valid(self):
        result = _validate_schedule({"mode": "weekly", "weekly_day": 1, "weekly_time_utc": "06:00"})
        assert result == {"mode": "weekly", "weekly_day": 1, "weekly_time_utc": (6, 0)}

    def test_weekly_day_out_of_range_defaults_to_manual(self):
        assert _validate_schedule({"mode": "weekly", "weekly_day": 7, "weekly_time_utc": "06:00"}) == DEFAULT_SCHEDULE

    def test_weekly_bad_time_defaults_to_manual(self):
        assert _validate_schedule({"mode": "weekly", "weekly_day": 1, "weekly_time_utc": "9:5"}) == DEFAULT_SCHEDULE

    def test_weekly_missing_time_defaults_to_manual(self):
        assert _validate_schedule({"mode": "weekly", "weekly_day": 1}) == DEFAULT_SCHEDULE

    def test_interval_weeks_valid(self):
        result = _validate_schedule({
            "mode": "interval_weeks", "interval_weeks": 2,
            "weekly_time_utc": "06:00", "interval_anchor": "2026-07-06",
        })
        assert result["mode"] == "interval_weeks"
        assert result["interval_weeks"] == 2
        assert result["weekly_time_utc"] == (6, 0)
        assert result["interval_anchor"] == _utc(2026, 7, 6)

    def test_interval_weeks_bad_anchor_defaults_to_manual(self):
        assert _validate_schedule({
            "mode": "interval_weeks", "interval_weeks": 2,
            "weekly_time_utc": "06:00", "interval_anchor": "not-a-date",
        }) == DEFAULT_SCHEDULE

    def test_monthly_valid(self):
        result = _validate_schedule({"mode": "monthly", "monthly_day": 15, "weekly_time_utc": "12:30"})
        assert result == {"mode": "monthly", "monthly_day": 15, "weekly_time_utc": (12, 30)}

    def test_monthly_day_31_rejected(self):
        # Clamped to 1-28 so every month has that day -- no Feb-29 guessing.
        assert _validate_schedule({"mode": "monthly", "monthly_day": 31, "weekly_time_utc": "12:30"}) == DEFAULT_SCHEDULE

    def test_monthly_day_zero_rejected(self):
        assert _validate_schedule({"mode": "monthly", "monthly_day": 0, "weekly_time_utc": "12:30"}) == DEFAULT_SCHEDULE

    def test_extra_unexpected_keys_are_ignored(self):
        # A document written by a future CMS version (or a stray manual
        # edit) may carry fields _validate_schedule doesn't know about --
        # they must be dropped silently, not cause a manual fallback or
        # leak into the normalized result.
        result = _validate_schedule({
            "mode": "weekly", "weekly_day": 4, "weekly_time_utc": "06:00",
            "unexpected_key": "ignored", "another": 123,
        })
        assert result == {"mode": "weekly", "weekly_day": 4, "weekly_time_utc": (6, 0)}

    def test_interval_hours_extra_keys_ignored(self):
        result = _validate_schedule({"mode": "interval_hours", "interval_hours": 6, "stray": True})
        assert result == {"mode": "interval_hours", "interval_hours": 6.0}


# ---------------------------------------------------------------------------
# compute_is_due
# ---------------------------------------------------------------------------

class TestComputeIsDue:
    def test_manual_never_due(self):
        assert compute_is_due({"mode": "manual"}, _utc(2026, 7, 16), None) is False
        assert compute_is_due({"mode": "manual"}, _utc(2026, 7, 16), _utc(2000, 1, 1)) is False

    def test_interval_hours_due_when_never_run(self):
        schedule = {"mode": "interval_hours", "interval_hours": 6.0}
        assert compute_is_due(schedule, _utc(2026, 7, 16, 6), None) is True

    def test_interval_hours_not_yet_due(self):
        schedule = {"mode": "interval_hours", "interval_hours": 6.0}
        last_run = _utc(2026, 7, 16, 6, 0)
        assert compute_is_due(schedule, _utc(2026, 7, 16, 10), last_run) is False

    def test_interval_hours_due_after_elapsed(self):
        schedule = {"mode": "interval_hours", "interval_hours": 6.0}
        last_run = _utc(2026, 7, 16, 6, 0)
        assert compute_is_due(schedule, _utc(2026, 7, 16, 12, 0), last_run) is True

    def test_interval_hours_malformed_schedule_never_due(self):
        assert compute_is_due({"mode": "interval_hours"}, _utc(2026, 7, 16), None) is False

    def test_weekly_due_on_matching_day_and_time(self):
        # 2026-07-16 is a Thursday (weekly_day=4).
        schedule = {"mode": "weekly", "weekly_day": 4, "weekly_time_utc": (6, 0)}
        assert compute_is_due(schedule, _utc(2026, 7, 16, 6, 5), None) is True

    def test_weekly_not_due_before_matching_time_same_week(self):
        schedule = {"mode": "weekly", "weekly_day": 4, "weekly_time_utc": (6, 0)}
        # Now is Wednesday, before this week's Thursday slot -- last
        # occurrence is LAST week's Thursday.
        last_run = _utc(2026, 7, 9, 6, 0)  # last week's Thursday run already covered it
        assert compute_is_due(schedule, _utc(2026, 7, 15, 12, 0), last_run) is False

    def test_weekly_not_due_twice_same_slot(self):
        schedule = {"mode": "weekly", "weekly_day": 4, "weekly_time_utc": (6, 0)}
        last_run = _utc(2026, 7, 16, 6, 0)
        assert compute_is_due(schedule, _utc(2026, 7, 16, 8, 0), last_run) is False

    def test_weekly_due_next_week_after_previous_run(self):
        schedule = {"mode": "weekly", "weekly_day": 4, "weekly_time_utc": (6, 0)}
        last_run = _utc(2026, 7, 16, 6, 0)  # this week's run
        assert compute_is_due(schedule, _utc(2026, 7, 23, 6, 5), last_run) is True

    def test_interval_weeks_anchor_in_future_not_due(self):
        schedule = {
            "mode": "interval_weeks", "interval_weeks": 2,
            "weekly_time_utc": (6, 0), "interval_anchor": _utc(2026, 8, 1),
        }
        assert compute_is_due(schedule, _utc(2026, 7, 16), None) is False

    def test_interval_weeks_due_on_anchor(self):
        schedule = {
            "mode": "interval_weeks", "interval_weeks": 2,
            "weekly_time_utc": (6, 0), "interval_anchor": _utc(2026, 7, 6),
        }
        assert compute_is_due(schedule, _utc(2026, 7, 6, 6, 5), None) is True

    def test_interval_weeks_not_due_between_periods(self):
        schedule = {
            "mode": "interval_weeks", "interval_weeks": 2,
            "weekly_time_utc": (6, 0), "interval_anchor": _utc(2026, 7, 6),
        }
        last_run = _utc(2026, 7, 6, 6, 0)
        # One week later -- not a 2-week boundary yet.
        assert compute_is_due(schedule, _utc(2026, 7, 13, 6, 5), last_run) is False

    def test_interval_weeks_due_at_next_period(self):
        schedule = {
            "mode": "interval_weeks", "interval_weeks": 2,
            "weekly_time_utc": (6, 0), "interval_anchor": _utc(2026, 7, 6),
        }
        last_run = _utc(2026, 7, 6, 6, 0)
        assert compute_is_due(schedule, _utc(2026, 7, 20, 6, 5), last_run) is True

    def test_monthly_due_on_matching_day(self):
        schedule = {"mode": "monthly", "monthly_day": 15, "weekly_time_utc": (12, 0)}
        assert compute_is_due(schedule, _utc(2026, 7, 15, 12, 5), None) is True

    def test_monthly_not_due_before_this_months_slot(self):
        schedule = {"mode": "monthly", "monthly_day": 15, "weekly_time_utc": (12, 0)}
        last_run = _utc(2026, 6, 15, 12, 0)  # last month's run already covered it
        assert compute_is_due(schedule, _utc(2026, 7, 10), last_run) is False

    def test_monthly_due_crossing_year_boundary(self):
        # "Now" is early January, before this month's slot -- most recent
        # occurrence is December of the previous year.
        schedule = {"mode": "monthly", "monthly_day": 15, "weekly_time_utc": (12, 0)}
        last_run = _utc(2025, 11, 15, 12, 0)
        assert compute_is_due(schedule, _utc(2026, 1, 5), last_run) is True

    def test_unknown_mode_never_due(self):
        assert compute_is_due({"mode": "bogus"}, _utc(2026, 7, 16), None) is False

    def test_extra_unexpected_keys_in_schedule_dict_dont_break_computation(self):
        # A schedule dict carrying extra keys beyond what each mode branch
        # reads (e.g. leftover fields from a different mode, or CMS
        # metadata) must not affect the result -- only schedule.get(...) of
        # the keys each branch actually needs should matter.
        schedule = {"mode": "interval_hours", "interval_hours": 6.0, "stray_field": "value"}
        assert compute_is_due(schedule, _utc(2026, 7, 16, 6), None) is True

    def test_weekly_due_at_exact_time_with_nonzero_seconds_in_now(self):
        # "now" carries sub-minute precision a fraction past the scheduled
        # slot -- candidate truncates seconds/microseconds to 0, so it must
        # still register as the same, already-elapsed occurrence rather than
        # being pushed to "not due yet" or skipped entirely.
        schedule = {"mode": "weekly", "weekly_day": 4, "weekly_time_utc": (6, 0)}
        now = _utc(2026, 7, 16, 6, 0, 0, 1)  # 1 microsecond past the exact minute
        assert compute_is_due(schedule, now, None) is True
        # And once a run has covered that exact slot, it's no longer due.
        assert compute_is_due(schedule, now, _utc(2026, 7, 16, 6, 0)) is False

    def test_monthly_day_28_due_in_non_leap_february(self):
        schedule = {"mode": "monthly", "monthly_day": 28, "weekly_time_utc": (0, 0)}
        assert compute_is_due(schedule, _utc(2026, 2, 28, 0, 5), None) is True

    def test_monthly_day_28_due_in_leap_february(self):
        # 2028 is a leap year -- day 28 must still resolve to Feb 28 (not
        # accidentally shifted by the Feb 29 that also exists that year).
        schedule = {"mode": "monthly", "monthly_day": 28, "weekly_time_utc": (0, 0)}
        assert compute_is_due(schedule, _utc(2028, 2, 28, 0, 5), None) is True

    def test_monthly_day_28_not_due_twice_in_leap_february(self):
        schedule = {"mode": "monthly", "monthly_day": 28, "weekly_time_utc": (0, 0)}
        last_run = _utc(2028, 2, 28, 0, 0)
        assert compute_is_due(schedule, _utc(2028, 3, 1), last_run) is False

    def test_interval_weeks_boundary_one_second_before_period(self):
        schedule = {
            "mode": "interval_weeks", "interval_weeks": 2,
            "weekly_time_utc": (6, 0), "interval_anchor": _utc(2026, 7, 6),
        }
        last_run = _utc(2026, 7, 6, 6, 0)
        one_second_before_next_period = _utc(2026, 7, 20, 5, 59, 59)
        assert compute_is_due(schedule, one_second_before_next_period, last_run) is False

    def test_interval_weeks_boundary_exact_second_of_period(self):
        schedule = {
            "mode": "interval_weeks", "interval_weeks": 2,
            "weekly_time_utc": (6, 0), "interval_anchor": _utc(2026, 7, 6),
        }
        last_run = _utc(2026, 7, 6, 6, 0)
        exact_next_period = _utc(2026, 7, 20, 6, 0)
        assert compute_is_due(schedule, exact_next_period, last_run) is True

    def test_interval_weeks_negative_interval_is_rejected_by_compute_is_due(self):
        # Fixed: compute_is_due now bounds-checks interval_weeks itself
        # (0 < weeks <= 520) rather than trusting every caller to have
        # routed the schedule dict through _validate_schedule first, so a
        # hand-built/malformed dict with a negative interval fails closed
        # to "not due" instead of reaching _last_interval_weeks_occurrence,
        # whose floor-division arithmetic would otherwise report a future
        # timestamp as an "occurrence <= now" (still true of that private
        # helper in isolation -- see the direct call below -- but no
        # longer reachable through the public compute_is_due contract).
        from pipeline.schedule_gate import _last_interval_weeks_occurrence

        anchor = _utc(2026, 7, 6)
        now = _utc(2026, 7, 16, 6, 5)
        occurrence = _last_interval_weeks_occurrence(anchor, -2, (6, 0), now)
        assert occurrence is not None
        assert occurrence > now  # the helper itself still has this quirk

        schedule = {
            "mode": "interval_weeks", "interval_weeks": -2,
            "weekly_time_utc": (6, 0), "interval_anchor": anchor,
        }
        assert compute_is_due(schedule, now, None) is False

    def test_interval_weeks_extreme_interval_is_rejected_by_compute_is_due(self):
        # Fixed: compute_is_due bounds interval_weeks to <= 520 before ever
        # calling _last_interval_weeks_occurrence, so an extreme value fails
        # closed to "not due" through the public contract instead of
        # overflowing datetime.timedelta. The private helper itself is
        # still unguarded in isolation (documented below), which is fine --
        # it's only reachable via compute_is_due or _validate_schedule's
        # own bound, both of which now gate it.
        import pytest

        from pipeline.schedule_gate import _last_interval_weeks_occurrence

        anchor = _utc(2026, 7, 6)
        now = _utc(2026, 7, 16, 6, 5)
        with pytest.raises(OverflowError):
            _last_interval_weeks_occurrence(anchor, 268_435_456, (6, 0), now)

        schedule = {
            "mode": "interval_weeks", "interval_weeks": 268_435_456,
            "weekly_time_utc": (6, 0), "interval_anchor": anchor,
        }
        assert compute_is_due(schedule, now, None) is False

    def test_unknown_mode_never_due_even_with_otherwise_valid_fields(self):
        # An unknown mode string alongside otherwise-plausible fields for
        # other modes must still fall through to "never due", not
        # accidentally match a known branch via a stray shared key name.
        schedule = {"mode": "yearly", "interval_hours": 1.0, "weekly_day": 1, "weekly_time_utc": (0, 0)}
        assert compute_is_due(schedule, _utc(2026, 7, 16), None) is False


# ---------------------------------------------------------------------------
# is_run_requested
# ---------------------------------------------------------------------------

class TestIsRunRequested:
    def test_no_request_is_false(self):
        assert is_run_requested(None, None) is False
        assert is_run_requested(None, _utc(2026, 7, 1)) is False

    def test_request_with_no_prior_dispatch_is_true(self):
        assert is_run_requested(_utc(2026, 7, 16), None) is True

    def test_request_before_last_dispatch_is_satisfied(self):
        assert is_run_requested(_utc(2026, 7, 10), _utc(2026, 7, 16)) is False

    def test_request_after_last_dispatch_is_pending(self):
        assert is_run_requested(_utc(2026, 7, 16), _utc(2026, 7, 10)) is True

    def test_request_equal_to_last_dispatch_is_satisfied(self):
        t = _utc(2026, 7, 16)
        assert is_run_requested(t, t) is False

    def test_last_dispatch_at_none_with_requested_at_far_in_the_past_is_still_true(self):
        # No dispatch has ever happened, so even a very old pending request
        # must read as still-pending -- there is no "too old to count"
        # cutoff in the contract.
        assert is_run_requested(_utc(1971, 1, 1), None) is True


# ---------------------------------------------------------------------------
# fetch_pipeline_schedule
# ---------------------------------------------------------------------------

class TestFetchPipelineSchedule:
    def test_fetches_and_validates(self):
        doc = _schedule_doc({"mode": "interval_hours", "interval_hours": 6})
        session = FakeSession(FakeResponse(doc))
        result = fetch_pipeline_schedule(project_id="test-proj", session=session)
        assert result == {"mode": "interval_hours", "interval_hours": 6.0}

    def test_missing_document_returns_manual_default(self):
        session = FakeSession(FakeResponse(status_code=404))
        assert fetch_pipeline_schedule(project_id="test-proj", session=session) == DEFAULT_SCHEDULE

    def test_network_error_returns_manual_default(self):
        session = FakeSession(requests.ConnectionError("boom"))
        assert fetch_pipeline_schedule(project_id="test-proj", session=session) == DEFAULT_SCHEDULE

    def test_no_project_id_returns_manual_default_without_network(self, monkeypatch):
        monkeypatch.setattr("pipeline.schedule_gate.load_project_id", lambda: None)
        session = FakeSession(FakeResponse(_schedule_doc({"mode": "interval_hours", "interval_hours": 6})))
        result = fetch_pipeline_schedule(project_id=None, session=session)
        assert result == DEFAULT_SCHEDULE
        assert session.calls == []

    def test_malformed_json_returns_manual_default(self):
        session = FakeSession(FakeResponse(json_exc=json.JSONDecodeError("bad", "", 0)))
        assert fetch_pipeline_schedule(project_id="test-proj", session=session) == DEFAULT_SCHEDULE

    def test_malformed_document_body_returns_manual_default(self):
        doc = {"name": "projects/test-proj/databases/(default)/documents/settings/pipeline_schedule", "fields": "not-a-dict"}
        session = FakeSession(FakeResponse(doc))
        assert fetch_pipeline_schedule(project_id="test-proj", session=session) == DEFAULT_SCHEDULE

    def test_document_with_extra_unexpected_fields_still_validates(self):
        # A document containing fields beyond the schema (e.g. an audit
        # trail like created_by/updated_at, or a nested map/array field)
        # must not break decoding or push the result to the manual
        # fallback -- only the fields _validate_schedule actually inspects
        # should matter.
        doc = {
            "name": "projects/test-proj/databases/(default)/documents/settings/pipeline_schedule",
            "fields": {
                "mode": {"stringValue": "interval_hours"},
                "interval_hours": {"doubleValue": 6.0},
                "created_by": {"stringValue": "admin@example.com"},
                "notes": {"mapValue": {"fields": {}}},
                "history": {"arrayValue": {"values": [{"stringValue": "x"}]}},
            },
        }
        session = FakeSession(FakeResponse(doc))
        result = fetch_pipeline_schedule(project_id="test-proj", session=session)
        assert result == {"mode": "interval_hours", "interval_hours": 6.0}

    def test_fetch_document_is_a_single_request_no_pagination(self):
        # fetch_pipeline_schedule/fetch_run_request both go through
        # fetch_document (single-doc GET), not fetch_collection
        # (paginated) -- confirms there's no nextPageToken concept here at
        # all, so pagination edge cases don't apply to this module.
        session = FakeSession(FakeResponse(_schedule_doc({"mode": "manual"})))
        fetch_pipeline_schedule(project_id="test-proj", session=session)
        assert len(session.calls) == 1
        assert (session.calls[0]["params"] or {}).get("pageToken") is None


# ---------------------------------------------------------------------------
# fetch_run_request
# ---------------------------------------------------------------------------

class TestFetchRunRequest:
    def test_fetches_and_parses(self):
        session = FakeSession(FakeResponse(_request_doc("2026-07-16T06:00:00Z")))
        result = fetch_run_request(project_id="test-proj", session=session)
        assert result == _utc(2026, 7, 16, 6, 0)

    def test_missing_document_returns_none(self):
        session = FakeSession(FakeResponse(status_code=404))
        assert fetch_run_request(project_id="test-proj", session=session) is None

    def test_network_error_returns_none(self):
        session = FakeSession(requests.ConnectionError("boom"))
        assert fetch_run_request(project_id="test-proj", session=session) is None

    def test_no_project_id_returns_none_without_network(self, monkeypatch):
        monkeypatch.setattr("pipeline.schedule_gate.load_project_id", lambda: None)
        session = FakeSession(FakeResponse(_request_doc("2026-07-16T06:00:00Z")))
        assert fetch_run_request(project_id=None, session=session) is None
        assert session.calls == []

    def test_malformed_timestamp_returns_none(self):
        session = FakeSession(FakeResponse(_request_doc("not-a-timestamp")))
        assert fetch_run_request(project_id="test-proj", session=session) is None

    def test_missing_requested_at_field_returns_none(self):
        session = FakeSession(FakeResponse(_request_doc(None)))
        assert fetch_run_request(project_id="test-proj", session=session) is None

    def test_requested_at_present_but_null_typed_returns_none(self):
        # Firestore can store an explicit null for a field that exists in
        # the document (as opposed to the field being absent entirely).
        # decode_value maps {"nullValue": None} -> None, and since None is
        # not a str, fetch_run_request must treat this the same as "no
        # request" -- not raise, not coerce a fallback string, not crash on
        # .replace("Z", ...) being called against None.
        doc = {
            "name": "projects/test-proj/databases/(default)/documents/settings/pipeline_run_request",
            "fields": {"requested_at": {"nullValue": None}},
        }
        session = FakeSession(FakeResponse(doc))
        assert fetch_run_request(project_id="test-proj", session=session) is None
