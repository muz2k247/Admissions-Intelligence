"""End-to-end wiring test for Phase R: proves stage_1_scrape and
_institutions_payload() both actually resolve institutions through the SAME
load_merged_institutions() call -- a curator-added institution gets scraped
AND published, and a tombstoned YAML institution is excluded from both --
rather than each stage independently trusting a mocked-away merge (as every
other test file in this suite does for isolation).

Exercises the REAL merge_institutions()/fetch_institution_docs() code path:
only the two true I/O boundaries (scraper.config.load_institutions, stubbed
to return an in-memory list instead of reading config/institutions.yaml, and
pipeline.institutions_registry.fetch_institution_docs, stubbed to return a
fake decoded Firestore response instead of making a REST call) are stubbed.
No live network.
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


class TestCuratorAddedInstitutionReachesBothStages:
    def test_new_institution_is_scraped_and_published(self, tmp_path, monkeypatch):
        monkeypatch.setattr(institutions_registry, "load_institutions", lambda *a, **k: _yaml_institutions())
        monkeypatch.setattr(
            institutions_registry, "fetch_institution_docs",
            lambda *a, **k: {"new_uni": {"name": "New Uni", "sources": [{"url": "https://new.edu.pk", "format": "html"}]}},
        )

        # stage_1_scrape: the curator-added institution must actually be
        # scraped, not just appear in the published metadata.
        calls = []
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(calls))
        monkeypatch.setattr(run_full, "build_session", lambda: object())
        rc = run_full.stage_1_scrape(tmp_path / "scraped")
        assert rc == 0
        assert set(calls) == {"giki", "uet", "new_uni"}

        # _institutions_payload: the same merged list feeds the published
        # institutions.json.
        payload = run_full._institutions_payload()
        assert {entry["id"] for entry in payload} == {"giki", "uet", "new_uni"}
        new_uni = next(e for e in payload if e["id"] == "new_uni")
        assert new_uni["name"] == "New Uni"
        assert new_uni["enabled"] is True


class TestTombstonedYamlInstitutionExcludedFromBothStages:
    def test_tombstoned_institution_is_neither_scraped_nor_published(self, tmp_path, monkeypatch):
        monkeypatch.setattr(institutions_registry, "load_institutions", lambda *a, **k: _yaml_institutions())
        monkeypatch.setattr(
            institutions_registry, "fetch_institution_docs",
            lambda *a, **k: {"uet": {"deleted": True}},
        )

        calls = []
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(calls))
        monkeypatch.setattr(run_full, "build_session", lambda: object())
        rc = run_full.stage_1_scrape(tmp_path / "scraped")
        assert rc == 0
        assert calls == ["giki"]  # uet never fetched

        payload = run_full._institutions_payload()
        assert {entry["id"] for entry in payload} == {"giki"}
