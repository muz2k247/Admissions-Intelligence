"""Firestore adapter for extracted records storage and retrieval.

Handles authentication via Firebase service account (from .env) and provides
a clean interface for backend to read/write records to Firestore.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from extraction.schema import ExtractedRecord


def _init_firebase() -> tuple[object, object]:
    """Initialize Firebase Admin SDK and return (app, firestore_client).

    Uses FIREBASE_SERVICE_ACCOUNT_KEY_JSON env var (JSON string) or
    FIREBASE_SERVICE_ACCOUNT_KEY_PATH (file path).

    Returns (None, None) if Firebase is not configured (for local development).
    """
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        print("[WARN] firebase-admin not installed. Falling back to local storage.")
        return None, None

    project_id = os.environ.get("FIREBASE_PROJECT_ID")
    if not project_id:
        print("[WARN] FIREBASE_PROJECT_ID not set. Using local storage fallback.")
        return None, None

    # Try JSON string first
    key_json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_JSON")
    if key_json_str:
        try:
            key_dict = json.loads(key_json_str)
            cred = credentials.Certificate(key_dict)
            app = firebase_admin.initialize_app(cred, {"projectId": project_id})
            db = firestore.client(app)
            return app, db
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[WARN] Failed to parse FIREBASE_SERVICE_ACCOUNT_KEY_JSON: {e}")

    # Try file path
    key_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_PATH")
    if key_path and os.path.exists(key_path):
        try:
            cred = credentials.Certificate(key_path)
            app = firebase_admin.initialize_app(cred, {"projectId": project_id})
            db = firestore.client(app)
            return app, db
        except Exception as e:
            print(f"[WARN] Failed to load service account from {key_path}: {e}")

    print("[WARN] No valid Firebase credentials found. Using local storage fallback.")
    return None, None


# Lazy initialize Firebase on first use
_firebase_app = None
_firestore_db = None


def _get_firestore_client() -> Optional[object]:
    """Get or initialize Firestore client."""
    global _firebase_app, _firestore_db
    if _firestore_db is None and _firebase_app is None:
        _firebase_app, _firestore_db = _init_firebase()
    return _firestore_db


def load_records_from_firestore(
    collection: str = "extracted_records",
    institution_id: Optional[str] = None,
    degree_level: Optional[str] = None,
) -> list[ExtractedRecord]:
    """Load extracted records from Firestore.

    Args:
        collection: Firestore collection name (default: extracted_records)
        institution_id: Optional filter by institution
        degree_level: Optional filter by degree level (Undergraduate, Postgraduate, Ambiguous)

    Returns:
        List of ExtractedRecord objects. Returns [] if Firestore unavailable.
    """
    db = _get_firestore_client()
    if db is None:
        # Firestore not configured; fall back to empty list
        # (In production, backend should use local file storage as fallback)
        return []

    try:
        query = db.collection(collection)

        if institution_id is not None:
            query = query.where("institution_id", "==", institution_id)

        if degree_level is not None:
            # degree_level filter: query for records where degree_level.value == degree_level
            # (or None if degree_level is "Ambiguous")
            wanted_value = None if degree_level == "Ambiguous" else degree_level
            query = query.where("degree_level.value", "==", wanted_value)

        docs = query.stream()
        records = []
        for doc in docs:
            try:
                record = ExtractedRecord.from_dict(doc.to_dict())
                records.append(record)
            except (KeyError, ValueError, TypeError):
                # Skip malformed records (same behavior as file-based backend)
                continue

        return records

    except Exception as e:
        print(f"[ERROR] Firestore query failed: {e}")
        return []


def write_record_to_firestore(
    record: ExtractedRecord,
    collection: str = "extracted_records",
    document_id: Optional[str] = None,
) -> bool:
    """Write an extracted record to Firestore.

    Args:
        record: ExtractedRecord to write
        collection: Firestore collection name
        document_id: Optional document ID (defaults to chunk_id)

    Returns:
        True if successful, False otherwise.
    """
    db = _get_firestore_client()
    if db is None:
        print("[WARN] Firestore not configured; skipping write.")
        return False

    doc_id = document_id or record.chunk_id
    try:
        db.collection(collection).document(doc_id).set(record.to_dict())
        return True
    except Exception as e:
        print(f"[ERROR] Failed to write record {doc_id} to Firestore: {e}")
        return False


def delete_collection(collection: str = "extracted_records") -> bool:
    """Delete all documents in a Firestore collection (for testing/refresh).

    Args:
        collection: Collection name to delete

    Returns:
        True if successful, False otherwise.
    """
    db = _get_firestore_client()
    if db is None:
        print("[WARN] Firestore not configured; skipping delete.")
        return False

    try:
        docs = db.collection(collection).stream()
        for doc in docs:
            db.collection(collection).document(doc.id).delete()
        return True
    except Exception as e:
        print(f"[ERROR] Failed to delete collection {collection}: {e}")
        return False
