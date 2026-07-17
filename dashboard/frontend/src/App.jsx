import { useEffect, useMemo, useState } from "react";
import { fetchHealth, fetchInstitutions, fetchRecords } from "./api";
import FilterBar from "./components/FilterBar";
import RecordCard from "./components/RecordCard";
import EmptyState from "./components/EmptyState";
import ThemeToggle from "./components/ThemeToggle";

// health.finished_at is validated the same way as an invalid/garbage
// fetched_at anywhere else in this project: fall back to omitting the
// footer stamp entirely rather than rendering "Invalid Date".
function formattedFinishedAt(finishedAt) {
  if (!finishedAt) return null;
  const date = new Date(finishedAt);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function App() {
  const [institutions, setInstitutions] = useState([]);
  const [records, setRecords] = useState([]);
  const [institutionId, setInstitutionId] = useState("");
  const [degreeLevel, setDegreeLevel] = useState("Undergraduate");
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [reloadToken, setReloadToken] = useState(0);
  // null (not yet loaded / failed) -- footer line is simply omitted; the
  // permanent staleness alarm this stamp exists for (root cause C, Phase T)
  // only works if a fetch problem hides the line rather than showing a
  // stale or garbled one.
  const [lastUpdated, setLastUpdated] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchInstitutions()
      .then((data) => {
        if (!cancelled) setInstitutions(data);
      })
      .catch(() => {
        /* institutions list is filter metadata only; a failure here still
         * lets records load and the institution filter degrades to empty */
      });
    fetchHealth()
      .then((health) => {
        if (!cancelled) setLastUpdated(formattedFinishedAt(health?.finished_at));
      })
      .catch(() => {
        /* health.json is diagnostic, not load-bearing -- a failure here just
         * omits the footer stamp, never blocks the records the page shows. */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    fetchRecords({ institutionId: institutionId || undefined, degreeLevel: degreeLevel || undefined })
      .then((data) => {
        if (cancelled) return;
        setRecords(data);
        setStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [institutionId, degreeLevel, reloadToken]);

  const institutionById = useMemo(() => {
    const map = new Map();
    for (const inst of institutions) map.set(inst.id, inst);
    return map;
  }, [institutions]);

  return (
    <>
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      <header className="app-header">
        <div className="app-header__inner">
          <h1 className="app-header__title">Admissions Intelligence</h1>
          <div className="app-header__actions">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main id="main-content" className="app-main">
        <FilterBar
          institutions={institutions}
          institutionId={institutionId}
          onInstitutionChange={setInstitutionId}
          degreeLevel={degreeLevel}
          onDegreeLevelChange={setDegreeLevel}
        />

        {status === "loading" && (
          <div className="record-grid" aria-busy="true">
            <span className="visually-hidden" role="status">
              Loading records…
            </span>
            {[1, 2, 3].map((i) => (
              <div key={i} className="record-card record-card--skeleton" aria-hidden="true" />
            ))}
          </div>
        )}

        {status === "error" && (
          <EmptyState
            title="Couldn't load admission records"
            message={
              <>
                The dashboard couldn't load the published data. This site is fully static — check
                that the pipeline has published <code>records.json</code> to the site, then{" "}
                <button type="button" className="link-button" onClick={() => setReloadToken((t) => t + 1)}>
                  try again
                </button>
                .
              </>
            }
          />
        )}

        {status === "ready" && records.length === 0 && (
          <EmptyState
            title="No records yet"
            message="No extracted admission records match this filter. Run the scraper and extraction pipeline, or adjust your filters."
          />
        )}

        {status === "ready" && records.length > 0 && (
          <div className="record-grid">
            {records.map((record) => (
              <RecordCard
                key={record.chunk_id}
                record={record}
                institutionName={institutionById.get(record.institution_id)?.name}
                isAdmittingBody={institutionById.get(record.institution_id)?.admitting_body ?? false}
              />
            ))}
          </div>
        )}
      </main>

      {lastUpdated && (
        <footer className="app-footer">
          <span>Data last updated {lastUpdated}</span>
        </footer>
      )}
    </>
  );
}
