import { useEffect, useState } from "react";
import {
  fetchPipelineSchedule,
  requestPipelineRun,
  savePipelineSchedule,
} from "../api/pipelineSchedule";
import { fetchLastPipelineRun } from "../api/pipelineStatus";

const MODES = [
  { value: "manual", label: "Manual only" },
  { value: "interval_hours", label: "Every X hours" },
  { value: "weekly", label: "Weekly (day + time)" },
  { value: "interval_weeks", label: "Every X weeks" },
  { value: "monthly", label: "Monthly (day of month + time)" },
];

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

// Fresh, valid defaults for each mode -- must match pipeline/schedule_gate
// .py::_validate_schedule's required fields exactly, so a freshly-selected
// mode is always immediately saveable rather than needing every field
// filled in by hand first.
function defaultsForMode(mode) {
  switch (mode) {
    case "interval_hours":
      return { mode, interval_hours: 6 };
    case "weekly":
      return { mode, weekly_day: 1, weekly_time_utc: "06:00" };
    case "interval_weeks":
      return {
        mode,
        interval_weeks: 2,
        weekly_time_utc: "06:00",
        interval_anchor: new Date().toISOString().slice(0, 10),
      };
    case "monthly":
      return { mode, monthly_day: 1, weekly_time_utc: "06:00" };
    default:
      return { mode: "manual" };
  }
}

export default function PipelineControl() {
  const [schedule, setSchedule] = useState(null); // null while loading
  const [error, setError] = useState(null);
  const [saveStatus, setSaveStatus] = useState(null); // null | "saving" | "saved" | error string
  const [lastRun, setLastRun] = useState(undefined); // undefined = loading, null = unavailable
  const [runStatus, setRunStatus] = useState(null); // null | "requesting" | "requested" | error string

  useEffect(() => {
    let cancelled = false;
    fetchPipelineSchedule()
      .then((s) => {
        if (!cancelled) setSchedule(s);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Could not load the pipeline schedule.");
      });
    fetchLastPipelineRun()
      .then((r) => {
        if (!cancelled) setLastRun(r);
      })
      .catch(() => {
        if (!cancelled) setLastRun(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSave() {
    setSaveStatus("saving");
    try {
      const saved = await savePipelineSchedule(schedule);
      setSchedule(saved);
      setSaveStatus("saved");
    } catch (e) {
      setSaveStatus(e?.message || "Save failed.");
    }
  }

  async function handleRunNow() {
    setRunStatus("requesting");
    try {
      await requestPipelineRun();
      setRunStatus("requested");
    } catch (e) {
      setRunStatus(e?.message || "Request failed.");
    }
  }

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!schedule) return <p className="muted">Loading pipeline schedule…</p>;

  return (
    <section className="card settings-panel">
      <h2>Pipeline Schedule</h2>
      <p className="muted">
        Controls when the admissions pipeline runs automatically. A separate,
        frequently-run check (every ~15 minutes) reads this configuration and
        triggers the pipeline when it's due — changes here can take up to
        that long to take effect. "Run Now" works regardless of the mode
        selected below.
      </p>

      <label className="settings-panel__row settings-panel__row--column">
        Cadence
        <select
          className="input"
          value={schedule.mode}
          onChange={(e) => setSchedule(defaultsForMode(e.target.value))}
        >
          {MODES.map((m) => (
            <option key={m.value} value={m.value}>{m.label}</option>
          ))}
        </select>
      </label>

      {schedule.mode === "interval_hours" && (
        <label className="settings-panel__row settings-panel__row--column">
          Every N hours
          <input
            className="input"
            type="number"
            min="1"
            step="1"
            value={schedule.interval_hours}
            onChange={(e) => setSchedule({ ...schedule, interval_hours: Number(e.target.value) })}
          />
        </label>
      )}

      {schedule.mode === "weekly" && (
        <>
          <label className="settings-panel__row settings-panel__row--column">
            Day of week (UTC)
            <select
              className="input"
              value={schedule.weekly_day}
              onChange={(e) => setSchedule({ ...schedule, weekly_day: Number(e.target.value) })}
            >
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={i}>{d}</option>
              ))}
            </select>
          </label>
          <label className="settings-panel__row settings-panel__row--column">
            Time (UTC)
            <input
              className="input"
              type="time"
              value={schedule.weekly_time_utc}
              onChange={(e) => setSchedule({ ...schedule, weekly_time_utc: e.target.value })}
            />
          </label>
        </>
      )}

      {schedule.mode === "interval_weeks" && (
        <>
          <label className="settings-panel__row settings-panel__row--column">
            Every N weeks
            <input
              className="input"
              type="number"
              min="1"
              max="520"
              step="1"
              value={schedule.interval_weeks}
              onChange={(e) => setSchedule({ ...schedule, interval_weeks: Number(e.target.value) })}
            />
          </label>
          <label className="settings-panel__row settings-panel__row--column">
            Starting (anchor) date, UTC
            <input
              className="input"
              type="date"
              value={schedule.interval_anchor}
              onChange={(e) => setSchedule({ ...schedule, interval_anchor: e.target.value })}
            />
          </label>
          <label className="settings-panel__row settings-panel__row--column">
            Time (UTC)
            <input
              className="input"
              type="time"
              value={schedule.weekly_time_utc}
              onChange={(e) => setSchedule({ ...schedule, weekly_time_utc: e.target.value })}
            />
          </label>
        </>
      )}

      {schedule.mode === "monthly" && (
        <>
          <label className="settings-panel__row settings-panel__row--column">
            Day of month
            <input
              className="input"
              type="number"
              min="1"
              max="28"
              step="1"
              value={schedule.monthly_day}
              onChange={(e) => setSchedule({ ...schedule, monthly_day: Number(e.target.value) })}
            />
          </label>
          <label className="settings-panel__row settings-panel__row--column">
            Time (UTC)
            <input
              className="input"
              type="time"
              value={schedule.weekly_time_utc}
              onChange={(e) => setSchedule({ ...schedule, weekly_time_utc: e.target.value })}
            />
          </label>
        </>
      )}

      <div>
        <button className="btn btn--primary" onClick={handleSave} disabled={saveStatus === "saving"}>
          {saveStatus === "saving" ? "Saving…" : "Save schedule"}
        </button>
        {saveStatus === "saved" && <span className="field__status ok">Saved.</span>}
        {saveStatus && saveStatus !== "saved" && saveStatus !== "saving" && (
          <span className="field__status err" role="alert">{saveStatus}</span>
        )}
      </div>

      <hr />

      <div className="settings-panel__row settings-panel__row--column">
        <button className="btn btn--ghost" onClick={handleRunNow} disabled={runStatus === "requesting"}>
          {runStatus === "requesting" ? "Requesting…" : "Run Now"}
        </button>
        {runStatus === "requested" && (
          <span className="muted">Requested — checked within ~15 minutes.</span>
        )}
        {runStatus && runStatus !== "requested" && runStatus !== "requesting" && (
          <span className="field__status err" role="alert">{runStatus}</span>
        )}
      </div>

      <p className="muted">
        {lastRun === undefined && "Loading last run status…"}
        {lastRun === null && "Last run status unavailable."}
        {lastRun && (
          <>
            Last run: {new Date(lastRun.createdAt).toLocaleString()} — {lastRun.conclusion || lastRun.status}
            {lastRun.htmlUrl && (
              <>
                {" "}(<a href={lastRun.htmlUrl} target="_blank" rel="noreferrer">view on GitHub</a>)
              </>
            )}
          </>
        )}
      </p>
    </section>
  );
}
