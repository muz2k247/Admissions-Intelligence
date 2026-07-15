// Pure content-hash logic, deliberately free of Firebase/Vite imports so it
// can run standalone under plain Node (see scripts/verify_hash_parity.mjs)
// as well as inside the built app. Mirrors extraction/review_gate.py --
// keep both in sync; see that module's docstring for the full rationale.

// Must match extraction/review_gate.py::REVIEW_FIELDS exactly -- the order
// and membership of fields hashed into content_hash.
export const REVIEW_FIELDS = ["deadline", "programs", "constituent_college", "admissions_open"];

/* Canonical JSON encoding matching Python's
 * json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 * exactly: object keys sorted at every nesting level, no whitespace, and
 * (like ensure_ascii=False) non-ASCII characters left unescaped -- native
 * JSON.stringify already leaves non-ASCII unescaped by default, so only the
 * key-sorting and separator behavior need to be reproduced by hand.
 *
 * One known, accepted gap: as of ES2019, JSON.stringify escapes the U+2028/
 * U+2029 line/paragraph separator characters (to make its output safely
 * eval-able as JavaScript); Python's json.dumps does not. A program name or
 * deadline text containing one of those exact code points would hash
 * differently on each side. Not fixed here -- these are non-printing
 * separator characters that won't appear in real admissions text, and
 * handling them would mean hand-rolling full string escaping instead of
 * delegating to JSON.stringify for every leaf value. */
export function canonicalStringify(value) {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) {
    return "[" + value.map(canonicalStringify).join(",") + "]";
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalStringify(value[k])).join(",") + "}";
  }
  return JSON.stringify(value);
}

async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/* Content hash of a record's four reviewable fields, matching
 * extraction/review_gate.py::content_hash() byte-for-byte -- this is what a
 * curator's approve/reject decision is keyed against (alongside chunk_id).
 * See tests/test_review_gate.py's fixed-fixture test and
 * dashboard/admin/scripts/verify_hash_parity.mjs for the cross-language
 * parity check. */
export async function contentHash(record) {
  const values = REVIEW_FIELDS.map((name) => record[name]?.value ?? null);
  return sha256Hex(canonicalStringify(values));
}
