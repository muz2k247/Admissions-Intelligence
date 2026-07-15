"""QA-added edge-case coverage for Phase R's load_merged_institutions()
wiring into stage_1_scrape, filling two gaps not exercised by
tests/test_institutions_registry_wiring.py (which only covers the *success*
path -- a curator-added institution and a tombstoned one) or
tests/test_scrape_enabled.py (which covers Institution.enabled end-to-end
but always via a pre-built list, never via the real load_merged_institutions
merge):

1. A Firestore fetch FAILURE during stage_1_scrape must degrade to
   YAML-only sources being scraped -- not zero sources, not a crash --
   exercising the REAL load_merged_institutions()/merge_institutions() code
   path (only fetch_institution_docs's network call is stubbed to raise/
   return {}, matching fetch_institution_docs's own documented contract).
2. A Firestore-added institution with enabled: false must be excluded from
   iter_sources by stage_1_scrape, the same way an already-disabled YAML
   institution is (tests/test_scrape_enabled.py covers the YAML case; this
   proves the Firestore-origin case behaves identically end-to-end).

No live network: institutions_registry.load_institutions and
institutions_registry.fetch_institution_docs are the only two I/O
boundaries, both stubbed, matching test_institutions_registry_wiring.py's
convention.
"""
from __future__ import annotations

import pipeline.institutions_registry as institutions_registry
import pipeline.run_full as run_full
from scraper.config import Institution, Source
from scraper.fetch import FetchResult


def _fake_fetch_source_factory(calls_log):
    def _fake_fetch_source(source, session=None, timeout=30):
        calls_log.append(source.institution_id)
        return FetchResult(
            institution_id=source.institution_id,
            campus=source.campus,
            source_url=source.url,
            fetched_at="2026-07-15T00:00:00Z",
            html="<html>ok</html>",
        )
    return _fake_fetch_source


def _yaml_institutions():
    return [
        Institution(
            id="giki", name="GIKI", admitting_body=False, ug_pg_mixed=False,
            sources=[Source(institution_id="giki", campus=None, url="https://giki.edu.pk", format="html")],
            enabled=True,
        ),
        Institution(
            id="uet", name="UET", admitting_body=False, ug_pg_mixed=False,
            sources=[Source(institution_id="uet", campus=None, url="https://uet.edu.pk", format="html")],
            enabled=True,
        ),
    ]


class TestFirestoreFetchFailureDegradesToYamlOnly:
    def test_fetch_institution_docs_raising_still_scrapes_all_yaml_sources(self, tmp_path, monkeypatch):
        # fetch_institution_docs's own contract is "never raise, return {}
        # on any failure" -- but this test goes one level up: even if that
        # contract were somehow violated (or simply behaves as documented
        # and returns {}), stage_1_scrape must still attempt every YAML
        # source, not zero.
        monkeypatch.setattr(institutions_registry, "load_institutions", lambda *a, **k: _yaml_institutions())
        monkeypatch.setattr(institutions_registry, "fetch_institution_docs", lambda *a, **k: {})

        calls = []
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(calls))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(tmp_path / "scraped")

        assert rc == 0
        assert set(calls) == {"giki", "uet"}

    def test_fetch_institution_docs_exception_does_not_crash_stage_1_scrape(self, tmp_path, monkeypatch):
        # Belt-and-suspenders: even if fetch_institution_docs's own internal
        # try/except somehow didn't catch a failure mode, load_merged_
        # institutions() wraps the call itself and degrades to YAML-only --
        # stage_1_scrape must not raise, and must still process every YAML
        # source.
        monkeypatch.setattr(institutions_registry, "load_institutions", lambda *a, **k: _yaml_institutions())

        def _raise(*a, **k):
            raise ConnectionError("Firestore unreachable")

        monkeypatch.setattr(institutions_registry, "fetch_institution_docs", _raise)

        calls = []
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(calls))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(tmp_path / "scraped")

        assert rc == 0
        assert set(calls) == {"giki", "uet"}


class TestFirestoreAddedDisabledInstitutionExcludedFromScrape:
    def test_firestore_added_institution_with_enabled_false_is_not_scraped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(institutions_registry, "load_institutions", lambda *a, **k: _yaml_institutions())
        monkeypatch.setattr(
            institutions_registry, "fetch_institution_docs",
            lambda *a, **k: {
                "new_uni": {
                    "name": "New Uni",
                    "enabled": False,
                    "sources": [{"url": "https://new.edu.pk", "format": "html"}],
                }
            },
        )

        calls = []
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(calls))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(tmp_path / "scraped")

        assert rc == 0
        assert set(calls) == {"giki", "uet"}  # new_uni never fetched

        payload = run_full._institutions_payload()
        new_uni = next(e for e in payload if e["id"] == "new_uni")
        assert new_uni["enabled"] is False  # still listed, honestly flagged

    def test_firestore_added_disabled_institution_is_logged_as_skipped(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(institutions_registry, "load_institutions", lambda *a, **k: _yaml_institutions())
        monkeypatch.setattr(
            institutions_registry, "fetch_institution_docs",
            lambda *a, **k: {
                "new_uni": {
                    "name": "New Uni",
                    "enabled": False,
                    "sources": [{"url": "https://new.edu.pk", "format": "html"}],
                }
            },
        )
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory([]))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        run_full.stage_1_scrape(tmp_path / "scraped")

        out = capsys.readouterr().out
        assert "[SKIP]" in out
        assert "new_uni" in out
        assert "disabled in config" in out
