import { useEffect, useMemo, useState } from "react";
import { fetchInstitutions, fetchRecords } from "./api";
import FilterBar from "./components/FilterBar";
import RecordCard from "./components/RecordCard";
import EmptyState from "./components/EmptyState";
import { PrinterIcon } from "./components/Icons";

export default function App() {
  const [institutions, setInstitutions] = useState([]);
  const [records, setRecords] = useState([]);
  const [institutionId, setInstitutionId] = useState("");
  const [degreeLevel, setDegreeLevel] = useState("");
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    fetchInstitutions()
      .then(setInstitutions)
      .catch(() => {
        /* institutions list is filter metadata only; a failure here still
         * lets records load and the institution filter degrades to empty */
      });
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

      <header className="app-header no-print">
        <div className="app-header__inner">
          <h1 className="app-header__title">Admissions Intelligence</h1>
          <button type="button" className="button button--secondary" onClick={() => window.print()}>
            <PrinterIcon />
            Print / Save as PDF
          </button>
        </div>
      </header>

      <main id="main-content" className="app-main">
        <div className="app-header__title print-only">Admissions Intelligence</div>

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
                The dashboard couldn't reach the API. Check that the backend server is running,
                then{" "}
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
    </>
  );
}
