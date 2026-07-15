import { useEffect, useState } from "react";
import {
  deleteInstitution,
  fetchLiveInstitutionTombstones,
  fetchPublishedInstitutions,
  saveInstitution,
} from "../api/institutions";
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
  const [adding, setAdding] = useState(false);
  const [existingIds, setExistingIds] = useState(null); // null until openAddForm() resolves
  const [checkingIds, setCheckingIds] = useState(false);
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

  /* Live-check existing ids before showing the add form -- institutions.json
   * (fetchPublishedInstitutions, what `institutions` state holds) only
   * reflects Firestore as of the LAST pipeline publish, up to a week stale
   * per .github/workflows/pipeline.yml's cron. Combining the published list
   * with a fresh read of the whole Firestore collection closes that gap: an
   * id counts as taken if it's in the published list AND not tombstoned
   * live, or if it's a live non-tombstoned Firestore doc not yet published.
   * A tombstoned id is deliberately left OUT (reusable) -- see
   * fetchLiveInstitutionTombstones's docstring. This still leaves a much
   * smaller, accepted race between this check and the eventual save (two
   * curators clicking "Add" within the same short window), matching this
   * codebase's existing "not transactional, fine at 1-2 curator scale"
   * precedent (api/overrides.js, saveInstitution/deleteInstitution here). */
  async function openAddForm() {
    setCheckingIds(true);
    setSaveError(null);
    try {
      const tombstones = await fetchLiveInstitutionTombstones();
      const ids = new Set();
      for (const inst of institutions) {
        if (tombstones.get(inst.id) !== true) ids.add(inst.id);
      }
      for (const [id, deleted] of tombstones) {
        if (!deleted) ids.add(id);
      }
      setExistingIds(ids);
      setEditingId(null);
      setAdding(true);
    } catch (e) {
      setError(e?.message || "Could not check existing institution ids.");
    } finally {
      setCheckingIds(false);
    }
  }

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
      setInstitutions((prev) => prev.map((i) => (i.id === editingId ? institution : i)));
    } catch (e) {
      setSaveError(e?.message || "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleCreate(institution) {
    setSaving(true);
    setSaveError(null);
    try {
      await saveInstitution(institution.id, institution);
      setAdding(false);
      // Optimistic local add -- takes effect in institutions.json (and
      // actual scraping) on the next pipeline run, same caveat as edits.
      setInstitutions((prev) => [...prev, institution]);
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
      {!editingInstitution && !adding && (
        <div className="queue-item__actions">
          <button className="btn btn--primary" onClick={openAddForm} disabled={checkingIds}>
            {checkingIds ? "Checking…" : "+ Add institution"}
          </button>
        </div>
      )}

      {adding && (
        <section className="card">
          <h2>Add institution</h2>
          <p className="muted">
            Enter the URL manually below. (Gemini/web-search URL suggestion is a
            planned follow-up, not built yet -- see CLAUDE.md's Phase R notes.)
          </p>
          <InstitutionForm
            institutionId={null}
            existingIds={[...existingIds]}
            saving={saving}
            error={saveError}
            onSave={handleCreate}
            onCancel={() => {
              setAdding(false);
              setSaveError(null);
            }}
          />
        </section>
      )}

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
              setAdding(false);
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
