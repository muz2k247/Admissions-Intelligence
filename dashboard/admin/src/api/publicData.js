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

export async function fetchPublicJson(url, label) {
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
  let data;
  try {
    data = await resp.json();
  } catch {
    throw new Error(`${label} returned invalid JSON`);
  }
  if (!Array.isArray(data)) {
    throw new Error(`${label} was not an array`);
  }
  return data;
}
