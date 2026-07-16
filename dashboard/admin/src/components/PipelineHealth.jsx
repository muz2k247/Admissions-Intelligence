import { useEffect, useState } from "react";
import { fetchPipelineHealth } from "../api/health";

const STATUS_LABELS = {
  published: "Published",
  publish_refused: "Publish refused",
  failed: "Failed",
};

const STATUS_CHIP_CLASS = {
  published: "chip--health-ok",
  publish_refused: "chip--health-warn",
  failed: "chip--health-err",
};

function formatCoverage(value) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

function SourceTable({ sources }) {
  if (!Array.isArray(sources) || sources.length === 0) return null;
  return (
    <details className="pipeline-health__sources">
      <summary>Per-source results ({sources.length})</summary>
      <div className="pipeline-health__table-wrap">
        <table className="pipeline-health__table">
          <thead>
            <tr>
              <th>Institution</th>
              <th>Campus</th>
              <th>Result</th>
              <th>PDF failures</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s, i) => (
              <tr key={`${s.institution_id}-${s.campus ?? "default"}-${i}`}>
                <td>{s.institution_id}</td>
                <td>{s.campus ?? "—"}</td>
                <td>
                  {s.ok ? (
                    <span className="field__status ok">OK</span>
                  ) : (
                    <span className="field__status err" title={s.error ?? undefined}>Failed</span>
                  )}
                </td>
                <td>{s.pdf_failure_count > 0 ? `${s.pdf_failure_count}/${s.pdf_count}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export default function PipelineHealth() {
  const [health, setHealth] = useState(null); // null while loading
  const [error, setError] = useState(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    fetchPipelineHealth()
      .then((h) => {
        if (!cancelled) setHealth(h);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Could not load pipeline health.");
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  if (error) {
    return (
      <p className="error" role="alert">
        {error}{" "}
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setReloadToken((t) => t + 1)}>
          Retry
        </button>
      </p>
    );
  }
  if (!health) return <p className="muted">Loading pipeline health…</p>;

  const scrape = health.scrape;
  const build = health.build;
  const publish = health.publish;
  const statusLabel = STATUS_LABELS[health.status] || health.status || "Unknown";
  const statusChipClass = STATUS_CHIP_CLASS[health.status] || "chip--confidence-none";

  return (
    <section className="card pipeline-health">
      <div className="pipeline-health__banner">
        <span className={`chip ${statusChipClass}`}>{statusLabel}</span>
        <span className="muted">
          {health.finished_at ? `Last run finished ${new Date(health.finished_at).toLocaleString()}` : "No run recorded yet."}
        </span>
      </div>

      {Array.isArray(health.warnings) && health.warnings.length > 0 && (
        <ul className="pipeline-health__warnings" aria-live="polite">
          {health.warnings.map((w, i) => (
            <li key={i} className="field__status err">{w}</li>
          ))}
        </ul>
      )}

      <div className="pipeline-health__stats">
        {scrape && (
          <div className="pipeline-health__stat">
            <span className="pipeline-health__stat-value">{scrape.ok ?? "—"}/{scrape.attempted ?? "—"}</span>
            <span className="muted">sources OK</span>
          </div>
        )}
        {publish && (
          <>
            <div className="pipeline-health__stat">
              <span className="pipeline-health__stat-value">{publish.records_published ?? "—"}</span>
              <span className="muted">records published</span>
            </div>
            <div className="pipeline-health__stat">
              <span className="pipeline-health__stat-value">{publish.queued_for_review ?? "—"}</span>
              <span className="muted">queued for review</span>
            </div>
          </>
        )}
        {build && (
          <div className="pipeline-health__stat">
            <span className="pipeline-health__stat-value">{build.extraction_mode ?? "—"}</span>
            <span className="muted">extraction mode</span>
          </div>
        )}
      </div>

      {publish && (publish.new_coverage != null || publish.previous_coverage != null) && (
        <p className="muted pipeline-health__coverage">
          Field coverage: {formatCoverage(publish.new_coverage)} (previous run: {formatCoverage(publish.previous_coverage)}) —{" "}
          {publish.new_institution_count ?? "—"} institution(s) (previous: {publish.previous_institution_count ?? "—"})
        </p>
      )}

      <SourceTable sources={scrape?.sources} />
    </section>
  );
}
