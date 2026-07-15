import { doc, getDoc, setDoc } from "firebase/firestore";
import { db, auth } from "../firebase";

// Must match pipeline/review.py::DEFAULT_SETTINGS -- fail-safe direction is
// "gate stays on", never "gate off" (an unreachable/missing settings doc
// must never let low-confidence data publish unreviewed).
export const DEFAULT_REVIEW_SETTINGS = { enabled: true, threshold: 0.8 };

export async function fetchReviewSettings() {
  const snap = await getDoc(doc(db, "settings", "review_gate"));
  if (!snap.exists()) return { ...DEFAULT_REVIEW_SETTINGS };

  const data = snap.data();
  const enabled = typeof data.enabled === "boolean" ? data.enabled : DEFAULT_REVIEW_SETTINGS.enabled;
  const threshold =
    typeof data.threshold === "number" && data.threshold >= 0 && data.threshold <= 1
      ? data.threshold
      : DEFAULT_REVIEW_SETTINGS.threshold;
  return { enabled, threshold };
}

export async function saveReviewSettings({ enabled, threshold }) {
  const uid = auth.currentUser?.uid;
  if (!uid) {
    throw new Error("Must be signed in to change review-gate settings.");
  }
  if (typeof enabled !== "boolean") {
    throw new Error("enabled must be a boolean.");
  }
  if (typeof threshold !== "number" || Number.isNaN(threshold) || threshold < 0 || threshold > 1) {
    throw new Error("threshold must be a number between 0 and 1.");
  }

  const payload = {
    enabled,
    threshold,
    updated_by: uid, // opaque UID only -- never email/name (public-read document)
    updated_at: new Date().toISOString(),
  };

  await setDoc(doc(db, "settings", "review_gate"), payload);
  return payload;
}
