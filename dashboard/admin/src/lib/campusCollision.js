// Mirrors scraper/config.py::campus_collision_key / find_campus_collision --
// keep both in sync. Two sources within one institution collide on the same
// scraped-file name (pipeline/run_full.py::stage_1_scrape) or chunk_id
// (extraction/chunker.py) when their campus values normalize to the same
// key; the second-scraped source then silently overwrites the first's data
// on disk, with no warning anywhere in the pipeline. The backend
// (pipeline/institutions_registry.py::_build_institution) is the
// authoritative safety net for this -- it degrades a colliding Firestore doc
// gracefully rather than crashing a run -- but catching the mistake here,
// before it's ever saved, is far better UX than a silent WARN in a GitHub
// Actions log the curator will likely never see.

export function campusCollisionKey(campus) {
  if (campus === null || campus === undefined) return "";
  const trimmed = campus.trim();
  if (!trimmed) return "";
  return trimmed.toLowerCase().replace(/ /g, "_");
}

/* Returns [firstLabel, secondLabel] of the first two sources whose campus
 * values collide, or null if every source has a distinct campus key. */
export function findCampusCollision(sources) {
  const seen = new Map();
  for (const s of sources) {
    const key = campusCollisionKey(s.campus);
    const label = s.campus?.trim() || "(no campus)";
    if (seen.has(key)) {
      return [seen.get(key), label];
    }
    seen.set(key, label);
  }
  return null;
}
