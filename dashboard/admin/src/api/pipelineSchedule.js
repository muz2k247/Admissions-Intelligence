import { doc, getDoc, setDoc } from "firebase/firestore";
import { db, auth } from "../firebase";

// Must match pipeline/schedule_gate.py::_validate_schedule -- same five
// modes, same required fields per mode, same fail-closed direction (manual
// is the only mode that can never fire an unconfigured pipeline run, so it's
// the default whenever the doc is missing or invalid on read).
export const DEFAULT_PIPELINE_SCHEDULE = { mode: "manual" };

const VALID_MODES = ["manual", "interval_hours", "weekly", "interval_weeks", "monthly"];

const TIME_UTC_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const DATE_SHAPE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/* True only for a real calendar date, mirroring pipeline/schedule_gate.py's
 * _parse_date (datetime.strptime(value, "%Y-%m-%d")). A shape-only regex
 * (\d{4}-\d{2}-\d{2}) would accept "2026-02-30" or "2026-13-01" -- Python's
 * strptime rejects both, so a shape-only check here would let a curator
 * save an anchor date the pipeline then silently can't parse, falling back
 * to "manual" with no error surfaced anywhere. Round-tripping through
 * Date.UTC and reading the parts back catches every invalid calendar date
 * (Feb 30, month 13, day 0, etc.) the same way strptime does. */
function isValidCalendarDate(value) {
  const match = DATE_SHAPE_RE.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return false;
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

export async function fetchPipelineSchedule() {
  const snap = await getDoc(doc(db, "settings", "pipeline_schedule"));
  if (!snap.exists()) return { ...DEFAULT_PIPELINE_SCHEDULE };

  const data = snap.data();
  // Read-side mirrors the pipeline's own fail-closed validation (schedule_
  // gate.py::fetch_pipeline_schedule -> _validate_schedule): reuse the same
  // per-mode required-field check the write side enforces, so a doc left
  // malformed by any other path (a manual console edit, a future migration,
  // an interrupted write) can't render in the CMS as an active cadence the
  // Python tick side would actually treat as manual (silently never firing).
  try {
    validateSchedule(data);
  } catch {
    return { ...DEFAULT_PIPELINE_SCHEDULE };
  }
  return data;
}

/* Throws with a user-facing message on any invalid shape -- mirrors
 * pipeline/schedule_gate.py::_validate_schedule's per-mode required fields,
 * so a schedule that would fail closed to "manual" on the read side is
 * rejected here instead of silently saved and only discovered as "silently
 * not doing what I configured" days later. */
function validateSchedule(schedule) {
  if (!schedule || !VALID_MODES.includes(schedule.mode)) {
    throw new Error("Choose a valid schedule mode.");
  }
  const isFiniteNumber = (v) => typeof v === "number" && Number.isFinite(v);

  switch (schedule.mode) {
    case "manual":
      return;
    case "interval_hours":
      if (!isFiniteNumber(schedule.interval_hours) || schedule.interval_hours <= 0) {
        throw new Error("Interval hours must be a positive number.");
      }
      return;
    case "weekly":
      if (!Number.isInteger(schedule.weekly_day) || schedule.weekly_day < 0 || schedule.weekly_day > 6) {
        throw new Error("Choose a day of the week.");
      }
      if (typeof schedule.weekly_time_utc !== "string" || !TIME_UTC_RE.test(schedule.weekly_time_utc)) {
        throw new Error("Time must be a valid 24-hour UTC time.");
      }
      return;
    case "interval_weeks":
      if (!Number.isInteger(schedule.interval_weeks) || schedule.interval_weeks <= 0 || schedule.interval_weeks > 520) {
        throw new Error("Interval weeks must be a positive whole number (max 520).");
      }
      if (typeof schedule.weekly_time_utc !== "string" || !TIME_UTC_RE.test(schedule.weekly_time_utc)) {
        throw new Error("Time must be a valid 24-hour UTC time.");
      }
      if (typeof schedule.interval_anchor !== "string" || !isValidCalendarDate(schedule.interval_anchor)) {
        throw new Error("Choose a valid starting (anchor) date.");
      }
      return;
    case "monthly":
      if (!Number.isInteger(schedule.monthly_day) || schedule.monthly_day < 1 || schedule.monthly_day > 28) {
        throw new Error("Day of month must be between 1 and 28.");
      }
      if (typeof schedule.weekly_time_utc !== "string" || !TIME_UTC_RE.test(schedule.weekly_time_utc)) {
        throw new Error("Time must be a valid 24-hour UTC time.");
      }
      return;
    default:
      throw new Error("Unknown schedule mode.");
  }
}

export async function savePipelineSchedule(schedule) {
  const uid = auth.currentUser?.uid;
  if (!uid) {
    throw new Error("Must be signed in to change the pipeline schedule.");
  }
  validateSchedule(schedule);

  const payload = {
    ...schedule,
    updated_by: uid, // opaque UID only -- never email/name (public-read document)
    updated_at: new Date().toISOString(),
  };

  await setDoc(doc(db, "settings", "pipeline_schedule"), payload);
  return payload;
}

/* Writes settings/pipeline_run_request ({requested_by, requested_at}) --
 * read by pipeline/schedule_gate.py's tick, which compares requested_at
 * against the most recent pipeline run's start time to decide whether the
 * request is still pending. Nothing here ever clears the doc; that
 * comparison is what makes a satisfied request stop re-triggering, without
 * any write-back needed. */
export async function requestPipelineRun() {
  const uid = auth.currentUser?.uid;
  if (!uid) {
    throw new Error("Must be signed in to trigger a pipeline run.");
  }

  const payload = {
    requested_by: uid,
    requested_at: new Date().toISOString(),
  };

  await setDoc(doc(db, "settings", "pipeline_run_request"), payload);
  return payload;
}
