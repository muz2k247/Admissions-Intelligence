import { doc, getDoc, setDoc } from "firebase/firestore";
import { db, auth } from "../firebase";

// Must match pipeline/overrides.py's _OVERRIDABLE_FIELDS exactly -- the four
// Field-typed attributes a curator may correct. degree_level is deliberately
// not here (UG/PG routing stays a classifier decision).
export const OVERRIDABLE_FIELDS = ["deadline", "fee", "programs", "constituent_college"];

export async function fetchOverride(chunkId) {
  const snap = await getDoc(doc(db, "overrides", chunkId));
  return snap.exists() ? snap.data() : null;
}

/* Write one field correction to overrides/{chunkId}. The document shape must
 * stay compatible with pipeline/overrides.py's reader: fields.<name> carries
 * {value, confidence, note} (the reader ignores the audit keys). We always
 * write confidence 1.0 and the exact string note "human-verified" so the
 * admin CMS's FieldEditor can special-case it as a "Verified" chip (the
 * public dashboard shows neither confidence nor verified status -- see
 * dashboard/frontend/src/components/RecordCard.jsx). `value` may be a
 * string or (for programs) an array of strings. */
export async function saveFieldOverride(chunkId, record, fieldName, value) {
  if (!OVERRIDABLE_FIELDS.includes(fieldName)) {
    throw new Error(`Not an overridable field: ${fieldName}`);
  }
  const uid = auth.currentUser?.uid;
  if (!uid) {
    throw new Error("Must be signed in to save a correction.");
  }

  const existing = await fetchOverride(chunkId);
  const existingField = existing?.fields?.[fieldName];

  // Capture the pipeline-extracted value as `original` (audit trail) only the
  // FIRST time this field is corrected -- across re-edits, preserve the
  // earliest-captured original rather than overwriting it with a prior
  // correction. record[fieldName] is the published {value, confidence, note}.
  const original =
    existingField && "original" in existingField
      ? existingField.original
      : record[fieldName]?.value ?? null;

  const fieldEntry = {
    value,
    confidence: 1.0,
    note: "human-verified",
    original,
    verified_by: uid, // opaque UID only -- never email/name (public-read collection)
    verified_at: new Date().toISOString(),
  };

  const merged = {
    chunk_id: chunkId,
    institution_id: record.institution_id,
    campus: record.campus ?? null,
    fields: { ...existing?.fields, [fieldName]: fieldEntry },
    updated_at: new Date().toISOString(),
  };

  await setDoc(doc(db, "overrides", chunkId), merged);
  return merged;
}
