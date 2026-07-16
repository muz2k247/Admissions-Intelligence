// The admin app reviews already-published records by fetching the PUBLIC
// site's static records.json cross-origin (both are static Firebase Hosting
// sites). It never reads the pipeline's internal state -- it reviews exactly
// what the public dashboard currently shows, which is the point.
import { fetchPublicJson } from "./publicData";

const PUBLIC_DATA_URL =
  import.meta.env.VITE_PUBLIC_DATA_URL ||
  "https://admissions-intelligence-2fc32.web.app/data/records.json";

export async function fetchPublishedRecords() {
  return fetchPublicJson(PUBLIC_DATA_URL, "published records.json");
}
