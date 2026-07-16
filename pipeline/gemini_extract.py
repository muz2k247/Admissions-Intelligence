"""Stage 3b: Field extraction via Gemini API.

Reads chunks from Stage 2 and institution config, sends them to Gemini
for structured field extraction (deadline, programs,
constituent_college), and writes llm_fields.json that Stage 4 expects.

This replaces the subagent-based invocation described in
orchestration_prompt.md with a direct API call so the pipeline can run
fully automated in GitHub Actions without an AI orchestrator.

Best-effort: if this script fails, Stage 4 falls back to the regex
extractor for every chunk — a missing/corrupt llm_fields.json is never
fatal to the pipeline run.

Usage:
    GEMINI_API_KEY=... python -m pipeline.gemini_extract \\
        --chunks .tmp/chunks/chunks.json \\
        --out .tmp/chunks/llm_fields.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from google import genai

from pipeline.health import record_stage

_DEFAULT_CHUNKS = Path(".tmp") / "chunks" / "chunks.json"
_DEFAULT_OUT = Path(".tmp") / "chunks" / "llm_fields.json"
_MODEL = "gemini-3.5-flash"
_BATCH_SIZE = 40  # Smaller batches — extraction prompt is heavier than classification.
_CONFIG_PATH = Path("config") / "institutions.yaml"

_SYSTEM_PROMPT = """\
You are a field extractor for Pakistani university admissions data.
Extract four structured fields from each chunk's raw_text.

## Fields

**deadline** — the application deadline explicitly stated (e.g.
"Application Deadline: 15 August 2026"). NOT a hostel deadline, financial
aid deadline, or entry-test date. If multiple labeled deadlines exist for
different tracks, return a list of {{"label": ..., "date": ...}} pairs.
Otherwise null.

**programs** — the list of degree programs mentioned for undergraduate
admission (e.g. ["BS Computer Science", "BE Electrical Engineering"]).
Only programs the text actually names. Otherwise null.

**constituent_college** — ONLY for institutions with constituent_colleges
in the config (uhs/nums). Which specific college this chunk's content is
about. Null unless a specific college is named.

## Rules
- When unsure, return null — a null is always better than a wrong value.
- Every non-null value must have a confidence in [0.0, 1.0].
- Never use the note string "human-verified" (reserved for curators).

## Output Format
Return ONLY valid JSON, no markdown fences, no explanation. An object
keyed by chunk id:
{{
  "<chunk_id>": {{
    "deadline": {{"value": "...", "confidence": 0.9, "note": null}} | null,
    "programs": {{"value": [...], "confidence": 0.85, "note": null}} | null,
    "constituent_college": {{"value": "...", "confidence": 0.9, "note": null}} | null
  }}
}}
"""


def _load_constituent_colleges() -> dict[str, str]:
    """Load constituent_colleges from institutions.yaml, keyed by institution id."""
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        result = {}
        for inst in data.get("institutions", []):
            for src in inst.get("sources", []):
                cc = src.get("constituent_colleges")
                if cc:
                    result[inst["id"]] = cc
        return result
    except (OSError, yaml.YAMLError, KeyError):
        return {}


def _extract_batch(client: genai.Client, chunks: list[dict], constituent_colleges: dict[str, str]) -> dict:
    """Send a batch of chunks to Gemini and parse the JSON response."""
    payload = []
    for c in chunks:
        entry = {
            "id": c["id"],
            "institution": c.get("institution_id", ""),
            "raw_text": c.get("raw_text", ""),
        }
        cc = constituent_colleges.get(c.get("institution_id", ""))
        if cc:
            entry["constituent_colleges_config"] = cc
        payload.append(entry)

    user_msg = json.dumps(payload, indent=2)

    response = client.models.generate_content(
        model=_MODEL,
        contents=user_msg,
        config=genai.types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )

    text = response.text.strip()
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract fields via Gemini API.")
    parser.add_argument("--chunks", type=Path, default=_DEFAULT_CHUNKS)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable not set.", file=sys.stderr)
        record_stage("extract_llm", {
            "error": "GEMINI_API_KEY environment variable not set",
            "chunks_in": None, "chunks_extracted": 0, "batches_failed": 0,
        })
        sys.exit(1)

    if not args.chunks.is_file():
        print(f"ERROR: Chunks file not found: {args.chunks}", file=sys.stderr)
        record_stage("extract_llm", {
            "error": f"chunks file not found: {args.chunks}",
            "chunks_in": None, "chunks_extracted": 0, "batches_failed": 0,
        })
        sys.exit(1)

    chunks = json.loads(args.chunks.read_text(encoding="utf-8"))
    if not chunks:
        print("ERROR: No chunks to extract.", file=sys.stderr)
        record_stage("extract_llm", {"error": "no chunks to extract", "chunks_in": 0})
        sys.exit(1)

    constituent_colleges = _load_constituent_colleges()
    print(f"Extracting fields from {len(chunks)} chunk(s) via Gemini ({_MODEL})...")
    client = genai.Client(api_key=api_key)

    # Merge results from all batches.
    merged: dict = {}
    batches_failed = 0
    for i in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[i : i + _BATCH_SIZE]
        print(f"  Batch {i // _BATCH_SIZE + 1}: {len(batch)} chunk(s)...")
        try:
            result = _extract_batch(client, batch, constituent_colleges)
            merged.update(result)
        except Exception as exc:
            print(f"WARN: Gemini extraction failed for batch: {exc}", file=sys.stderr)
            # Best-effort: continue with remaining batches.
            batches_failed += 1
            continue

    if not merged:
        # batches_failed here reflects batches that RAISED, not batches that
        # returned cleanly but produced no usable fields -- the latter case
        # (every batch "succeeds" with an empty {} response) would still hit
        # this branch with batches_failed == 0, which is a real but accepted
        # ambiguity: either way, Stage 4 correctly falls back to regex, and
        # a curator reading health.json's warning at least sees SOME failure
        # was recorded even if the exact count under-represents it.
        print("WARN: No fields extracted from any batch — Stage 4 will use regex fallback.", file=sys.stderr)
        record_stage("extract_llm", {
            "error": "no fields extracted from any batch",
            "chunks_in": len(chunks),
            "chunks_extracted": 0,
            "batches_failed": batches_failed,
        })
        sys.exit(1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    print(f"Extraction complete: {len(merged)} chunk(s) with fields → {args.out}")
    record_stage("extract_llm", {
        "chunks_in": len(chunks),
        "chunks_extracted": len(merged),
        "batches_failed": batches_failed,
    })


if __name__ == "__main__":
    main()
