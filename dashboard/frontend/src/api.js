const DATA_BASE = "/data";
const REQUEST_TIMEOUT_MS = 10_000;

async function getJson(path) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${DATA_BASE}${path}`, { signal: controller.signal });
    if (!res.ok) {
      throw new Error(`${path} failed: ${res.status}`);
    }
    const contentType = res.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) {
      // The static host's SPA rewrite returns index.html (200, text/html) for any
      // missing path — catching that here turns a JSON-parse crash into a clear signal.
      throw new Error(`${path} is likely missing or not yet published (unexpected content-type: ${contentType || "none"})`);
    }
    return await res.json();
  } finally {
    clearTimeout(timeout);
  }
}

export function fetchInstitutions() {
  return getJson("/institutions.json");
}

// health.json (Phase T Task 4) is the 4th static file the pipeline publishes
// -- same-origin here (unlike the admin CMS, which fetches it cross-origin
// from this site). Used only for the footer's "Data last updated" stamp
// (Task 5.3): a fetch failure just means the stamp is omitted, never a
// blocking error for the records the rest of the page already loaded.
export function fetchHealth() {
  return getJson("/health.json");
}

// No caching of records.json here on purpose: the retry button in App.jsx
// bumps reloadToken specifically to re-fetch, and once records come from a
// static file the cron pipeline overwrites periodically, a fresh fetch is
// also how the dashboard picks up newly published data without a full page
// reload. Filtering (institution/degree-level) that used to happen
// server-side in dashboard/backend/main.py::list_records now happens here.
export async function fetchRecords({ institutionId, degreeLevel } = {}) {
  const records = await getJson("/records.json");
  // Undergraduate is the default when no degree_level filter is chosen —
  // the dashboard's default view is undergrad-only, Ambiguous is opt-in
  // (CLAUDE.md hard rule). "Ambiguous" maps to a null degree_level.value.
  const wanted = degreeLevel ?? "Undergraduate";
  const wantedValue = wanted === "Ambiguous" ? null : wanted;
  return records.filter((r) => {
    if (institutionId && r.institution_id !== institutionId) return false;
    return r.degree_level.value === wantedValue;
  });
}
