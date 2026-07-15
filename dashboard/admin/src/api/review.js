import { doc, setDoc } from "firebase/firestore";
import { db, auth } from "../firebase";
import { contentHash } from "../lib/contentHash";

// The admin app reads the needs-review queue by fetching the PUBLIC site's
// static needs_review.json cross-origin -- same pattern as api/records.js's
// fetchPublishedRecords, and the same reasoning: it reviews exactly what the
// pipeline last queued, not any internal pipeline state.
const NEEDS_REVIEW_URL =
  import.meta.env.VITE_PUBLIC_NEEDS_REVIEW_URL ||
  "https://admissions-intelligence-2fc32.web.app/data/needs_review.json";

export { contentHash };

export async function fetchNeedsReviewRecords() {
  const resp = await fetch(NEEDS_REVIEW_URL);
  if (!resp.ok) {
    throw new Error(`Failed to fetch needs-review records (HTTP ${resp.status})`);
  }
  const data = await resp.json();
  if (!Array.isArray(data)) {
    throw new Error("needs_review.json was not an array");
  }
  return data;
}

/* Record a curator's approve/reject call on a needs-review record. Keyed by
 * chunk_id + the record's CURRENT content_hash (computed here, from exactly
 * what the curator is looking at) so pipeline/review.py can detect a stale
 * decision if a later re-scrape changes the record's content before this
 * decision is consumed at publish time. */
export async function submitReviewDecision(chunkId, record, decision) {
  if (decision !== "approved" && decision !== "rejected") {
    throw new Error(`Invalid decision: ${decision}`);
  }
  const uid = auth.currentUser?.uid;
  if (!uid) {
    throw new Error("Must be signed in to submit a review decision.");
  }

  const payload = {
    chunk_id: chunkId,
    institution_id: record.institution_id,
    campus: record.campus ?? null,
    decision,
    content_hash: await contentHash(record),
    decided_by: uid, // opaque UID only -- never email/name (public-read collection)
    decided_at: new Date().toISOString(),
  };

  await setDoc(doc(db, "review_decisions", chunkId), payload);
  return payload;
}
