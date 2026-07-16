// Shared fetch helper for the three static public-data files the admin app
// reads cross-origin from the PUBLIC site (records.json, needs_review.json,
// institutions.json) -- see the per-file comments in records.js/review.js/
// institutions.js for why the admin app reads the public site's static
// output rather than any internal pipeline state.
//
// Centralizing this catches the same failure mode in all three places: if a
// file isn't published yet, Firebase Hosting's SPA rewrite (`** ->
// /index.html`) returns HTTP 200 with `text/html`, which used to surface as
// a raw `Unexpected token '<'` JSON-parse crash instead of a clear label.
const REQUEST_TIMEOUT_MS = 10_000;

// Shared core: fetch + ok/timeout/content-type/parse checks, common to every
// published static file regardless of its top-level JSON shape (array for
// records.json/institutions.json/needs_review.json, object for health.json).
async function _fetchPublicJsonRaw(url, label) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let resp;
  try {
    resp = await fetch(url, { signal: controller.signal });
  } catch (e) {
    if (e?.name === "AbortError") {
      throw new Error(`Timed out fetching ${label}`);
    }
    throw e;
  } finally {
    clearTimeout(timeout);
  }
  if (!resp.ok) {
    throw new Error(`Failed to fetch ${label} (HTTP ${resp.status})`);
  }
  const contentType = resp.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new Error(`${label} is likely missing or not yet published (unexpected content-type: ${contentType || "none"})`);
  }
  try {
    return await resp.json();
  } catch {
    throw new Error(`${label} returned invalid JSON`);
  }
}

export async function fetchPublicJson(url, label) {
  const data = await _fetchPublicJsonRaw(url, label);
  if (!Array.isArray(data)) {
    throw new Error(`${label} was not an array`);
  }
  return data;
}

// health.json (Phase T Task 4) is a single object, not an array -- every
// other published static file this app reads is an array, hence the
// separate shape check rather than reusing fetchPublicJson's.
export async function fetchPublicJsonObject(url, label) {
  const data = await _fetchPublicJsonRaw(url, label);
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    throw new Error(`${label} was not a JSON object`);
  }
  return data;
}
