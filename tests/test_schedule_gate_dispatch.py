"""Tests for pipeline/schedule_gate.py's GitHub Actions integration (Phase S
tick side): fetch_workflow_runs, summarize_runs, dispatch_workflow, main().

No live network / no live GitHub API: mocked the same way test_review.py
and test_overrides.py mock Firestore.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from pipeline.schedule_gate import (
    dispatch_workflow,
    fetch_workflow_runs,
    main,
    summarize_runs,
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
    def __init__(self, get_responses=None, post_responses=None):
        self._get_responses = get_responses if isinstance(get_responses, list) else [get_responses]
        self._post_responses = post_responses if isinstance(post_responses, list) else [post_responses]
        self.get_calls = []
        self.post_calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.get_calls.append({"url": url, "headers": headers, "params": params})
        result = self._get_responses[min(len(self.get_calls) - 1, len(self._get_responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, url, headers=None, json=None, timeout=None):
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        result = self._post_responses[min(len(self.post_calls) - 1, len(self._post_responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def _utc(*args, **kwargs):
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


def _run(status, created_at):
    return {"status": status, "created_at": created_at}


# ---------------------------------------------------------------------------
# fetch_workflow_runs
# ---------------------------------------------------------------------------

class TestFetchWorkflowRuns:
    def test_fetches_runs(self):
        payload = {"workflow_runs": [_run("completed", "2026-07-16T06:00:00Z")]}
        session = FakeSession(get_responses=FakeResponse(payload))
        result = fetch_workflow_runs("owner/repo", "tok", session)
        assert result == payload["workflow_runs"]

    def test_no_runs_yet_returns_empty_list_not_none(self):
        session = FakeSession(get_responses=FakeResponse({"workflow_runs": []}))
        result = fetch_workflow_runs("owner/repo", "tok", session)
        assert result == []

    def test_network_error_returns_none(self):
        session = FakeSession(get_responses=requests.ConnectionError("boom"))
        assert fetch_workflow_runs("owner/repo", "tok", session) is None

    def test_http_error_returns_none(self):
        session = FakeSession(get_responses=FakeResponse(status_code=500))
        assert fetch_workflow_runs("owner/repo", "tok", session) is None

    def test_malformed_body_returns_none(self):
        session = FakeSession(get_responses=FakeResponse("not-a-dict"))
        assert fetch_workflow_runs("owner/repo", "tok", session) is None

    def test_workflow_runs_not_a_list_returns_none(self):
        session = FakeSession(get_responses=FakeResponse({"workflow_runs": "oops"}))
        assert fetch_workflow_runs("owner/repo", "tok", session) is None

    def test_sends_bearer_token(self):
        session = FakeSession(get_responses=FakeResponse({"workflow_runs": []}))
        fetch_workflow_runs("owner/repo", "my-token", session)
        assert session.get_calls[0]["headers"]["Authorization"] == "Bearer my-token"


# ---------------------------------------------------------------------------
# summarize_runs
# ---------------------------------------------------------------------------

class TestSummarizeRuns:
    def test_empty_list_returns_none_and_not_active(self):
        assert summarize_runs([]) == (None, False)

    def test_single_completed_run(self):
        runs = [_run("completed", "2026-07-16T06:00:00Z")]
        last, active = summarize_runs(runs)
        assert last == _utc(2026, 7, 16, 6, 0)
        assert active is False

    def test_in_progress_run_is_active(self):
        runs = [_run("in_progress", "2026-07-16T06:00:00Z")]
        _, active = summarize_runs(runs)
        assert active is True

    def test_queued_run_is_active(self):
        runs = [_run("queued", "2026-07-16T06:00:00Z")]
        _, active = summarize_runs(runs)
        assert active is True

    def test_most_recent_created_at_wins(self):
        runs = [
            _run("completed", "2026-07-01T00:00:00Z"),
            _run("completed", "2026-07-16T06:00:00Z"),
            _run("completed", "2026-07-10T00:00:00Z"),
        ]
        last, _ = summarize_runs(runs)
        assert last == _utc(2026, 7, 16, 6, 0)

    def test_malformed_created_at_is_skipped_not_fatal(self):
        runs = [_run("completed", "not-a-timestamp"), _run("completed", "2026-07-16T06:00:00Z")]
        last, _ = summarize_runs(runs)
        assert last == _utc(2026, 7, 16, 6, 0)

    def test_non_dict_run_entry_does_not_crash(self):
        runs = ["not-a-dict", _run("completed", "2026-07-16T06:00:00Z")]
        last, active = summarize_runs(runs)
        assert last == _utc(2026, 7, 16, 6, 0)
        assert active is False

    def test_all_malformed_created_at_returns_none(self):
        runs = [_run("completed", "garbage")]
        last, _ = summarize_runs(runs)
        assert last is None


# ---------------------------------------------------------------------------
# dispatch_workflow
# ---------------------------------------------------------------------------

class TestDispatchWorkflow:
    def test_success_returns_true(self):
        session = FakeSession(post_responses=FakeResponse(status_code=204))
        assert dispatch_workflow("owner/repo", "tok", session) is True

    def test_http_error_returns_false(self):
        session = FakeSession(post_responses=FakeResponse(status_code=403))
        assert dispatch_workflow("owner/repo", "tok", session) is False

    def test_network_error_returns_false(self):
        session = FakeSession(post_responses=requests.ConnectionError("boom"))
        assert dispatch_workflow("owner/repo", "tok", session) is False

    def test_posts_ref_in_body(self):
        session = FakeSession(post_responses=FakeResponse(status_code=204))
        dispatch_workflow("owner/repo", "tok", session, ref="main")
        assert session.post_calls[0]["json"] == {"ref": "main"}


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_missing_env_vars_returns_error_without_network(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        assert main() == 1

    def test_unknown_run_state_skips_without_dispatch(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr("pipeline.schedule_gate.fetch_pipeline_schedule", lambda session=None: {"mode": "manual"})
        monkeypatch.setattr("pipeline.schedule_gate.fetch_run_request", lambda session=None: None)
        monkeypatch.setattr("pipeline.schedule_gate.fetch_workflow_runs", lambda *a, **k: None)
        dispatched = {"called": False}
        monkeypatch.setattr("pipeline.schedule_gate.dispatch_workflow", lambda *a, **k: dispatched.update(called=True) or True)

        assert main() == 0
        assert dispatched["called"] is False

    def test_active_run_skips_without_dispatch(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr("pipeline.schedule_gate.fetch_pipeline_schedule", lambda session=None: {"mode": "interval_hours", "interval_hours": 1.0})
        monkeypatch.setattr("pipeline.schedule_gate.fetch_run_request", lambda session=None: None)
        monkeypatch.setattr("pipeline.schedule_gate.fetch_workflow_runs", lambda *a, **k: [_run("in_progress", "2026-07-16T06:00:00Z")])
        dispatched = {"called": False}
        monkeypatch.setattr("pipeline.schedule_gate.dispatch_workflow", lambda *a, **k: dispatched.update(called=True) or True)

        assert main() == 0
        assert dispatched["called"] is False

    def test_not_due_skips_without_dispatch(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr("pipeline.schedule_gate.fetch_pipeline_schedule", lambda session=None: {"mode": "manual"})
        monkeypatch.setattr("pipeline.schedule_gate.fetch_run_request", lambda session=None: None)
        monkeypatch.setattr("pipeline.schedule_gate.fetch_workflow_runs", lambda *a, **k: [_run("completed", "2026-07-16T06:00:00Z")])
        dispatched = {"called": False}
        monkeypatch.setattr("pipeline.schedule_gate.dispatch_workflow", lambda *a, **k: dispatched.update(called=True) or True)

        assert main() == 0
        assert dispatched["called"] is False

    def test_due_by_schedule_dispatches(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr("pipeline.schedule_gate.fetch_pipeline_schedule", lambda session=None: {"mode": "interval_hours", "interval_hours": 1.0})
        monkeypatch.setattr("pipeline.schedule_gate.fetch_run_request", lambda session=None: None)
        monkeypatch.setattr("pipeline.schedule_gate.fetch_workflow_runs", lambda *a, **k: [_run("completed", "2000-01-01T00:00:00Z")])
        dispatched = {"called": False}
        monkeypatch.setattr("pipeline.schedule_gate.dispatch_workflow", lambda *a, **k: dispatched.update(called=True) or True)

        assert main() == 0
        assert dispatched["called"] is True

    def test_due_by_manual_request_dispatches_even_in_manual_mode(self, monkeypatch):
        # "Run Now" must work regardless of the cadence mode -- manual mode
        # only gates the automatic cadence trigger, not the button.
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr("pipeline.schedule_gate.fetch_pipeline_schedule", lambda session=None: {"mode": "manual"})
        monkeypatch.setattr("pipeline.schedule_gate.fetch_run_request", lambda session=None: _utc(2026, 7, 16))
        monkeypatch.setattr("pipeline.schedule_gate.fetch_workflow_runs", lambda *a, **k: [_run("completed", "2000-01-01T00:00:00Z")])
        dispatched = {"called": False}
        monkeypatch.setattr("pipeline.schedule_gate.dispatch_workflow", lambda *a, **k: dispatched.update(called=True) or True)

        assert main() == 0
        assert dispatched["called"] is True

    def test_failed_dispatch_returns_nonzero(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr("pipeline.schedule_gate.fetch_pipeline_schedule", lambda session=None: {"mode": "interval_hours", "interval_hours": 1.0})
        monkeypatch.setattr("pipeline.schedule_gate.fetch_run_request", lambda session=None: None)
        monkeypatch.setattr("pipeline.schedule_gate.fetch_workflow_runs", lambda *a, **k: [_run("completed", "2000-01-01T00:00:00Z")])
        monkeypatch.setattr("pipeline.schedule_gate.dispatch_workflow", lambda *a, **k: False)

        assert main() == 1


# ---------------------------------------------------------------------------
# Additional coverage: active-status completeness, ordering independence,
# dispatch HTTP status variety, main() due_by_schedule/due_by_request
# interaction, and main()'s behavior if the "never raises" fetchers
# unexpectedly do.
# ---------------------------------------------------------------------------

class TestSummarizeRunsActiveStatusCompleteness:
    """GitHub's documented run statuses include queued/in_progress plus the
    less-common requested/waiting/pending -- confirm every one of them is
    recognized as active, not just the two most commonly seen in practice."""

    def test_requested_status_is_active(self):
        runs = [_run("requested", "2026-07-16T06:00:00Z")]
        _, active = summarize_runs(runs)
        assert active is True

    def test_waiting_status_is_active(self):
        runs = [_run("waiting", "2026-07-16T06:00:00Z")]
        _, active = summarize_runs(runs)
        assert active is True

    def test_pending_status_is_active(self):
        runs = [_run("pending", "2026-07-16T06:00:00Z")]
        _, active = summarize_runs(runs)
        assert active is True

    def test_mixed_runs_some_active_some_not_is_active(self):
        # One completed, one still queued -- any active run must block.
        runs = [
            _run("completed", "2026-07-16T05:00:00Z"),
            _run("queued", "2026-07-16T06:00:00Z"),
            _run("completed", "2026-07-15T00:00:00Z"),
        ]
        last, active = summarize_runs(runs)
        assert active is True
        assert last == _utc(2026, 7, 16, 6, 0)

    def test_mixed_runs_none_active_is_not_active(self):
        runs = [
            _run("completed", "2026-07-16T05:00:00Z"),
            _run("cancelled", "2026-07-16T06:00:00Z"),
            _run("failure", "2026-07-15T00:00:00Z"),
        ]
        _, active = summarize_runs(runs)
        assert active is False

    def test_most_recent_run_not_first_in_api_order_still_wins(self):
        # GitHub's default ordering is newest-first, but the function must not
        # rely on that -- feed it deliberately out of that order (oldest
        # first, with the true most-recent run buried in the middle) and
        # confirm max() by created_at is used, not list position.
        runs = [
            _run("completed", "2026-01-01T00:00:00Z"),
            _run("completed", "2026-07-20T00:00:00Z"),  # true most recent
            _run("completed", "2026-06-01T00:00:00Z"),
        ]
        last, _ = summarize_runs(runs)
        assert last == _utc(2026, 7, 20, 0, 0)

    def test_active_run_not_first_in_list_still_detected(self):
        # Active status buried at the end of the list, not the head.
        runs = [
            _run("completed", "2026-07-16T05:00:00Z"),
            _run("completed", "2026-07-15T00:00:00Z"),
            _run("in_progress", "2026-07-16T06:00:00Z"),
        ]
        _, active = summarize_runs(runs)
        assert active is True


class TestDispatchWorkflowStatusCodes:
    def test_401_unauthorized_returns_false(self):
        session = FakeSession(post_responses=FakeResponse(status_code=401))
        assert dispatch_workflow("owner/repo", "tok", session) is False

    def test_404_workflow_not_found_returns_false(self):
        session = FakeSession(post_responses=FakeResponse(status_code=404))
        assert dispatch_workflow("owner/repo", "tok", session) is False

    def test_422_unprocessable_returns_false(self):
        # e.g. bad ref -- another plausible real-world dispatch failure mode.
        session = FakeSession(post_responses=FakeResponse(status_code=422))
        assert dispatch_workflow("owner/repo", "tok", session) is False

    def test_odd_raise_for_status_exception_does_not_propagate(self):
        # A response whose raise_for_status raises something within the
        # caught exception set should still degrade rather than propagate.
        session = FakeSession(post_responses=FakeResponse(status_code=204, raise_exc=ValueError("weird")))
        assert dispatch_workflow("owner/repo", "tok", session) is False

    def test_204_no_content_success_returns_true(self):
        # GitHub's real dispatch endpoint returns 204 with an empty body --
        # confirm that specific realistic status still reads as success.
        session = FakeSession(post_responses=FakeResponse(status_code=204))
        assert dispatch_workflow("owner/repo", "tok", session) is True


class TestFetchWorkflowRunsMoreStatusCodes:
    def test_403_forbidden_returns_none(self):
        session = FakeSession(get_responses=FakeResponse(status_code=403))
        assert fetch_workflow_runs("owner/repo", "tok", session) is None

    def test_404_repo_not_found_returns_none(self):
        session = FakeSession(get_responses=FakeResponse(status_code=404))
        assert fetch_workflow_runs("owner/repo", "tok", session) is None

    def test_malformed_json_returns_none(self):
        session = FakeSession(get_responses=FakeResponse(json_exc=ValueError("bad json")))
        assert fetch_workflow_runs("owner/repo", "tok", session) is None


class TestMainDueByScheduleAndRequestBoth:
    def test_both_due_dispatches_once_and_reports_manual_reason(self, monkeypatch, capsys):
        # When both the cadence and a pending Run Now request are due
        # simultaneously, main() must still dispatch exactly once (not
        # twice), and per its own reason-selection logic (due_by_request
        # checked first), the printed reason should be the manual request,
        # not the cadence -- confirm that's actually what happens.
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr(
            "pipeline.schedule_gate.fetch_pipeline_schedule",
            lambda session=None: {"mode": "interval_hours", "interval_hours": 1.0},
        )
        monkeypatch.setattr(
            "pipeline.schedule_gate.fetch_run_request",
            lambda session=None: _utc(2026, 7, 16),
        )
        monkeypatch.setattr(
            "pipeline.schedule_gate.fetch_workflow_runs",
            lambda *a, **k: [_run("completed", "2000-01-01T00:00:00Z")],
        )
        dispatch_calls = {"count": 0}

        def fake_dispatch(*a, **k):
            dispatch_calls["count"] += 1
            return True

        monkeypatch.setattr("pipeline.schedule_gate.dispatch_workflow", fake_dispatch)

        assert main() == 0
        assert dispatch_calls["count"] == 1
        out = capsys.readouterr().out
        assert "manual run request" in out
        assert "cadence" not in out


class TestMainUnexpectedFetcherException:
    """fetch_pipeline_schedule / fetch_run_request are documented to always
    degrade internally and never raise. main() itself has no try/except
    around either call, so if one somehow did raise despite that contract,
    main() would crash rather than degrade. This test pins down that this is
    the current behavior -- not a silent swallow -- so a future change to
    either fetcher's contract doesn't quietly regress into main() crashing
    without anyone noticing this coupling exists."""

    def test_schedule_fetch_raising_propagates_out_of_main(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

        def boom(session=None):
            raise RuntimeError("unexpected failure fetching schedule")

        monkeypatch.setattr("pipeline.schedule_gate.fetch_pipeline_schedule", boom)
        monkeypatch.setattr("pipeline.schedule_gate.fetch_run_request", lambda session=None: None)

        with pytest.raises(RuntimeError, match="unexpected failure fetching schedule"):
            main()

    def test_run_request_fetch_raising_propagates_out_of_main(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr(
            "pipeline.schedule_gate.fetch_pipeline_schedule", lambda session=None: {"mode": "manual"}
        )

        def boom(session=None):
            raise RuntimeError("unexpected failure fetching run request")

        monkeypatch.setattr("pipeline.schedule_gate.fetch_run_request", boom)

        with pytest.raises(RuntimeError, match="unexpected failure fetching run request"):
            main()
