"""Tests for the AIA SSL-recovery path in scraper/fetch.py:
`_fetch_with_aia_recovery()` and the `except requests.exceptions.SSLError`
branch inside `fetch_source()`.

All network access is mocked (scraper.fetch.build_completed_bundle -- the
name as *imported into scraper.fetch*, not scraper.tls -- and session.get
are both faked) -- no test in this file may hit a live university website
(CLAUDE.md hard rule / QA policy). This file intentionally does not
duplicate the non-SSL-error tests already covered in
tests/test_scraper.py::TestFetchSource.

IMPORTANT: fetch.py does `from scraper.tls import build_completed_bundle`,
which binds a local name in scraper.fetch's namespace at import time.
Patching `scraper.tls.build_completed_bundle` does NOT affect that already
-bound name, so every mock here patches `scraper.fetch.build_completed_bundle`
instead -- patching the wrong target here would silently fall through to
the REAL implementation (which itself attempts a real socket connection),
so this distinction is safety-critical, not just correctness-critical.
"""
from __future__ import annotations

import requests

from scraper.config import Source
from scraper.fetch import FetchResult, _fetch_with_aia_recovery, fetch_source


# ---------------------------------------------------------------------------
# Fakes (mirrors the style used in tests/test_scraper.py)
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, text=None, content=None, status_code=200, raise_exc=None):
        self.text = text
        self.content = content if content is not None else (text.encode() if text else b"")
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class ScriptedSession:
    """Session whose .get() returns a scripted sequence of results per call
    (in order), rather than being keyed by URL — needed here because the
    same URL is requested twice (plain, then with verify=bundle) with
    different outcomes each time."""

    def __init__(self, results):
        # results: list of FakeResponse/Exception, consumed in order
        self._results = list(results)
        self.calls = []  # list of (url, kwargs)

    def get(self, url, timeout=None, **kwargs):
        self.calls.append((url, kwargs))
        if not self._results:
            raise requests.ConnectionError("no more scripted responses")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


# ---------------------------------------------------------------------------
# _fetch_with_aia_recovery
# ---------------------------------------------------------------------------

class TestFetchWithAiaRecovery:
    def test_returns_none_when_bundle_cannot_be_built(self, monkeypatch):
        source = Source(institution_id="giki", campus=None, url="https://admissions.giki.edu.pk", format="html")
        monkeypatch.setattr(
            "scraper.fetch.build_completed_bundle",
            lambda host, session, port=443, timeout=30: None,
        )
        session = ScriptedSession([])

        result = _fetch_with_aia_recovery(source, session, timeout=30)

        assert result is None
        assert session.calls == []  # never retried .get() without a bundle

    def test_returns_response_when_bundle_and_retry_succeed(self, monkeypatch):
        source = Source(institution_id="giki", campus=None, url="https://admissions.giki.edu.pk", format="html")
        monkeypatch.setattr(
            "scraper.fetch.build_completed_bundle",
            lambda host, session, port=443, timeout=30: "/fake/bundle.pem",
        )
        ok_response = FakeResponse(text="<html>recovered</html>")
        session = ScriptedSession([ok_response])

        result = _fetch_with_aia_recovery(source, session, timeout=30)

        assert result == (ok_response, "/fake/bundle.pem")
        assert len(session.calls) == 1
        url, kwargs = session.calls[0]
        assert url == source.url
        assert kwargs.get("verify") == "/fake/bundle.pem"

    def test_returns_none_when_bundle_built_but_retry_fails(self, monkeypatch):
        source = Source(institution_id="giki", campus=None, url="https://admissions.giki.edu.pk", format="html")
        monkeypatch.setattr(
            "scraper.fetch.build_completed_bundle",
            lambda host, session, port=443, timeout=30: "/fake/bundle.pem",
        )
        session = ScriptedSession([requests.exceptions.SSLError("still fails")])

        result = _fetch_with_aia_recovery(source, session, timeout=30)

        assert result is None

    def test_returns_none_when_source_url_has_no_hostname(self, monkeypatch):
        source = Source(institution_id="x", campus=None, url="not-a-valid-url", format="html")
        called = {"n": 0}

        def fake_build(host, session, port=443, timeout=30):
            called["n"] += 1
            return "/fake/bundle.pem"

        monkeypatch.setattr("scraper.fetch.build_completed_bundle", fake_build)
        session = ScriptedSession([])

        result = _fetch_with_aia_recovery(source, session, timeout=30)

        assert result is None
        assert called["n"] == 0  # never even attempted to build a bundle


# ---------------------------------------------------------------------------
# fetch_source: SSLError branch
# ---------------------------------------------------------------------------

class TestFetchSourceSslRecovery:
    def test_ssl_error_recovered_via_aia_returns_html(self, monkeypatch):
        source = Source(institution_id="giki", campus=None, url="https://admissions.giki.edu.pk", format="html")

        def fake_recovery(src, session, timeout):
            assert src is source
            return FakeResponse(text="<html>recovered via AIA</html>"), "/fake/bundle.pem"

        monkeypatch.setattr("scraper.fetch._fetch_with_aia_recovery", fake_recovery)

        session = ScriptedSession([requests.exceptions.SSLError("cert chain incomplete")])
        result = fetch_source(source, session=session)

        assert isinstance(result, FetchResult)
        assert result.ok is True
        assert result.error is None
        assert result.html == "<html>recovered via AIA</html>"
        assert result.source_url == source.url
        assert result.institution_id == "giki"

    def test_ssl_error_recovery_impossible_reports_ssl_failure(self, monkeypatch):
        source = Source(institution_id="giki", campus=None, url="https://admissions.giki.edu.pk", format="html")

        monkeypatch.setattr("scraper.fetch._fetch_with_aia_recovery", lambda src, session, timeout: None)

        session = ScriptedSession([requests.exceptions.SSLError("cert chain incomplete")])
        result = fetch_source(source, session=session)

        assert isinstance(result, FetchResult)
        assert result.ok is False
        assert result.html is None
        assert result.error is not None
        assert "SSL" in result.error
        assert result.source_url == source.url

    def test_ssl_error_recovery_bundle_found_but_retry_still_fails(self, monkeypatch):
        # _fetch_with_aia_recovery already swallows the retry-failure case
        # internally and returns None per its own contract (tested above in
        # TestFetchWithAiaRecovery.test_returns_none_when_bundle_built_but_
        # retry_fails) -- from fetch_source's point of view this collapses
        # to the same "recovery impossible" branch, exercised here via the
        # public fetch_source() entry point instead of the helper directly.
        source = Source(institution_id="giki", campus=None, url="https://admissions.giki.edu.pk", format="html")

        monkeypatch.setattr("scraper.fetch._fetch_with_aia_recovery", lambda src, session, timeout: None)

        session = ScriptedSession([requests.exceptions.SSLError("cert chain incomplete")])
        result = fetch_source(source, session=session)

        assert result.ok is False
        assert result.html is None
        assert result.error is not None
        assert "SSL" in result.error


# ---------------------------------------------------------------------------
# fetch_source: AIA-recovered bundle reused for same-host PDF fallback
# ---------------------------------------------------------------------------

class TestFetchSourcePdfBundlePropagation:
    def test_recovered_bundle_is_reused_for_pdf_fallback(self, monkeypatch):
        # If the HTML page needed AIA recovery, a linked PDF on the same
        # host almost certainly has the identical incomplete-chain problem
        # -- the recovered bundle should be reused, not dropped.
        source = Source(
            institution_id="pu", campus=None, url="https://pu.edu.pk/admissions", format="html+pdf"
        )

        def fake_recovery(src, session, timeout):
            return (
                FakeResponse(text='<html><a href="notice.pdf">Notice</a></html>'),
                "/fake/bundle.pem",
            )

        monkeypatch.setattr("scraper.fetch._fetch_with_aia_recovery", fake_recovery)

        captured = {}

        def fake_fetch_linked_pdfs(html, base_url, session, verify=True):
            captured["verify"] = verify
            return []

        monkeypatch.setattr("scraper.fetch.fetch_linked_pdfs", fake_fetch_linked_pdfs)

        session = ScriptedSession([requests.exceptions.SSLError("cert chain incomplete")])
        result = fetch_source(source, session=session)

        assert result.ok is True
        assert captured["verify"] == "/fake/bundle.pem"

    def test_normal_success_path_uses_default_verify_for_pdf_fallback(self, monkeypatch):
        source = Source(
            institution_id="pu", campus=None, url="https://pu.edu.pk/admissions", format="html+pdf"
        )
        captured = {}

        def fake_fetch_linked_pdfs(html, base_url, session, verify=True):
            captured["verify"] = verify
            return []

        monkeypatch.setattr("scraper.fetch.fetch_linked_pdfs", fake_fetch_linked_pdfs)

        session = ScriptedSession([FakeResponse(text="<html>no ssl issue</html>")])
        result = fetch_source(source, session=session)

        assert result.ok is True
        assert captured["verify"] is True  # no AIA recovery happened; default verification
