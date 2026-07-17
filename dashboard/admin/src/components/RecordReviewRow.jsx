import { useState } from "react";
import { OVERRIDABLE_FIELDS, saveFieldOverride } from "../api/overrides";

const FIELD_LABELS = {
  deadline: "Deadline",
  programs: "Programs",
  constituent_college: "Constituent college",
  admissions_open: "Admissions status",
};

/* extraction/chunker.py::_pdf_chunk_id always ends a PDF chunk_id in
 * "__pdf_" + (optional path slug + "_") + a 10-char lowercase-hex digest --
 * matched here anchored at the string's end, not a loose substring check,
 * so a campus whose slug happens to contain "pdf_" (e.g. "PDF Campus" ->
 * "pdf_campus") never gets its ordinary page chunk mislabeled as a PDF. */
const PDF_CHUNK_ID_SUFFIX = /__pdf_[a-z0-9_]*[0-9a-f]{10}$/;

/* A short, human-readable tag for which chunk within an institution/campus
 * this record came from -- disambiguates duplicate-looking cards once the
 * pipeline-side dedup (Phase T Task 5.1) still leaves multiple distinct
 * records for the same institution (e.g. a genuinely different PDF notice).
 * Not a guess at the chunk's content, just a read of its own id shape. */
function chunkDiscriminator(chunkId, institutionId) {
  if (!chunkId) return null;
  if (PDF_CHUNK_ID_SUFFIX.test(chunkId)) return "PDF notice";
  if (chunkId === institutionId || chunkId.startsWith(`${institutionId}__`)) return "Page";
  return chunkId;
}

function sourceDomain(sourceUrl) {
  try {
    return new URL(sourceUrl).hostname;
  } catch {
    return null;
  }
}

function formattedFetchedAt(fetchedAt) {
  if (!fetchedAt) return null;
  const date = new Date(fetchedAt);
  return Number.isNaN(date.getTime()) ? fetchedAt : date.toLocaleDateString();
}

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

/* Confidence is an editorial signal for curators deciding whether a field
 * needs a correction -- it's deliberately not shown on the public dashboard
 * (see dashboard/frontend/src/components/RecordCard.jsx), so this is its
 * only UI surface. Not stated gets its own neutral chip rather than being
 * lumped into "low confidence" -- those are different situations for a
 * curator (nothing extracted vs. an unreliable extraction). Mirrors
 * displayValue's null/empty-array check above so the chip and the value
 * text never disagree about whether something was extracted. */
function confidenceChip(published) {
  const { value, confidence } = published;
  const isNotStated = value == null || (Array.isArray(value) && value.length === 0);
  if (isNotStated || typeof confidence !== "number") {
    return { className: "chip--confidence-none", label: "Not stated" };
  }
  if (confidence >= 0.8) return { className: "chip--confidence-high", label: `High confidence (${Math.round(confidence * 100)}%)` };
  if (confidence >= 0.5) return { className: "chip--confidence-medium", label: `Medium confidence (${Math.round(confidence * 100)}%)` };
  return { className: "chip--confidence-low", label: `Low confidence (${Math.round(confidence * 100)}%)` };
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

function FieldEditor({ record, fieldName, onFieldSaved }) {
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
  const chip = isVerified ? null : confidenceChip(published);

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
    // admissions_open is a controlled vocabulary -- AdmissionsStatusBadge on
    // the public dashboard only recognizes the literal strings "Open"/
    // "Closed" and silently renders nothing for anything else, so a stray
    // value here would look "saved" to the curator but never actually show
    // up. The <select> below already constrains input to these two values,
    // this is a defense-in-depth check in case that ever changes.
    if (fieldName === "admissions_open" && value !== "Open" && value !== "Closed") {
      setStatus('Admissions status must be exactly "Open" or "Closed".');
      return;
    }
    try {
      await saveFieldOverride(record.chunk_id, record, fieldName, value);
      setSavedValue(value);
      setStatus("saved");
      setEditing(false);
      onFieldSaved?.(fieldName, value);
    } catch (e) {
      setStatus(e?.message || "Save failed.");
    }
  }

  return (
    <div className="field">
      <div className="field__head">
        <span className="field__label">{FIELD_LABELS[fieldName]}</span>
        {isVerified ? (
          <span className="chip chip--verified">✔ Verified</span>
        ) : (
          <span className={`chip ${chip.className}`}>{chip.label}</span>
        )}
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
          {fieldName === "admissions_open" ? (
            <select
              className="input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label={`Corrected ${FIELD_LABELS[fieldName]}`}
            >
              <option value="" disabled>
                Select a status
              </option>
              <option value="Open">Open</option>
              <option value="Closed">Closed</option>
            </select>
          ) : (
            <input
              className="input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={fieldName === "programs" ? "BS CS, BE EE, …" : "Corrected value"}
              aria-label={`Corrected ${FIELD_LABELS[fieldName]}`}
            />
          )}
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

export default function RecordReviewRow({ record, institutionNames, onFieldSaved }) {
  // Graceful fallback to the raw institution_id: institutionNames is only
  // populated once ReviewScreen's institutions.json fetch resolves (and
  // never populated at all if that fetch fails), so this must never assume
  // a lookup hit.
  const institutionName = institutionNames?.[record.institution_id] || record.institution_id;
  const discriminator = chunkDiscriminator(record.chunk_id, record.institution_id);
  const domain = sourceDomain(record.source_url);
  const fetchedAt = formattedFetchedAt(record.fetched_at);
  const metaParts = [fetchedAt, discriminator, domain].filter(Boolean);

  return (
    <section className="card record">
      <header className="record__head">
        <h2>{institutionName}{record.campus ? ` — ${record.campus}` : ""}</h2>
        <a className="record__src" href={record.source_url} target="_blank" rel="noopener noreferrer">
          source ↗
        </a>
      </header>
      {metaParts.length > 0 && <p className="record__meta muted">{metaParts.join(" · ")}</p>}
      <div className="record__fields">
        {OVERRIDABLE_FIELDS.map((fieldName) => (
          <FieldEditor key={fieldName} record={record} fieldName={fieldName} onFieldSaved={onFieldSaved} />
        ))}
      </div>
    </section>
  );
}
