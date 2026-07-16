"""Regression test for a real production bug (2026-07-16, live verification
run): pipeline/gemini_classify.py and pipeline/gemini_extract.py both do
`from pipeline.health import record_stage` (Phase T Task 4). Invoking either
script as `python pipeline/gemini_classify.py` (a direct script path) puts
the pipeline/ directory itself -- not the repo root -- on sys.path[0], so
that import raises ModuleNotFoundError before main() ever runs. This crashed
Stage 3a outright in ~2 seconds with zero health.json "classify" section
recorded, the opposite of what Task 4 exists to guarantee.

Fix: .github/workflows/pipeline.yml invokes both scripts via `-m
pipeline.gemini_classify` / `-m pipeline.gemini_extract` (module form,
matching pipeline/run_full.py's existing invocation style), which puts the
current working directory (the repo root, in CI) on sys.path[0] instead.

This test can't exercise the actual import failure without the google-genai
dependency (not installed in this project's local/test environment, by
design -- these two scripts call a live external API and aren't otherwise
unit tested), so it guards the fix at the workflow-config level instead:
plain text/YAML inspection, no dependencies, catches a regression back to
the direct-script-path form.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_PIPELINE_YML = Path(__file__).parent.parent / ".github" / "workflows" / "pipeline.yml"


def _step_run_commands():
    workflow = yaml.safe_load(_PIPELINE_YML.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["pipeline"]["steps"]
    return {step["name"]: step["run"] for step in steps if "run" in step}


def test_classify_invoked_as_module_not_direct_script():
    commands = _step_run_commands()
    run = commands["Stage 3a: Classify via Gemini"]
    assert "-m pipeline.gemini_classify" in run
    assert "pipeline/gemini_classify.py" not in run


def test_extract_invoked_as_module_not_direct_script():
    commands = _step_run_commands()
    run = commands["Stage 3b: Extract fields via Gemini (best-effort)"]
    assert "-m pipeline.gemini_extract" in run
    assert "pipeline/gemini_extract.py" not in run
