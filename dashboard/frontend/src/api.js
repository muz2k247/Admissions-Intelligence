const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
const REQUEST_TIMEOUT_MS = 10_000;

async function getJson(path) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, { signal: controller.signal });
    if (!res.ok) {
      throw new Error(`${path} failed: ${res.status}`);
    }
    return await res.json();
  } finally {
    clearTimeout(timeout);
  }
}

export function fetchInstitutions() {
  return getJson("/api/institutions");
}

export function fetchRecords({ institutionId, degreeLevel } = {}) {
  const params = new URLSearchParams();
  if (institutionId) params.set("institution_id", institutionId);
  if (degreeLevel) params.set("degree_level", degreeLevel);
  const qs = params.toString();
  return getJson(`/api/records${qs ? `?${qs}` : ""}`);
}
