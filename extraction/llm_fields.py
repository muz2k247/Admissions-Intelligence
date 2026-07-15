"""Reads a field-extractor subagent output file and turns it into per-field
Field results keyed by chunk id.

The field-extractor subagent is invoked out-of-band (a Gemini Agent
tool call, mirroring content-classifier) -- see extraction/run.py's `chunk`
step for producing its shared input and pipeline/run_full.py's stage 4 for
consuming this module's output.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from extraction.schema import NULL_FIELD, Field

_FIELD_NAMES = ("deadline", "programs", "constituent_college")


def load_llm_field_results(path: Path | str) -> dict[str, dict[str, Field]]:
    """A single malformed field (violates Field's value/confidence invariant,
    or isn't a dict/null) degrades to NULL_FIELD with a printed warning -- it
    never fails the whole chunk or run, matching this pipeline's existing
    per-item-skip philosophy (see extraction/run.py's _load_scraped_records).
    A malformed chunk entry (not an object) skips just that chunk."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    results: dict[str, dict[str, Field]] = {}
    for chunk_id, fields in data.items():
        if not isinstance(fields, dict):
            print(f"WARN  field-extractor output for {chunk_id!r} is not an object -- skipping chunk", file=sys.stderr)
            continue
        chunk_fields: dict[str, Field] = {}
        for name in _FIELD_NAMES:
            raw = fields.get(name)
            if not raw:
                chunk_fields[name] = NULL_FIELD
                continue
            if not isinstance(raw, dict):
                # A bare string/number/list/bool in place of a {value,
                # confidence, note} object -- Field.from_dict would call
                # .get() on it and raise AttributeError, which isn't a
                # ValueError/TypeError, so it must be caught here explicitly
                # rather than falling through to the except below.
                print(f"WARN  {chunk_id!r}.{name}: field is not an object ({raw!r}) -- treated as null", file=sys.stderr)
                chunk_fields[name] = NULL_FIELD
                continue
            try:
                chunk_fields[name] = Field.from_dict(raw)
            except (ValueError, TypeError) as exc:
                print(f"WARN  {chunk_id!r}.{name}: invalid field ({exc}) -- treated as null", file=sys.stderr)
                chunk_fields[name] = NULL_FIELD
        results[chunk_id] = chunk_fields
    return results
