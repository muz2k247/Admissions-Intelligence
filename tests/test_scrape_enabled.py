"""Additional coverage for pipeline/run_full.py stage_1_scrape, filling gaps
not exercised by tests/test_scraper.py:

1. Institution.enabled interacting with stage_1_scrape end-to-end (not just
   iter_sources() in isolation): a disabled institution must never reach
   fetch_source, and must produce no scraped output file for it.
2. stage_1_scrape's log output distinguishes a disabled institution (named in
   an explicit "[SKIP] ... disabled in config" line) from one merely excluded
   by --institution filtering (which is silently absent -- not the same event).

No live network calls: fetch_source and build_session are monkeypatched on
pipeline.run_full directly (matching the documented binding-order caveat --
`pipeline/run_full.py` does `from scraper.fetch import build_session,
fetch_source`, so patches must target `run_full.fetch_source` /
`run_full.build_session`, not the `scraper.fetch` module attributes directly).
"""
from __future__ import annotations

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
            fetched_at="2026-07-13T00:00:00Z",
            html="<html>ok</html>",
        )
    return _fake_fetch_source


class TestStage1ScrapeEnabledEndToEnd:
    def _two_institutions(self):
        active = Institution(
            id="active_inst", name="Active Institution", admitting_body=False, ug_pg_mixed=False,
            sources=[Source(institution_id="active_inst", campus=None, url="https://active.example.edu", format="html")],
            enabled=True,
        )
        disabled = Institution(
            id="disabled_inst", name="Disabled Institution", admitting_body=False, ug_pg_mixed=False,
            sources=[Source(institution_id="disabled_inst", campus=None, url="https://disabled.example.edu", format="html")],
            enabled=False,
        )
        return [active, disabled]

    def test_disabled_institution_never_reaches_fetch_source(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "scraped"
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._two_institutions())
        calls = []
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(calls))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(out_dir)

        assert rc == 0
        assert calls == ["active_inst"], "fetch_source must never be called for a disabled institution"

    def test_disabled_institution_produces_no_scraped_output_file(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "scraped"
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._two_institutions())
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory([]))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        run_full.stage_1_scrape(out_dir)

        produced = sorted(p.name for p in out_dir.glob("*.json"))
        assert produced == ["active_inst__default.json"]

    def test_disabled_institution_is_logged_as_skipped(self, tmp_path, monkeypatch, capsys):
        # stage_1_scrape emits an explicit, honest operator signal for disabled
        # institutions: a "[SKIP] N institution(s) disabled in config: <ids>"
        # line naming them. The disabled institution therefore appears in the
        # log -- but only in that SKIP line, never in a per-institution
        # OK/processing line, since iter_sources() still never yields it.
        out_dir = tmp_path / "scraped"
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._two_institutions())
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory([]))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        run_full.stage_1_scrape(out_dir)

        out = capsys.readouterr().out
        assert "[SKIP]" in out
        assert "disabled in config" in out
        lines_mentioning_disabled = [line for line in out.splitlines() if "disabled_inst" in line]
        assert lines_mentioning_disabled, "the disabled institution must be named in the skip log"
        assert all("[SKIP]" in line for line in lines_mentioning_disabled), (
            "a disabled institution must appear ONLY in the skip line, never as a processed source"
        )
        assert "active_inst" in out

    def test_log_distinguishes_disabled_from_filtered_out(self, tmp_path, monkeypatch, capsys):
        # A disabled institution and one merely excluded by --institution
        # filtering are NOT the same event, and the log must not blur them:
        # the disabled institution gets a "[SKIP] ... disabled in config" line
        # naming it, whereas a filtered-out (but enabled) institution produces
        # no such line -- it just isn't the one selected this run.
        out_dir_disabled = tmp_path / "scraped_disabled_case"
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._two_institutions())
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory([]))
        monkeypatch.setattr(run_full, "build_session", lambda: object())
        run_full.stage_1_scrape(out_dir_disabled)
        disabled_case_out = capsys.readouterr().out

        # Same two institutions, but both enabled=True this time, filtered down
        # to just "active_inst" via --institution.
        both_enabled = [
            Institution(
                id="active_inst", name="Active Institution", admitting_body=False, ug_pg_mixed=False,
                sources=[Source(institution_id="active_inst", campus=None, url="https://active.example.edu", format="html")],
                enabled=True,
            ),
            Institution(
                id="disabled_inst", name="Disabled Institution", admitting_body=False, ug_pg_mixed=False,
                sources=[Source(institution_id="disabled_inst", campus=None, url="https://disabled.example.edu", format="html")],
                enabled=True,
            ),
        ]
        out_dir_filtered = tmp_path / "scraped_filtered_case"
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: both_enabled)
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory([]))
        run_full.stage_1_scrape(out_dir_filtered, institution_filter="active_inst")
        filtered_case_out = capsys.readouterr().out

        # Disabled case: an explicit skip line naming the institution.
        assert "disabled in config" in disabled_case_out
        assert "disabled_inst" in disabled_case_out
        # Filtered-out case: no "disabled in config" signal at all, and the
        # excluded (but enabled) institution is simply absent from the log.
        assert "disabled in config" not in filtered_case_out
        assert "disabled_inst" not in filtered_case_out

    def test_all_institutions_disabled_returns_error_and_makes_no_fetch(self, tmp_path, monkeypatch):
        # Every institution disabled -> zero sources attempted. stage_1_scrape
        # must fail loudly (exit 1) rather than silently "succeed" with no
        # output, and must never touch the network.
        out_dir = tmp_path / "scraped"
        both_disabled = [
            Institution(
                id="a", name="A", admitting_body=False, ug_pg_mixed=False,
                sources=[Source(institution_id="a", campus=None, url="https://a.example.edu", format="html")],
                enabled=False,
            ),
            Institution(
                id="b", name="B", admitting_body=False, ug_pg_mixed=False,
                sources=[Source(institution_id="b", campus=None, url="https://b.example.edu", format="html")],
                enabled=False,
            ),
        ]
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: both_disabled)
        calls = []
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(calls))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(out_dir)

        assert rc == 1
        assert calls == [], "no fetch may happen when every institution is disabled"

    def test_institution_filter_pointed_at_disabled_returns_error_and_makes_no_fetch(self, tmp_path, monkeypatch):
        # A --institution debug run targeting a disabled institution attempts
        # zero sources (iter_sources never yields it) and must return 1 with
        # the "matched no enabled institution" reason, not a silent success.
        out_dir = tmp_path / "scraped"
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._two_institutions())
        calls = []
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(calls))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(out_dir, institution_filter="disabled_inst")

        assert rc == 1
        assert calls == [], "a disabled institution must never be fetched, even when filtered to"

    def test_institutions_payload_reports_enabled_flag_for_disabled_institution(self, monkeypatch):
        # _institutions_payload keeps disabled institutions in the published
        # registry, flagged enabled:false -- honest metadata; the value must
        # track Institution.enabled, not silently default to True.
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._two_institutions())

        payload = run_full._institutions_payload()

        by_id = {entry["id"]: entry for entry in payload}
        assert by_id["active_inst"]["enabled"] is True
        assert by_id["disabled_inst"]["enabled"] is False

    def test_disabled_institution_is_still_returned_by_load_and_left_unmutated(self):
        # The toggle only gates scraping (iter_sources); it must never mutate
        # the institution, and load_institutions-equivalent listing must still
        # expose it so it can be re-enabled later.
        institutions = self._two_institutions()

        yielded = [inst.id for inst, _ in run_full.iter_sources(institutions)]

        assert yielded == ["active_inst"]
        # The disabled institution object is untouched and still present.
        disabled = next(i for i in institutions if i.id == "disabled_inst")
        assert disabled.enabled is False
        assert len(institutions) == 2
