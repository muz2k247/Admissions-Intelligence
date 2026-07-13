import { useState } from "react";
import { OVERRIDABLE_FIELDS, saveFieldOverride } from "../api/overrides";

const FIELD_LABELS = {
  deadline: "Deadline",
  fee: "Fee",
  programs: "Programs",
  constituent_college: "Constituent college",
};

/* Render a published field's value for display. Handles the three shapes the
 * pipeline can produce: a scalar string, a list of program strings, and a
 * list of {label, date} objects (multi-deadline). */
function displayValue(value) {
  if (value === null || value === undefined) return "Not stated";
  if (Array.isArray(value)) {
    if (value.length === 0) return "Not stated";
    if (typeof value[0] === "object" && value[0] !== null) {
      return value.map((v) => `${v.label}: ${v.date}`).join("; ");
    }
    return value.join(", ");
  }
  return String(value);
}

/* Pre-fill text for the editor input. A multi-deadline (array of objects)
 * can't be round-tripped through a single text box, so it starts blank -- the
 * curator types the single corrected value they want. */
function toInputText(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) {
    if (value.length && typeof value[0] === "object") return "";
    return value.join(", ");
  }
  return String(value);
}

function FieldEditor({ record, fieldName }) {
  const published = record[fieldName] || {};

  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(toInputText(published.value));
  const [status, setStatus] = useState(null); // null | "saving" | "saved" | error string
  // Optimistic local copy of a just-saved correction, so the row reflects it
  // immediately rather than looking unchanged until the next page load /
  // pipeline publish. null until this curator saves a correction here.
  const [savedValue, setSavedValue] = useState(null);

  const effectiveValue = savedValue !== null ? savedValue : published.value;
  const isVerified = savedValue !== null || published.note === "human-verified";

  async function handleSave() {
    setStatus("saving");
    const trimmed = text.trim();
    // programs is a list field; everything else is a scalar string.
    const value =
      fieldName === "programs"
        ? trimmed.split(",").map((s) => s.trim()).filter(Boolean)
        : trimmed;
    if (fieldName === "programs" ? value.length === 0 : value === "") {
      setStatus("A correction can't be empty. Type a value or leave the field as-is.");
      return;
    }
    try {
      await saveFieldOverride(record.chunk_id, record, fieldName, value);
      setSavedValue(value);
      setStatus("saved");
      setEditing(false);
    } catch (e) {
      setStatus(e?.message || "Save failed.");
    }
  }

  return (
    <div className="field">
      <div className="field__head">
        <span className="field__label">{FIELD_LABELS[fieldName]}</span>
        {isVerified && <span className="chip chip--verified">✔ Verified</span>}
      </div>
      <div className="field__value">{displayValue(effectiveValue)}</div>
      {fieldName === "constituent_college" && (
        <small className="hint">
          Use the exact college name as it appears on the source page / registry —
          this is stored as-is, not matched against a list.
        </small>
      )}

      {!editing && (
        <button className="btn btn--ghost btn--sm" onClick={() => { setEditing(true); setStatus(null); }}>
          Correct
        </button>
      )}

      {editing && (
        <div className="editor">
          <input
            className="input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={fieldName === "programs" ? "BS CS, BE EE, …" : "Corrected value"}
            aria-label={`Corrected ${FIELD_LABELS[fieldName]}`}
          />
          <div className="editor__actions">
            <button className="btn btn--primary btn--sm" onClick={handleSave} disabled={status === "saving"}>
              {status === "saving" ? "Saving…" : "Save"}
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => { setEditing(false); setStatus(null); }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {status === "saved" && <span className="field__status ok">Saved — live after next publish.</span>}
      {status && status !== "saved" && status !== "saving" && (
        <span className="field__status err" role="alert">{status}</span>
      )}
    </div>
  );
}

export default function RecordReviewRow({ record }) {
  return (
    <section className="card record">
      <header className="record__head">
        <h2>{record.institution_id}{record.campus ? ` — ${record.campus}` : ""}</h2>
        <a className="record__src" href={record.source_url} target="_blank" rel="noopener noreferrer">
          source ↗
        </a>
      </header>
      <div className="record__fields">
        {OVERRIDABLE_FIELDS.map((fieldName) => (
          <FieldEditor key={fieldName} record={record} fieldName={fieldName} />
        ))}
      </div>
    </section>
  );
}
