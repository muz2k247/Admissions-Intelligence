import { useEffect, useState } from "react";
import { fetchPublishedRecords } from "../api/records";
import InstitutionsManager from "./InstitutionsManager";
import PipelineControl from "./PipelineControl";
import RecordReviewRow from "./RecordReviewRow";
import ReviewQueue from "./ReviewQueue";
import ReviewSettings from "./ReviewSettings";

const TABS = ["Published", "Needs Review", "Institutions", "Schedule", "Settings"];

function PublishedTab() {
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
          <RecordReviewRow key={record.chunk_id} record={record} />
        ))}
      </div>
    </>
  );
}

export default function ReviewScreen({ user, onLogOut }) {
  const [tab, setTab] = useState(TABS[0]);

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

      {tab === "Published" && <PublishedTab />}
      {tab === "Needs Review" && <ReviewQueue />}
      {tab === "Institutions" && <InstitutionsManager />}
      {tab === "Schedule" && <PipelineControl />}
      {tab === "Settings" && <ReviewSettings />}
    </div>
  );
}
