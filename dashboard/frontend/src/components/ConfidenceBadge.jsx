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

  // A curator-verified correction (admin CMS): note is the exact machine
  // marker "human-verified" (never free text), so it reads as a distinct
  // "Verified" state rather than just a numeric-confidence tier. This is
  // checked before the confidence tiers -- a human sign-off outranks the
  // auto-extraction confidence score.
  if (field.note === "human-verified") {
    return (
      <span className="badge badge--verified" title="Manually verified by a curator">
        <CheckIcon />
        Verified
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
