import { useEffect, useState } from "react";
import { fetchPublishedRecords } from "../api/records";
import RecordReviewRow from "./RecordReviewRow";

export default function ReviewScreen({ user, onLogOut }) {
  const [records, setRecords] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
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

      <p className="muted note">
        Corrections are saved to Firestore and appear on the public dashboard
        after the next pipeline publish — not instantly.
      </p>

      {error && <p className="error" role="alert">{error}</p>}
      {!error && records === null && <p className="muted">Loading records…</p>}
      {records !== null && records.length === 0 && (
        <p className="muted">No published records to review.</p>
      )}

      <div className="records">
        {records?.map((record) => (
          <RecordReviewRow key={record.chunk_id} record={record} />
        ))}
      </div>
    </div>
  );
}
