import { useEffect, useState } from "react";
import { deleteInstitution, fetchPublishedInstitutions, saveInstitution } from "../api/institutions";
import InstitutionForm from "./InstitutionForm";

// Note: `constituent_colleges` (per-source, admitting-body institutions like
// UHS/NUMS -- see CLAUDE.md's institution registry schema) isn't editable
// here. scraper/config.py's Source dataclass has never parsed that field
// from the YAML registry (a pre-existing gap predating Phase R, not
// introduced by it), so it isn't in the published institutions.json this
// tab reads from either. Out of scope for this cut; flagged for whoever
// picks up constituent-college editing next.

function InstitutionRow({ institution, onEdit, onDelete, deleting }) {
  return (
    <div className="card institution-row">
      <header className="record__head">
        <h2>
          {institution.name}
          {!institution.enabled && <span className="chip chip--confidence-none"> Disabled</span>}
        </h2>
        <span className="muted">{institution.id}</span>
      </header>
      <ul className="institution-row__sources">
        {institution.sources?.map((s, i) => (
          <li key={i}>
            {s.campus ? `${s.campus}: ` : ""}
            <a href={s.url} target="_blank" rel="noopener noreferrer">{s.url}</a>
            {" "}
            <span className="muted">({s.format}{s.render === "js" ? ", js-rendered" : ""})</span>
          </li>
        ))}
      </ul>
      <div className="queue-item__actions">
        <button className="btn btn--ghost btn--sm" onClick={() => onEdit(institution)}>Edit</button>
        <button
          className="btn btn--ghost btn--sm"
          onClick={() => onDelete(institution)}
          disabled={deleting}
        >
          {deleting ? "Removing…" : "Remove"}
        </button>
      </div>
    </div>
  );
}

export default function InstitutionsManager() {
  const [institutions, setInstitutions] = useState(null);
  const [error, setError] = useState(null);
  const [editingId, setEditingId] = useState(null); // null | institutionId
  const [saveError, setSaveError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);

  function load() {
    setError(null);
    fetchPublishedInstitutions()
      .then(setInstitutions)
      .catch((e) => setError(e?.message || "Could not load institutions."));
  }

  useEffect(() => {
    load();
  }, []);

  const editingInstitution = institutions?.find((i) => i.id === editingId) ?? null;

  async function handleSave(institution) {
    setSaving(true);
    setSaveError(null);
    try {
      await saveInstitution(editingId, institution);
      setEditingId(null);
      // Optimistic local update -- the write only takes effect in
      // institutions.json on the NEXT pipeline run (same "stale until next
      // publish" caveat already documented for the review queue/overrides),
      // so reflect it locally for the rest of this session.
      setInstitutions((prev) =>
        prev.map((i) => (i.id === editingId ? { id: editingId, ...institution } : i))
      );
    } catch (e) {
      setSaveError(e?.message || "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(institution) {
    const confirmed = window.confirm(
      `Remove ${institution.name}? This stops it from being scraped on the next pipeline run.`
    );
    if (!confirmed) return;

    setDeletingId(institution.id);
    try {
      await deleteInstitution(institution.id);
      setInstitutions((prev) => prev.filter((i) => i.id !== institution.id));
    } catch (e) {
      setError(e?.message || "Delete failed.");
    } finally {
      setDeletingId(null);
    }
  }

  if (error) return <p className="error" role="alert">{error}</p>;
  if (institutions === null) return <p className="muted">Loading institutions…</p>;

  return (
    <div>
      {editingInstitution && (
        <section className="card">
          <h2>Edit {editingInstitution.name}</h2>
          <InstitutionForm
            institutionId={editingId}
            initial={editingInstitution}
            saving={saving}
            error={saveError}
            onSave={handleSave}
            onCancel={() => {
              setEditingId(null);
              setSaveError(null);
            }}
          />
        </section>
      )}

      <div className="records">
        {institutions.map((institution) => (
          <InstitutionRow
            key={institution.id}
            institution={institution}
            onEdit={(i) => {
              setEditingId(i.id);
              setSaveError(null);
            }}
            onDelete={handleDelete}
            deleting={deletingId === institution.id}
          />
        ))}
      </div>
    </div>
  );
}
