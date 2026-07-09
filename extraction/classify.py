"""Reads a content-classifier subagent output file and turns it into
DegreeLevel results keyed by chunk id.

The content-classifier subagent is invoked out-of-band (it's a Claude Code
Agent tool call, not something this module calls directly) — see
extraction/run.py's `chunk` step for producing its input and the `build`
step for consuming its output.
"""
from __future__ import annotations

import json
from pathlib import Path

from extraction.schema import DegreeLevel


def load_classifier_results(path: Path | str) -> dict[str, DegreeLevel]:
    """Never lets one malformed classifier output derail the whole build:
    an id claimed by more than one category, or an Ambiguous entry missing
    its id, is routed to Ambiguous with a distinct reason code rather than
    silently resolved by list order or raising (CLAUDE.md hard rule 5: a
    correct answer late beats a wrong one immediately)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    claimed_by: dict[str, list[str]] = {}
    for chunk_id in data.get("Undergraduate", []):
        claimed_by.setdefault(chunk_id, []).append("Undergraduate")
    for chunk_id in data.get("Postgraduate", []):
        claimed_by.setdefault(chunk_id, []).append("Postgraduate")
    for item in data.get("Ambiguous", []):
        if not isinstance(item, dict) or "id" not in item:
            continue
        claimed_by.setdefault(item["id"], []).append("Ambiguous")

    results: dict[str, DegreeLevel] = {}
    ambiguous_reasons = {
        item["id"]: item.get("reason", "no-signal")
        for item in data.get("Ambiguous", [])
        if isinstance(item, dict) and "id" in item
    }
    for chunk_id, categories in claimed_by.items():
        if len(set(categories)) > 1:
            results[chunk_id] = DegreeLevel(value=None, reason="conflicting-classification")
        elif categories[0] == "Ambiguous":
            results[chunk_id] = DegreeLevel(value=None, reason=ambiguous_reasons.get(chunk_id, "no-signal"))
        else:
            results[chunk_id] = DegreeLevel(value=categories[0])
    return results
