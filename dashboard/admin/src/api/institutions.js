import { collection, doc, getDoc, getDocs, setDoc } from "firebase/firestore";
import { db, auth } from "../firebase";
import { fetchPublicJson } from "./publicData";

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
  return fetchPublicJson(PUBLIC_INSTITUTIONS_URL, "published institutions.json");
}

export async function fetchInstitutionDoc(institutionId) {
  const snap = await getDoc(doc(db, "institutions", institutionId));
  return snap.exists() ? snap.data() : null;
}

/* Every id currently live in the `institutions` collection, mapped to
 * whether it's tombstoned (`deleted: true`). Used to correct a real
 * staleness gap in the "create new institution" id-uniqueness check:
 * fetchPublishedInstitutions() only reflects Firestore state as of the
 * LAST pipeline publish (up to a week old per .github/workflows/
 * pipeline.yml's cron schedule), so a very recently added -- or deleted --
 * institution needs a live check, not the stale published snapshot, or a
 * curator could unknowingly pick an id collision that silently clobbers
 * another institution's doc on save (setDoc is a full replace, not a
 * merge). A tombstoned id is intentionally NOT treated as "taken" --
 * recreating a previously-deleted id is a legitimate curator action. */
export async function fetchLiveInstitutionTombstones() {
  const snap = await getDocs(collection(db, "institutions"));
  const tombstones = new Map();
  snap.forEach((d) => tombstones.set(d.id, d.data()?.deleted === true));
  return tombstones;
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
