"""Stage 3a: Content classification via Gemini API.

Reads chunks from Stage 2, sends them to Gemini for UG/PG/Ambiguous
classification, and writes the classified.json that Stage 4 expects.

This replaces the subagent-based invocation described in
orchestration_prompt.md with a direct API call so the pipeline can run
fully automated in GitHub Actions without an AI orchestrator.

Usage:
    GEMINI_API_KEY=... python -m pipeline.gemini_classify \\
        --chunks .tmp/chunks/chunks.json \\
        --out .tmp/chunks/classified.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from google import genai

from pipeline.health import record_stage

_DEFAULT_CHUNKS = Path(".tmp") / "chunks" / "chunks.json"
_DEFAULT_OUT = Path(".tmp") / "chunks" / "classified.json"
_MODEL = "gemini-3.5-flash"

# Max chunks per API call to stay within context limits.  Most sources
# produce a handful of chunks each, so 15 institutions × ~3 chunks ≈ 45
# total — well within a single call.  The batch loop is a safety net.
_BATCH_SIZE = 60

_SYSTEM_PROMPT = """\
You are a content classifier for Pakistani university admissions data.
Classify each chunk of scraped admission content into exactly one of
three categories based on its raw_text.

**Undergraduate** — explicitly references a bachelor's-level program or
entry route: BS, BE, B.Sc, BBA, MBBS, BDS, or other 4/5-year first-degree
programs; ECAT, NUST NET (UG track), FAST entry test (UG), MDCAT,
GIKI/PIEAS entry tests; Associate Degree Programs (ADP);
Intermediate/FSc/A-Level eligibility requirements.

**Postgraduate** — explicitly references MS, MPhil, PhD, or postgraduate
diplomas/certificates; programs requiring a prior bachelor's degree;
GAT/GRE/HAT-based entry tests.

**Ambiguous** — route here when:
- The announcement covers both UG and PG without clearly separable sections
- The degree level isn't stated and can't be inferred
- The text is truncated or extraction broke
- The content isn't an admissions announcement at all (hostel notice, etc.)

When genuinely unsure, Ambiguous is always correct — never force a guess.

Return ONLY valid JSON in this exact format, no markdown fences, no
explanation:
{
  "Undergraduate": ["chunk_id_1", ...],
  "Postgraduate": ["chunk_id_2", ...],
  "Ambiguous": [{"id": "chunk_id_3", "reason": "mixed-degree-level|no-signal|extraction-broken|not-admissions-content"}, ...]
}
"""


def _classify_batch(client: genai.Client, chunks: list[dict]) -> dict:
    """Send a batch of chunks to Gemini and parse the JSON response."""
    user_msg = json.dumps(
        [{"id": c["id"], "institution": c.get("institution_id", ""), "raw_text": c.get("raw_text", "")} for c in chunks],
        indent=2,
    )

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
    parser = argparse.ArgumentParser(description="Classify chunks via Gemini API.")
    parser.add_argument("--chunks", type=Path, default=_DEFAULT_CHUNKS)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable not set.", file=sys.stderr)
        record_stage("classify", {"error": "GEMINI_API_KEY environment variable not set", "chunks_in": None})
        sys.exit(1)

    if not args.chunks.is_file():
        print(f"ERROR: Chunks file not found: {args.chunks}", file=sys.stderr)
        record_stage("classify", {"error": f"chunks file not found: {args.chunks}", "chunks_in": None})
        sys.exit(1)

    chunks = json.loads(args.chunks.read_text(encoding="utf-8"))
    if not chunks:
        print("ERROR: No chunks to classify.", file=sys.stderr)
        record_stage("classify", {"error": "no chunks to classify", "chunks_in": 0})
        sys.exit(1)

    print(f"Classifying {len(chunks)} chunk(s) via Gemini ({_MODEL})...")
    client = genai.Client(api_key=api_key)

    # Merge results from all batches.
    merged: dict[str, list] = {"Undergraduate": [], "Postgraduate": [], "Ambiguous": []}
    for i in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[i : i + _BATCH_SIZE]
        print(f"  Batch {i // _BATCH_SIZE + 1}: {len(batch)} chunk(s)...")
        try:
            result = _classify_batch(client, batch)
        except Exception as exc:
            print(f"ERROR: Gemini classification failed: {exc}", file=sys.stderr)
            record_stage("classify", {"error": str(exc), "chunks_in": len(chunks)})
            sys.exit(1)
        for key in merged:
            merged[key].extend(result.get(key, []))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    ug = len(merged["Undergraduate"])
    pg = len(merged["Postgraduate"])
    amb = len(merged["Ambiguous"])
    print(f"Classification complete: {ug} UG, {pg} PG, {amb} Ambiguous → {args.out}")
    record_stage("classify", {"chunks_in": len(chunks), "undergraduate": ug, "postgraduate": pg, "ambiguous": amb})


if __name__ == "__main__":
    main()
