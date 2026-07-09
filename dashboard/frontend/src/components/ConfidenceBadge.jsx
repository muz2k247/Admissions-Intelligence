import { AlertIcon, CheckIcon, DashIcon } from "./Icons";

/* Field-level confidence display (CLAUDE.md hard rule 2: field-level, not
 * record-level). Every tier pairs an icon with text — never color alone
 * (ui-ux-pro-max: color-not-only). */
export default function ConfidenceBadge({ field }) {
  if (!field || field.value === null || field.value === undefined) {
    return (
      <span className="badge badge--neutral">
        <DashIcon />
        Not stated
      </span>
    );
  }

  const { confidence } = field;
  if (confidence >= 0.8) {
    return (
      <span className="badge badge--success" title={field.note ?? undefined}>
        <CheckIcon />
        High confidence
      </span>
    );
  }
  if (confidence >= 0.5) {
    return (
      <span className="badge badge--warning" title={field.note ?? undefined}>
        <AlertIcon />
        Medium confidence
      </span>
    );
  }
  return (
    <span className="badge badge--danger" title={field.note ?? undefined}>
      <AlertIcon />
      Low confidence
    </span>
  );
}
