"""FastAPI backend for the admissions dashboard.

Reads institution structure from config/institutions.yaml (via
scraper.config, never hardcoded here) and extracted records from
EXTRACTED_DIR (via extraction.schema, so every record served to the
frontend is schema-valid: field-level confidence, source_url present,
degree_level only ever set from the content-classifier's output).
"""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from dashboard.backend.config import EXTRACTED_DIR
from extraction.schema import ExtractedRecord
from scraper.config import load_institutions

app = FastAPI(title="Admissions Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # read-only public admissions data, no auth/cookies — open by design
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _load_records() -> list[ExtractedRecord]:
    if not EXTRACTED_DIR.is_dir():
        return []
    try:
        paths = sorted(EXTRACTED_DIR.glob("*.json"))
    except OSError:
        return []  # unreadable directory (e.g. permissions) degrades to "no records", not a 500

    records = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as f:
                records.append(ExtractedRecord.from_dict(json.load(f)))
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            continue  # one malformed record must not break the whole listing
    return records


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/institutions")
def list_institutions() -> list[dict]:
    try:
        institutions = load_institutions()
    except (OSError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"config/institutions.yaml is unreadable: {exc}") from exc
    return [
        {
            "id": inst.id,
            "name": inst.name,
            "admitting_body": inst.admitting_body,
            "ug_pg_mixed": inst.ug_pg_mixed,
            "campuses": [s.campus for s in inst.sources if s.campus is not None],
        }
        for inst in institutions
    ]


@app.get("/api/records")
def list_records(institution_id: str | None = None, degree_level: str | None = None) -> list[dict]:
    if degree_level is not None and degree_level not in ("Undergraduate", "Postgraduate", "Ambiguous"):
        raise HTTPException(status_code=400, detail="degree_level must be Undergraduate, Postgraduate, or Ambiguous")

    records = _load_records()
    if institution_id is not None:
        records = [r for r in records if r.institution_id == institution_id]
    if degree_level is not None:
        wanted = None if degree_level == "Ambiguous" else degree_level
        records = [r for r in records if r.degree_level.value == wanted]
    return [r.to_dict() for r in records]


@app.get("/api/records/{chunk_id}")
def get_record(chunk_id: str) -> dict:
    for record in _load_records():
        if record.chunk_id == chunk_id:
            return record.to_dict()
    raise HTTPException(status_code=404, detail=f"No record with chunk_id={chunk_id!r}")
