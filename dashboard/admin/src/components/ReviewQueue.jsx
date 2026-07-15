import { useEffect, useState } from "react";
import { fetchNeedsReviewRecords, submitReviewDecision } from "../api/review";
import { applyFieldEdits } from "../lib/reviewRecord";
import RecordReviewRow from "./RecordReviewRow";

const FIELD_LABELS = {
  deadline: "Deadline",
  programs: "Programs",
  constituent_college: "Constituent college",
  admissions_open: "Admissions status",
};

function QueueItem({ record, onDecided }) {
  const [status, setStatus] = useState(null); // null | "saving" | error string
  // Fields the curator has corrected THIS session, via RecordReviewRow's
  // FieldEditor -- kept here (not just inside FieldEditor's own local state)
  // so decide() can hash the record as it will exist AFTER stage_5_publish
  // merges the same override, not the stale pre-edit values. Without this,
  // approving right after an edit hashes values the pipeline will never
  // recompute, so the decision looks stale and the record silently re-queues.
  const [edits, setEdits] = useState({});

  async function decide(decision) {
    setStatus("saving");
    try {
      const effectiveRecord = applyFieldEdits(record, edits);
      await submitReviewDecision(record.chunk_id, effectiveRecord, decision);
      onDecided(record.chunk_id);
    } catch (e) {
      setStatus(e?.message || "Failed to save decision.");
    }
  }

  return (
    <div className="queue-item">
      {record.flagged_fields?.length > 0 && (
        <p className="queue-item__flagged muted">
          Flagged for review: {record.flagged_fields.map((f) => FIELD_LABELS[f] || f).join(", ")}
        </p>
      )}
      <RecordReviewRow
        record={record}
        onFieldSaved={(fieldName, value) => setEdits((prev) => ({ ...prev, [fieldName]: value }))}
      />
      <div className="queue-item__actions">
        <button className="btn btn--primary" onClick={() => decide("approved")} disabled={status === "saving"}>
          {status === "saving" ? "Saving…" : "Approve"}
        </button>
        <button className="btn btn--ghost" onClick={() => decide("rejected")} disabled={status === "saving"}>
          Reject
        </button>
      </div>
      {status && status !== "saving" && (
        <span className="field__status err" role="alert">{status}</span>
      )}
    </div>
  );
}

export default function ReviewQueue() {
  const [records, setRecords] = useState(null);
  const [error, setError] = useState(null);
  // Optimistic local hide of decided chunk_ids -- a decision only takes
  // effect in the published data on the NEXT pipeline run, so this just
  // keeps the queue from looking stale for the rest of this session
  // (mirrors RecordReviewRow's FieldEditor optimistic-save pattern).
  const [decided, setDecided] = useState(() => new Set());

  useEffect(() => {
    let cancelled = false;
    fetchNeedsReviewRecords()
      .then((data) => {
        if (!cancelled) setRecords(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Could not load the needs-review queue.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function handleDecided(chunkId) {
    setDecided((prev) => new Set(prev).add(chunkId));
  }

  const pending = records?.filter((r) => !decided.has(r.chunk_id)) ?? null;

  return (
    <div>
      {error && <p className="error" role="alert">{error}</p>}
      {!error && records === null && <p className="muted">Loading needs-review queue…</p>}
      {pending !== null && pending.length === 0 && (
        <p className="muted">Nothing pending review.</p>
      )}
      <div className="records">
        {pending?.map((record) => (
          <QueueItem key={record.chunk_id} record={record} onDecided={handleDecided} />
        ))}
      </div>
    </div>
  );
}
