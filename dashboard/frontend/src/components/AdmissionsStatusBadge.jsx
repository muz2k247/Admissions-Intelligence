import { CheckCircleIcon, XCircleIcon } from "./Icons";

/* admissions_open is its own classified signal (never derived from the
 * deadline field -- see extraction/fields.py's extract_admissions_open). A
 * null value means the page didn't state either way, which reads as no
 * badge at all here -- silence must never render as "Closed" (hard rule 1). */
export default function AdmissionsStatusBadge({ admissionsOpen }) {
  const value = admissionsOpen?.value;
  if (value === "Open") {
    return (
      <span className="badge badge--open">
        <CheckCircleIcon />
        Admissions Open
      </span>
    );
  }
  if (value === "Closed") {
    return (
      <span className="badge badge--closed">
        <XCircleIcon />
        Admissions Closed
      </span>
    );
  }
  return null;
}
