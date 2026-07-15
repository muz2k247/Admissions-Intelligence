import { useEffect, useState } from "react";
import { fetchReviewSettings, saveReviewSettings } from "../api/reviewSettings";

export default function ReviewSettings() {
  const [settings, setSettings] = useState(null); // {enabled, threshold} | null (loading)
  const [error, setError] = useState(null);
  const [status, setStatus] = useState(null); // null | "saving" | "saved" | error string

  useEffect(() => {
    let cancelled = false;
    fetchReviewSettings()
      .then((s) => {
        if (!cancelled) setSettings(s);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Could not load review-gate settings.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSave() {
    setStatus("saving");
    try {
      await saveReviewSettings(settings);
      setStatus("saved");
    } catch (e) {
      setStatus(e?.message || "Save failed.");
    }
  }

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!settings) return <p className="muted">Loading review-gate settings…</p>;

  return (
    <section className="card settings-panel">
      <h2>Needs-Review Gate</h2>
      <p className="muted">
        Records below the confidence threshold are withheld from the public dashboard
        until a curator approves, edits, or rejects them.
      </p>

      <label className="settings-panel__row">
        <input
          type="checkbox"
          checked={settings.enabled}
          onChange={(e) => setSettings({ ...settings, enabled: e.target.checked })}
        />
        Gate enabled
      </label>

      <label className="settings-panel__row settings-panel__row--column">
        Confidence threshold ({settings.threshold.toFixed(2)})
        <input
          className="input"
          type="number"
          min="0"
          max="1"
          step="0.01"
          value={settings.threshold}
          disabled={!settings.enabled}
          onChange={(e) => setSettings({ ...settings, threshold: Number(e.target.value) })}
        />
      </label>

      <button className="btn btn--primary" onClick={handleSave} disabled={status === "saving"}>
        {status === "saving" ? "Saving…" : "Save"}
      </button>
      {status === "saved" && <span className="field__status ok">Saved — applies on next publish.</span>}
      {status && status !== "saved" && status !== "saving" && (
        <span className="field__status err" role="alert">{status}</span>
      )}
    </section>
  );
}
