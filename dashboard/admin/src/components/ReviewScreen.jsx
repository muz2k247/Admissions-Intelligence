import { useEffect, useState } from "react";
import { fetchPublishedInstitutions } from "../api/institutions";
import { fetchPublishedRecords } from "../api/records";
import InstitutionsManager from "./InstitutionsManager";
import PipelineControl from "./PipelineControl";
import RecordReviewRow from "./RecordReviewRow";
import ReviewQueue from "./ReviewQueue";
import ReviewSettings from "./ReviewSettings";

const TABS = ["Published", "Needs Review", "Institutions", "Pipeline", "Settings"];

function PublishedTab({ institutionNames }) {
  const [records, setRecords] = useState(null);
  const [error, setError] = useState(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    fetchPublishedRecords()
      .then((data) => {
        if (!cancelled) setRecords(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Could not load records.");
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  return (
    <>
      {error && (
        <p className="error" role="alert">
          {error}{" "}
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setReloadToken((t) => t + 1)}>
            Retry
          </button>
        </p>
      )}
      {!error && records === null && <p className="muted">Loading records…</p>}
      {records !== null && records.length === 0 && (
        <p className="muted">No published records to review.</p>
      )}

      <div className="records">
        {records?.map((record) => (
          <RecordReviewRow key={record.chunk_id} record={record} institutionNames={institutionNames} />
        ))}
      </div>
    </>
  );
}

export default function ReviewScreen({ user, onLogOut }) {
  const [tab, setTab] = useState(TABS[0]);
  // Fetched once here (not per-tab/per-row) so PublishedTab, ReviewQueue, and
  // every RecordReviewRow inside them share the same id->name lookup instead
  // of each re-fetching institutions.json independently. A failure here just
  // means every row falls back to its raw institution_id -- never fatal to
  // the tab itself.
  const [institutionNames, setInstitutionNames] = useState({});

  useEffect(() => {
    let cancelled = false;
    fetchPublishedInstitutions()
      .then((institutions) => {
        if (cancelled) return;
        const names = {};
        for (const inst of institutions || []) {
          if (inst?.id) names[inst.id] = inst.name || inst.id;
        }
        setInstitutionNames(names);
      })
      .catch(() => {
        // Silent fallback -- RecordReviewRow renders the raw institution_id
        // when a name isn't found, so a failed fetch here is never fatal.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="screen">
      <header className="topbar">
        <h1>Curator Review</h1>
        <div className="topbar__user">
          <span className="muted">{user.email}</span>
          <button className="btn btn--ghost" onClick={onLogOut}>Sign out</button>
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={`tab ${tab === t ? "tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>

      {tab === "Published" && <PublishedTab institutionNames={institutionNames} />}
      {tab === "Needs Review" && <ReviewQueue institutionNames={institutionNames} />}
      {tab === "Institutions" && <InstitutionsManager />}
      {tab === "Pipeline" && <PipelineControl />}
      {tab === "Settings" && <ReviewSettings />}
    </div>
  );
}
