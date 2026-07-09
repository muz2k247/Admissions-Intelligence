import ConfidenceBadge from "./ConfidenceBadge";
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

function FieldRow({ label, field }) {
  return (
    <div className="record-card__field">
      <span className="record-card__field-label">{label}</span>
      <span className="record-card__field-value">
        {field?.value ?? <span className="record-card__field-empty">Not stated</span>}
      </span>
      <ConfidenceBadge field={field} />
    </div>
  );
}

export default function RecordCard({ record, institutionName, isAdmittingBody }) {
  const programs = Array.isArray(record.programs?.value) ? record.programs.value : null;
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
        <FieldRow label="Fee" field={record.fee} />
        <div className="record-card__field">
          <span className="record-card__field-label">Programs</span>
          <span className="record-card__field-value">
            {programs && programs.length > 0 ? (
              programs.join(", ")
            ) : (
              <span className="record-card__field-empty">Not stated</span>
            )}
          </span>
          <ConfidenceBadge field={record.programs} />
        </div>
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
