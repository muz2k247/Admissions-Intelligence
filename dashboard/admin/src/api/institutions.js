import { doc, getDoc, setDoc } from "firebase/firestore";
import { db, auth } from "../firebase";

// The admin app lists institutions by fetching the PUBLIC site's static
// institutions.json cross-origin -- same pattern as api/records.js's
// fetchPublishedRecords and api/review.js's fetchNeedsReviewRecords. Phase R
// extended _institutions_payload() (pipeline/run_full.py) to include each
// source's full campus/url/format/render (not just campus names, which is
// all the public dropdown itself needs) specifically so this tab can show
// and edit an institution's actual admissions URLs.
const PUBLIC_INSTITUTIONS_URL =
  import.meta.env.VITE_PUBLIC_INSTITUTIONS_URL ||
  "https://admissions-intelligence-2fc32.web.app/data/institutions.json";

// Must match extraction/schema.py / scraper/config.py's source shape.
export const VALID_FORMATS = ["html", "html+pdf"];
export const VALID_RENDER_MODES = ["static", "js"];

export async function fetchPublishedInstitutions() {
  const resp = await fetch(PUBLIC_INSTITUTIONS_URL);
  if (!resp.ok) {
    throw new Error(`Failed to fetch published institutions (HTTP ${resp.status})`);
  }
  const data = await resp.json();
  if (!Array.isArray(data)) {
    throw new Error("Published institutions.json was not an array");
  }
  return data;
}

export async function fetchInstitutionDoc(institutionId) {
  const snap = await getDoc(doc(db, "institutions", institutionId));
  return snap.exists() ? snap.data() : null;
}

/* Write the FULL institution shape to institutions/{institutionId} --
 * pipeline/institutions_registry.py::merge_institutions treats a Firestore
 * institution doc as wholly replacing the YAML entry (institution-level
 * replace, not a deep field merge), so every save here must include every
 * field the curator wants to keep, never a partial patch. `added_by`/
 * `added_at` are stamped once (on the first save for this id, whether it
 * originated from the YAML seed or not) and preserved across later edits;
 * `updated_by`/`updated_at` are stamped on every save. */
export async function saveInstitution(institutionId, institution) {
  const uid = auth.currentUser?.uid;
  if (!uid) {
    throw new Error("Must be signed in to save an institution.");
  }

  const existing = await fetchInstitutionDoc(institutionId);
  const now = new Date().toISOString();

  const payload = {
    name: institution.name,
    admitting_body: institution.admitting_body,
    ug_pg_mixed: institution.ug_pg_mixed,
    enabled: institution.enabled,
    sources: institution.sources,
    added_by: existing?.added_by ?? uid,
    added_at: existing?.added_at ?? now,
    updated_by: uid,
    updated_at: now,
  };

  await setDoc(doc(db, "institutions", institutionId), payload);
  return payload;
}

/* Tombstone an institution -- pipeline/institutions_registry.py excludes any
 * institution whose doc has `deleted: true` entirely, regardless of whether
 * it originated from the YAML seed or a prior curator addition. A minimal
 * doc is enough; no other fields are read once `deleted` is true. */
export async function deleteInstitution(institutionId) {
  const uid = auth.currentUser?.uid;
  if (!uid) {
    throw new Error("Must be signed in to delete an institution.");
  }

  const payload = {
    deleted: true,
    deleted_by: uid,
    deleted_at: new Date().toISOString(),
  };

  await setDoc(doc(db, "institutions", institutionId), payload);
  return payload;
}
