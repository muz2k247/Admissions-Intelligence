import DegreeLevelBadge from "./DegreeLevelBadge";
import { ExternalLinkIcon } from "./Icons";

const SAFE_URL_SCHEMES = ["http:", "https:"];

function isSafeUrl(url) {
  try {
    return SAFE_URL_SCHEMES.includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

/* Field-level confidence scores are an editorial/admin signal (see the
 * admin CMS's FieldEditor), not something the public dashboard shows -- a
 * regular visitor sees the extracted value or nothing, never a confidence
 * tier. When a field is null, that's communicated by simply showing no
 * value rather than a "Not stated" badge, so the public UI carries no
 * confidence-related indicator at all. */
function FieldRow({ label, field }) {
  const isLabeledList = Array.isArray(field?.value);
  return (
    <div className="record-card__field">
      <span className="record-card__field-label">{label}</span>
      {isLabeledList ? (
        <ul className="record-card__field-list">
          {field.value.map((entry, i) => (
            <li key={i} className="record-card__field-value">
              <span className="record-card__field-list-label">{entry.label}:</span> {entry.date}
            </li>
          ))}
        </ul>
      ) : field?.value != null ? (
        <span className="record-card__field-value">{field.value}</span>
      ) : (
        <span className="record-card__field-empty">Not stated</span>
      )}
    </div>
  );
}

function ProgramsFieldRow({ field }) {
  const programs = Array.isArray(field?.value) ? field.value : null;
  return (
    <FieldRow
      label="Programs"
      field={programs && programs.length > 0 ? { value: programs.join(", ") } : { value: null }}
    />
  );
}

export default function RecordCard({ record, institutionName, isAdmittingBody }) {
  const sourceUrlSafe = isSafeUrl(record.source_url);

  return (
    <article className="record-card">
      <header className="record-card__header">
        <div>
          <h2 className="record-card__title">{institutionName ?? record.institution_id}</h2>
          {record.campus && <p className="record-card__campus">{record.campus}</p>}
        </div>
        <DegreeLevelBadge degreeLevel={record.degree_level} />
      </header>

      <div className="record-card__fields">
        <FieldRow label="Deadline" field={record.deadline} />
        <ProgramsFieldRow field={record.programs} />
        {isAdmittingBody && <FieldRow label="Constituent college" field={record.constituent_college} />}
      </div>

      <footer className="record-card__footer">
        {sourceUrlSafe ? (
          <a
            className="record-card__source-link"
            href={record.source_url}
            target="_blank"
            rel="noreferrer noopener"
          >
            View source
            <ExternalLinkIcon />
          </a>
        ) : (
          <span className="record-card__field-empty">Source link unavailable</span>
        )}
        <span className="record-card__fetched-at">
          Fetched {new Date(record.fetched_at).toLocaleString()}
        </span>
      </footer>
    </article>
  );
}
