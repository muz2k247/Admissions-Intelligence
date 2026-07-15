// Merge a curator's locally-saved field edits into a needs-review record,
// producing the record an approve/reject decision should be hashed against.
//
// FieldEditor (components/RecordReviewRow.jsx) writes an edited value
// straight to the `overrides` collection (api/overrides.js::saveFieldOverride)
// but has no way to mutate the record object ReviewQueue holds. Meanwhile
// pipeline/run_full.py::stage_5_publish merges that same override into the
// record BEFORE computing extraction/review_gate.py::content_hash for the
// gate comparison -- so a decision hashed against the original, un-edited
// record can never match the pipeline's post-merge hash, and an "edit then
// approve" record would silently stay queued forever. This merges pending
// local edits in first so contentHash() sees what the pipeline will see.
import { REVIEW_FIELDS } from "./contentHash.js";

export function applyFieldEdits(record, edits) {
  if (!edits || Object.keys(edits).length === 0) return record;
  const merged = { ...record };
  for (const name of REVIEW_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(edits, name)) {
      merged[name] = { ...record[name], value: edits[name] };
    }
  }
  return merged;
}
