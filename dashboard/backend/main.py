"""FastAPI backend for the admissions dashboard.

Reads institution structure from config/institutions.yaml (via
scraper.config, never hardcoded here) and extracted records from:
- PRIMARY: Firestore (if FIREBASE_PROJECT_ID is set in .env)
- FALLBACK: Local EXTRACTED_DIR (for local development)

Every record served to frontend is schema-valid: field-level confidence,
source_url present, degree_level only set from content-classifier output.
"""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from dashboard.backend.config import EXTRACTED_DIR
from dashboard.backend.firestore_adapter import load_records_from_firestore
from extraction.schema import ExtractedRecord
from scraper.config import load_institutions

app = FastAPI(title="Admissions Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # read-only public admissions data, no auth/cookies — open by design
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _load_records(
    institution_id: str | None = None, degree_level: str | None = None
) -> list[ExtractedRecord]:
    """Load records from Firestore (primary) or local files (fallback).

    Tries Firestore first if configured via FIREBASE_PROJECT_ID env var.
    Falls back to local .tmp/extracted/ directory if Firestore unavailable.

    Args:
        institution_id: Optional filter by institution
        degree_level: Optional filter by degree level
    """
    # Try Firestore first if configured
    if os.environ.get("FIREBASE_PROJECT_ID"):
        records = load_records_from_firestore(
            institution_id=institution_id, degree_level=degree_level
        )
        if records is not None:
            return records

    # Fallback to local file storage (filter in-memory)
    if not EXTRACTED_DIR.is_dir():
        return []
    try:
        paths = sorted(EXTRACTED_DIR.glob("*.json"))
    except OSError:
        return []

    records = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as f:
                record = ExtractedRecord.from_dict(json.load(f))
                # Apply filters
                if institution_id and record.institution_id != institution_id:
                    continue
                if degree_level:
                    wanted = None if degree_level == "Ambiguous" else degree_level
                    if record.degree_level.value != wanted:
                        continue
                records.append(record)
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            continue
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

    # Project scope is undergrad-only (postgraduate records are excluded at
    # extraction time already, but default the *filter* too so an absent
    # query param never means "show everything" — Ambiguous stays reachable
    # via an explicit ?degree_level=Ambiguous for manual review).
    effective_degree_level = degree_level if degree_level is not None else "Undergraduate"

    # Pass filters to loader (Firestore can optimize; local storage filters in-memory)
    records = _load_records(institution_id=institution_id, degree_level=effective_degree_level)
    return [r.to_dict() for r in records]


@app.get("/api/records/{chunk_id}")
def get_record(chunk_id: str) -> dict:
    # Try Firestore first if configured
    if os.environ.get("FIREBASE_PROJECT_ID"):
        records = load_records_from_firestore()
        for record in records:
            if record.chunk_id == chunk_id:
                return record.to_dict()

    # Fallback to local files
    for record in _load_records():
        if record.chunk_id == chunk_id:
            return record.to_dict()

    raise HTTPException(status_code=404, detail=f"No record with chunk_id={chunk_id!r}")
