import { AlertIcon } from "./Icons";

const REASON_LABELS = {
  "mixed-degree-level": "Mixed UG/PG content",
  "no-signal": "No degree-level signal found",
  "extraction-broken": "Page extraction broke",
  "not-admissions-content": "Not admissions content",
  "conflicting-classification": "Conflicting classification",
};

/* degree_level is only ever set by the content-classifier subagent
 * (CLAUDE.md hard rule 3) — this component just renders what it decided,
 * it never infers UG/PG itself. */
export default function DegreeLevelBadge({ degreeLevel }) {
  if (!degreeLevel || degreeLevel.value === "Undergraduate") {
    return degreeLevel?.value === "Undergraduate" ? (
      <span className="badge badge--ug">Undergraduate</span>
    ) : null;
  }
  if (degreeLevel.value === "Postgraduate") {
    return <span className="badge badge--pg">Postgraduate</span>;
  }
  const reasonLabel = REASON_LABELS[degreeLevel.reason] ?? degreeLevel.reason ?? "Unresolved";
  return (
    <span className="badge badge--neutral" title={reasonLabel}>
      <AlertIcon />
      Ambiguous — {reasonLabel}
    </span>
  );
}
