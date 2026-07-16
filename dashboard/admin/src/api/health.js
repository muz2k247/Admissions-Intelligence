// The admin app reads the pipeline's health.json by fetching the PUBLIC
// site's static file cross-origin -- same pattern as api/records.js's
// fetchPublishedRecords/api/review.js's fetchNeedsReviewRecords/api/
// institutions.js's fetchPublishedInstitutions, all via api/publicData.js's
// shared fetch helper (Phase T Task 4.4). health.json is the 4th static file
// stage 5 (well, a dedicated "Finalize health.json" CI step) publishes
// alongside records.json/institutions.json/needs_review.json -- see
// pipeline/health.py and docs/pipeline_health.md for the schema.
import { fetchPublicJsonObject } from "./publicData";

const PUBLIC_HEALTH_URL =
  import.meta.env.VITE_PUBLIC_HEALTH_URL ||
  "https://admissions-intelligence-2fc32.web.app/data/health.json";

export async function fetchPipelineHealth() {
  return fetchPublicJsonObject(PUBLIC_HEALTH_URL, "health.json");
}
