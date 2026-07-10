export default function FilterBar({
  institutions,
  institutionId,
  onInstitutionChange,
  degreeLevel,
  onDegreeLevelChange,
}) {
  return (
    <form className="filter-bar" role="search" aria-label="Filter admission records">
      <div className="filter-bar__field">
        <label htmlFor="filter-institution">Institution</label>
        <select
          id="filter-institution"
          value={institutionId}
          onChange={(e) => onInstitutionChange(e.target.value)}
        >
          <option value="">All institutions</option>
          {institutions.map((inst) => (
            <option key={inst.id} value={inst.id}>
              {inst.name}
            </option>
          ))}
        </select>
      </div>

      <div className="filter-bar__field">
        <label htmlFor="filter-degree-level">Degree level</label>
        <select
          id="filter-degree-level"
          value={degreeLevel}
          onChange={(e) => onDegreeLevelChange(e.target.value)}
        >
          <option value="Undergraduate">Undergraduate</option>
          <option value="Ambiguous">Needs review (Ambiguous)</option>
        </select>
      </div>
    </form>
  );
}
