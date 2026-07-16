"""QA tests for Phase T Task 4.2: confirming pipeline/run_full.py's stage
functions (stage_1_scrape, stage_2_chunk, stage_4_build, stage_5_publish)
correctly call pipeline/health.py's record_stage(name, payload) to accumulate
a per-run health fragment.

pipeline/health.py's own internals (fragment read/write, finalize,
_derive_status) already have a dedicated suite at tests/test_health.py and are
NOT re-tested here -- this file only confirms run_full.py's four stage
functions call into record_stage() with the right section name and the right
payload keys, on both success and failure/early-return paths.

Approach: run_full.py does `from pipeline.health import init_run,
record_stage` at module level, so record_stage is bound into run_full's own
module namespace. Monkeypatching `pipeline.run_full.record_stage` (not
`pipeline.health.record_stage`) is therefore the correct interception point --
same binding-order caveat already documented in
tests/test_pipeline_publish.py / tests/test_scrape_enabled.py for other
run_full-imported names. A tiny capture double stands in and never touches
disk, keeping these tests independent of health.py's own fragment-file
behavior.

No live network calls, no live Firestore (the autouse fixture below reuses
tests/test_pipeline_publish.py's stubbing pattern for stage_5_publish's
Firestore-backed dependencies). No original code modified.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import pipeline.run_full as run_full
from extraction.schema import Field
from scraper.config import DEFAULT_CONFIG_PATH, Institution, Source, load_institutions
from scraper.fetch import FetchResult


# ---------------------------------------------------------------------------
# Shared capture double for record_stage
# ---------------------------------------------------------------------------

class _RecordStageCapture:
    """Records every call as (name, payload) in order. Mirrors the real
    record_stage's fragment-merge semantics loosely enough for assertions
    (last call per name wins) without touching disk."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, name, payload, fragment_path=None):
        self.calls.append((name, payload))

    def payload_for(self, name):
        matches = [p for n, p in self.calls if n == name]
        assert matches, f"record_stage was never called with section {name!r}; calls={self.calls}"
        return matches[-1]

    def names_called(self):
        return [n for n, _ in self.calls]


@pytest.fixture
def capture(monkeypatch):
    cap = _RecordStageCapture()
    monkeypatch.setattr(run_full, "record_stage", cap)
    return cap


@pytest.fixture(autouse=True)
def _no_live_firestore(monkeypatch):
    """stage_5_publish depends on four Firestore-backed helpers -- stub them
    exactly as tests/test_pipeline_publish.py's autouse fixture does, so every
    test in this file (including ones not directly about stage_5_publish) is
    hermetic. Tests that want to exercise overrides/gate/institutions merge
    behavior re-patch these themselves."""
    monkeypatch.setattr(run_full, "fetch_overrides", lambda *a, **k: {})
    monkeypatch.setattr(run_full, "fetch_review_settings", lambda *a, **k: {"enabled": True, "threshold": 0.8})
    monkeypatch.setattr(run_full, "fetch_review_decisions", lambda *a, **k: {})
    monkeypatch.setattr(run_full, "load_merged_institutions", lambda *a, **k: load_institutions(DEFAULT_CONFIG_PATH))


# ---------------------------------------------------------------------------
# 1. stage_1_scrape
# ---------------------------------------------------------------------------

def _fake_fetch_source_factory(ok=True, error=None, pdfs=None):
    def _fake(source, session=None, timeout=30):
        return FetchResult(
            institution_id=source.institution_id,
            campus=source.campus,
            source_url=source.url,
            fetched_at="2026-07-16T00:00:00Z",
            html="<html>ok</html>" if ok else None,
            pdfs=pdfs or [],
            error=error,
        )
    return _fake


class TestStage1ScrapeHealth:
    def _one_institution(self, id_="giki"):
        return [
            Institution(
                id=id_, name=id_.upper(), admitting_body=False, ug_pg_mixed=False,
                sources=[Source(institution_id=id_, campus=None, url=f"https://{id_}.example.edu.pk", format="html")],
                enabled=True,
            )
        ]

    def test_success_records_scrape_section_with_expected_keys(self, tmp_path, monkeypatch, capture):
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._one_institution())
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(ok=True))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(tmp_path / "scraped")

        assert rc == 0
        payload = capture.payload_for("scrape")
        assert payload["attempted"] == 1
        assert payload["ok"] == 1
        assert payload["failed"] == 0
        assert isinstance(payload["sources"], list) and len(payload["sources"]) == 1
        src = payload["sources"][0]
        for key in ("institution_id", "campus", "url", "ok", "error", "pdf_count", "pdf_failure_count"):
            assert key in src
        assert src["institution_id"] == "giki"
        assert src["ok"] is True
        assert src["error"] is None
        assert src["pdf_count"] == 0
        assert src["pdf_failure_count"] == 0

    def test_all_sources_failed_still_records_scrape_section(self, tmp_path, monkeypatch, capture):
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._one_institution())
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(ok=False, error="connection refused"))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(tmp_path / "scraped")

        assert rc == 1
        payload = capture.payload_for("scrape")
        assert payload["attempted"] == 1
        assert payload["ok"] == 0
        assert payload["failed"] == 1
        assert payload["sources"][0]["ok"] is False
        assert payload["sources"][0]["error"] == "connection refused"

    def test_no_institutions_enabled_records_zeros_and_reason(self, tmp_path, monkeypatch, capture):
        disabled = Institution(
            id="x", name="X", admitting_body=False, ug_pg_mixed=False,
            sources=[Source(institution_id="x", campus=None, url="https://x.example.edu.pk", format="html")],
            enabled=False,
        )
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: [disabled])
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(ok=True))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(tmp_path / "scraped")

        assert rc == 1
        payload = capture.payload_for("scrape")
        assert payload == {"attempted": 0, "ok": 0, "failed": 0, "sources": [], "reason": "0 institutions are enabled in config"}

    def test_institution_filter_matching_nothing_records_zeros_and_reason(self, tmp_path, monkeypatch, capture):
        monkeypatch.setattr(run_full, "load_merged_institutions", lambda: self._one_institution())
        monkeypatch.setattr(run_full, "fetch_source", _fake_fetch_source_factory(ok=True))
        monkeypatch.setattr(run_full, "build_session", lambda: object())

        rc = run_full.stage_1_scrape(tmp_path / "scraped", institution_filter="nonexistent")

        assert rc == 1
        payload = capture.payload_for("scrape")
        assert payload["attempted"] == 0 and payload["ok"] == 0 and payload["failed"] == 0 and payload["sources"] == []
        assert "reason" in payload
        assert "nonexistent" in payload["reason"]


# ---------------------------------------------------------------------------
# 2. stage_2_chunk
# ---------------------------------------------------------------------------

class TestStage2ChunkHealth:
    def test_empty_scraped_records_records_zero_chunk_section(self, tmp_path, capture):
        scraped_dir = tmp_path / "scraped"
        scraped_dir.mkdir()

        rc = run_full.stage_2_chunk(scraped_dir, tmp_path / "chunks.json")

        assert rc == 1
        payload = capture.payload_for("chunk")
        assert payload == {"chunks": 0, "skipped": 0}

    def test_normal_path_records_chunk_and_skipped_counts(self, tmp_path, capture):
        scraped_dir = tmp_path / "scraped"
        scraped_dir.mkdir()
        good = {
            "institution_id": "giki", "campus": None,
            "source_url": "https://giki.edu.pk", "fetched_at": "2026-07-16T00:00:00Z",
            "html": "<p>Last date to apply: 10 August 2026.</p>", "pdfs": [], "error": None,
        }
        (scraped_dir / "giki.json").write_text(json.dumps(good), encoding="utf-8")
        errored = {
            "institution_id": "uet", "campus": None,
            "source_url": "https://uet.edu.pk", "fetched_at": "2026-07-16T00:00:00Z",
            "html": None, "pdfs": [], "error": "fetch timed out",
        }
        (scraped_dir / "uet.json").write_text(json.dumps(errored), encoding="utf-8")

        rc = run_full.stage_2_chunk(scraped_dir, tmp_path / "chunks.json")

        assert rc == 0
        payload = capture.payload_for("chunk")
        assert payload["chunks"] == 1
        assert payload["skipped"] == 1

    def test_all_records_skipped_still_records_chunk_section_with_zero_chunks(self, tmp_path, capture):
        # No unhandled input produces 0 chunks but > 0 skipped -- the section
        # must reflect that, not just an all-zero "no input at all" shape.
        scraped_dir = tmp_path / "scraped"
        scraped_dir.mkdir()
        errored = {
            "institution_id": "uet", "campus": None,
            "source_url": "https://uet.edu.pk", "fetched_at": "2026-07-16T00:00:00Z",
            "html": None, "pdfs": [], "error": "fetch timed out",
        }
        (scraped_dir / "uet.json").write_text(json.dumps(errored), encoding="utf-8")

        rc = run_full.stage_2_chunk(scraped_dir, tmp_path / "chunks.json")

        assert rc == 1  # 0 chunks produced
        payload = capture.payload_for("chunk")
        assert payload == {"chunks": 0, "skipped": 1}


# ---------------------------------------------------------------------------
# 3. stage_4_build
# ---------------------------------------------------------------------------

def _write_scraped(scraped_dir, filename, institution_id, html):
    scraped_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "institution_id": institution_id, "campus": None,
        "source_url": f"https://{institution_id}.edu.pk", "fetched_at": "2026-07-16T00:00:00Z",
        "html": html, "pdfs": [], "error": None,
    }
    (scraped_dir / filename).write_text(json.dumps(record), encoding="utf-8")


def _write_classified(path, undergraduate_ids):
    path.write_text(json.dumps({"Undergraduate": undergraduate_ids}), encoding="utf-8")


class TestStage4BuildHealth:
    def test_early_return_no_scraped_records_does_not_record_build_section(self, tmp_path, capture):
        scraped_dir = tmp_path / "scraped"
        scraped_dir.mkdir()
        classified_path = tmp_path / "classified.json"
        _write_classified(classified_path, [])

        rc = run_full.stage_4_build(scraped_dir, classified_path, tmp_path / "extracted")

        assert rc == 1
        assert "build" not in capture.names_called()

    def test_early_return_unreadable_classifier_output_does_not_record_build_section(self, tmp_path, capture):
        scraped_dir = tmp_path / "scraped"
        _write_scraped(scraped_dir, "giki.json", "giki", "<p>Last date to apply: 10 August 2026.</p>")
        classified_path = tmp_path / "classified.json"
        classified_path.write_text("{not valid json,,,", encoding="utf-8")

        rc = run_full.stage_4_build(scraped_dir, classified_path, tmp_path / "extracted")

        assert rc == 1
        assert "build" not in capture.names_called()

    def test_normal_path_regex_only_records_build_section_with_regex_fallback_mode(self, tmp_path, capture):
        scraped_dir = tmp_path / "scraped"
        _write_scraped(scraped_dir, "giki.json", "giki", "<p>Last date to apply: 10 August 2026.</p>")
        classified_path = tmp_path / "classified.json"
        _write_classified(classified_path, ["giki::0"])  # chunk id shape doesn't need to match exactly for this test

        rc = run_full.stage_4_build(scraped_dir, classified_path, tmp_path / "extracted")

        assert rc == 0
        payload = capture.payload_for("build")
        for key in ("records", "llm_chunks", "regex_chunks", "excluded_postgraduate", "skipped", "extraction_mode"):
            assert key in payload
        assert payload["regex_chunks"] >= 1
        assert payload["llm_chunks"] == 0
        assert payload["extraction_mode"] == "regex_fallback"

    def test_llm_only_records_llm_extraction_mode(self, tmp_path, capture):
        scraped_dir = tmp_path / "scraped"
        _write_scraped(scraped_dir, "giki.json", "giki", "<p>Some announcement text with no deadline keyword.</p>")
        classified_path = tmp_path / "classified.json"

        # Discover the actual chunk id the chunker will assign, so the LLM
        # fields file keys match it (chunk_scraped_record's id scheme is an
        # implementation detail this test shouldn't hardcode).
        from extraction.chunker import chunk_scraped_record
        scraped_record = json.loads((scraped_dir / "giki.json").read_text(encoding="utf-8"))
        chunk_ids = [c.id for c in chunk_scraped_record(scraped_record)]
        assert chunk_ids, "fixture HTML must produce at least one chunk"
        _write_classified(classified_path, chunk_ids)

        llm_path = tmp_path / "llm_fields.json"
        llm_payload = {
            chunk_ids[0]: {
                "deadline": {"value": "10 Aug 2026", "confidence": 0.9, "note": None},
                "programs": {"value": None, "confidence": None, "note": None},
                "constituent_college": {"value": None, "confidence": None, "note": None},
                "admissions_open": {"value": None, "confidence": None, "note": None},
            }
        }
        llm_path.write_text(json.dumps(llm_payload), encoding="utf-8")

        rc = run_full.stage_4_build(scraped_dir, classified_path, tmp_path / "extracted", llm_extracted_path=llm_path)

        assert rc == 0
        payload = capture.payload_for("build")
        assert payload["llm_chunks"] == 1
        assert payload["regex_chunks"] == 0
        assert payload["extraction_mode"] == "llm"

    def test_mixed_llm_and_regex_records_mixed_extraction_mode(self, tmp_path, capture):
        scraped_dir = tmp_path / "scraped"
        _write_scraped(scraped_dir, "giki.json", "giki", "<p>Last date to apply: 10 August 2026.</p>")
        _write_scraped(scraped_dir, "uet.json", "uet", "<p>Last date to apply: 15 August 2026.</p>")
        classified_path = tmp_path / "classified.json"

        from extraction.chunker import chunk_scraped_record
        giki_record = json.loads((scraped_dir / "giki.json").read_text(encoding="utf-8"))
        uet_record = json.loads((scraped_dir / "uet.json").read_text(encoding="utf-8"))
        giki_chunk_ids = [c.id for c in chunk_scraped_record(giki_record)]
        uet_chunk_ids = [c.id for c in chunk_scraped_record(uet_record)]
        assert giki_chunk_ids and uet_chunk_ids
        _write_classified(classified_path, giki_chunk_ids + uet_chunk_ids)

        # LLM output covers only giki's chunk -- uet falls back to regex.
        llm_path = tmp_path / "llm_fields.json"
        llm_payload = {
            giki_chunk_ids[0]: {
                "deadline": {"value": "10 Aug 2026", "confidence": 0.9, "note": None},
                "programs": {"value": None, "confidence": None, "note": None},
                "constituent_college": {"value": None, "confidence": None, "note": None},
                "admissions_open": {"value": None, "confidence": None, "note": None},
            }
        }
        llm_path.write_text(json.dumps(llm_payload), encoding="utf-8")

        rc = run_full.stage_4_build(scraped_dir, classified_path, tmp_path / "extracted", llm_extracted_path=llm_path)

        assert rc == 0
        payload = capture.payload_for("build")
        assert payload["llm_chunks"] == 1
        assert payload["regex_chunks"] == 1
        assert payload["extraction_mode"] == "mixed"

    def test_zero_records_written_still_records_build_section(self, tmp_path, capture):
        # Scraped records exist and classifier output is readable, but every
        # chunk is classified Postgraduate -> 0 records written. This is NOT
        # the same early-return path as the two "no build section" cases
        # above (it passes both prior fatal checks), so build IS recorded.
        scraped_dir = tmp_path / "scraped"
        _write_scraped(scraped_dir, "giki.json", "giki", "<p>Last date to apply: 10 August 2026.</p>")
        classified_path = tmp_path / "classified.json"

        from extraction.chunker import chunk_scraped_record
        scraped_record = json.loads((scraped_dir / "giki.json").read_text(encoding="utf-8"))
        chunk_ids = [c.id for c in chunk_scraped_record(scraped_record)]
        classified_path.write_text(json.dumps({"Postgraduate": chunk_ids}), encoding="utf-8")

        rc = run_full.stage_4_build(scraped_dir, classified_path, tmp_path / "extracted")

        assert rc == 1  # 0 records written
        payload = capture.payload_for("build")
        assert payload["records"] == 0
        assert payload["excluded_postgraduate"] == len(chunk_ids)
        assert payload["extraction_mode"] is None


# ---------------------------------------------------------------------------
# 4. stage_5_publish
# ---------------------------------------------------------------------------

def _write_extracted_record(extracted_dir, filename, chunk_id="giki", **overrides):
    extracted_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "institution_id": "giki",
        "campus": None,
        "source_url": "https://giki.edu.pk/admissions/admissions-undergraduates/",
        "fetched_at": "2026-07-16T00:00:00Z",
        "chunk_id": chunk_id,
        "degree_level": {"value": "Undergraduate", "reason": None},
        "constituent_college": {"value": None, "confidence": None, "note": None},
        "deadline": {"value": "10 Aug 2026", "confidence": 0.85, "note": None},
        "programs": {"value": None, "confidence": None, "note": None},
    }
    record.update(overrides)
    (extracted_dir / filename).write_text(json.dumps(record), encoding="utf-8")


class TestStage5PublishHealth:
    def test_refused_no_records_records_decision(self, tmp_path, capture):
        extracted_dir = tmp_path / "extracted"
        extracted_dir.mkdir()
        publish_dir = tmp_path / "publish"

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 1
        payload = capture.payload_for("publish")
        assert payload["decision"] == "refused_no_records"

    def test_refused_coverage_drop_records_decision_with_numbers(self, tmp_path, monkeypatch, capture):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"

        # Seed a "previous" published run with high coverage across several
        # institutions so the new (empty-field) run looks like a regression.
        publish_dir.mkdir(parents=True)
        previous_records = [
            {
                "institution_id": inst_id, "campus": None,
                "source_url": f"https://{inst_id}.edu.pk", "fetched_at": "2026-07-01T00:00:00Z",
                "chunk_id": inst_id,
                "degree_level": {"value": "Undergraduate", "reason": None},
                "constituent_college": {"value": None, "confidence": None, "note": None},
                "deadline": {"value": "1 Aug 2026", "confidence": 0.9, "note": None},
                "programs": {"value": ["BS CS"], "confidence": 0.9, "note": None},
            }
            for inst_id in ("giki", "uet", "fast", "comsats")
        ]
        (publish_dir / "records.json").write_text(json.dumps(previous_records), encoding="utf-8")
        (publish_dir / "needs_review.json").write_text("[]", encoding="utf-8")

        # New run: only one institution, all fields null -- both coverage and
        # institution-count drop well past the 50% threshold.
        _write_extracted_record(
            extracted_dir, "giki.json", chunk_id="giki",
            deadline={"value": None, "confidence": None, "note": None},
        )

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 1
        payload = capture.payload_for("publish")
        assert payload["decision"] == "refused_coverage_drop"
        for key in (
            "coverage_dropped", "institutions_dropped", "new_coverage", "previous_coverage",
            "new_institution_count", "previous_institution_count",
        ):
            assert key in payload
        assert payload["new_institution_count"] == 1
        assert payload["previous_institution_count"] == 4

    def test_published_success_records_decision_and_counts(self, tmp_path, capture):
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", chunk_id="giki")

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        payload = capture.payload_for("publish")
        assert payload["decision"] == "published"
        for key in (
            "records_published", "queued_for_review", "institutions_published",
            "overrides_applied", "new_coverage", "previous_coverage",
            "new_institution_count", "previous_institution_count",
        ):
            assert key in payload
        assert payload["records_published"] == 1
        assert payload["overrides_applied"] == 0

    def test_overrides_applied_is_zero_not_a_crash_when_no_overrides_at_all(self, tmp_path, monkeypatch, capture):
        # Exercises the `overridden if overrides else 0` ternary directly:
        # `overridden` is only assigned inside `if overrides:` in
        # stage_5_publish, so an empty overrides dict must not raise
        # NameError when that expression is evaluated.
        extracted_dir = tmp_path / "extracted"
        publish_dir = tmp_path / "publish"
        _write_extracted_record(extracted_dir, "giki.json", chunk_id="giki")
        monkeypatch.setattr(run_full, "fetch_overrides", lambda *a, **k: {})

        rc = run_full.stage_5_publish(extracted_dir, publish_dir)

        assert rc == 0
        payload = capture.payload_for("publish")
        assert payload["decision"] == "published"
        assert payload["overrides_applied"] == 0
