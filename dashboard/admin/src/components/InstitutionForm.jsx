import { useState } from "react";
import { VALID_FORMATS, VALID_RENDER_MODES } from "../api/institutions";

let _sourceKeySeq = 0;
function _newSourceRow(source) {
  return {
    _key: `source-${++_sourceKeySeq}`,
    campus: source?.campus ?? "",
    url: source?.url ?? "",
    format: source?.format ?? "html",
    render: source?.render ?? "static",
  };
}

function SourceRow({ source, onChange, onRemove, canRemove }) {
  return (
    <div className="source-row">
      <input
        className="input"
        placeholder="Campus (leave blank if none)"
        value={source.campus}
        onChange={(e) => onChange({ ...source, campus: e.target.value })}
        aria-label="Campus"
      />
      <input
        className="input"
        placeholder="https://admissions.example.edu.pk"
        value={source.url}
        onChange={(e) => onChange({ ...source, url: e.target.value })}
        aria-label="Admissions URL"
      />
      <select
        className="input"
        value={source.format}
        onChange={(e) => onChange({ ...source, format: e.target.value })}
        aria-label="Format"
      >
        {VALID_FORMATS.map((f) => (
          <option key={f} value={f}>{f}</option>
        ))}
      </select>
      <select
        className="input"
        value={source.render}
        onChange={(e) => onChange({ ...source, render: e.target.value })}
        aria-label="Render mode"
      >
        {VALID_RENDER_MODES.map((r) => (
          <option key={r} value={r}>{r}</option>
        ))}
      </select>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={onRemove}
        disabled={!canRemove}
        title={canRemove ? "Remove this source" : "An institution needs at least one source"}
      >
        Remove
      </button>
    </div>
  );
}

/* Shared add/edit form for one institution -- reused by InstitutionsManager
 * (edit an existing institution) and the "Add institution" flow. Always
 * round-trips the FULL institution shape on save (see api/institutions.js's
 * saveInstitution docstring for why partial patches are never safe here). */
export default function InstitutionForm({ institutionId, initial, onCancel, onSave, saving, error }) {
  const [name, setName] = useState(initial?.name ?? "");
  const [admittingBody, setAdmittingBody] = useState(initial?.admitting_body ?? false);
  const [ugPgMixed, setUgPgMixed] = useState(initial?.ug_pg_mixed ?? false);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [sources, setSources] = useState(() =>
    initial?.sources?.length ? initial.sources.map(_newSourceRow) : [_newSourceRow()]
  );
  const [validationError, setValidationError] = useState(null);

  function updateSource(index, next) {
    setSources((prev) => prev.map((s, i) => (i === index ? next : s)));
  }

  function removeSource(index) {
    setSources((prev) => prev.filter((_, i) => i !== index));
  }

  function addSource() {
    setSources((prev) => [...prev, _newSourceRow()]);
  }

  function handleSubmit(e) {
    e.preventDefault();
    setValidationError(null);

    const trimmedName = name.trim();
    if (!trimmedName) {
      setValidationError("Name can't be empty.");
      return;
    }
    if (sources.length === 0) {
      setValidationError("An institution needs at least one source.");
      return;
    }
    const cleanedSources = [];
    for (const s of sources) {
      const url = s.url.trim();
      if (!url) {
        setValidationError("Every source needs a URL.");
        return;
      }
      cleanedSources.push({ campus: s.campus.trim() || null, url, format: s.format, render: s.render });
    }

    onSave({
      name: trimmedName,
      admitting_body: admittingBody,
      ug_pg_mixed: ugPgMixed,
      enabled,
      sources: cleanedSources,
    });
  }

  return (
    <form className="institution-form" onSubmit={handleSubmit}>
      <label className="settings-panel__row settings-panel__row--column">
        Name
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} aria-label="Institution name" />
      </label>

      {institutionId && (
        <p className="muted institution-form__id">id: {institutionId}</p>
      )}

      <label className="settings-panel__row">
        <input type="checkbox" checked={admittingBody} onChange={(e) => setAdmittingBody(e.target.checked)} />
        Admitting body (admits on behalf of named constituent colleges)
      </label>
      <label className="settings-panel__row">
        <input type="checkbox" checked={ugPgMixed} onChange={(e) => setUgPgMixed(e.target.checked)} />
        UG/PG mixed on the same pages
      </label>
      <label className="settings-panel__row">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enabled (scraped on the next pipeline run)
      </label>

      <div className="institution-form__sources">
        <p className="muted">Sources</p>
        {sources.map((source, i) => (
          <SourceRow
            key={source._key}
            source={source}
            onChange={(next) => updateSource(i, next)}
            onRemove={() => removeSource(i)}
            canRemove={sources.length > 1}
          />
        ))}
        <button type="button" className="btn btn--ghost btn--sm" onClick={addSource}>
          + Add source
        </button>
      </div>

      {validationError && <p className="error" role="alert">{validationError}</p>}
      {error && <p className="error" role="alert">{error}</p>}

      <div className="institution-form__actions">
        <button type="submit" className="btn btn--primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn btn--ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}
