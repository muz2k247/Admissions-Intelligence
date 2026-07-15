// Regression check for the "edit then approve" bug: FieldEditor
// (components/RecordReviewRow.jsx) used to save a correction only to its own
// local state, so ReviewQueue's decide() hashed the STALE pre-edit record --
// a decision's content_hash could then never match what stage_5_publish
// computes post-merge_overrides, and the record silently re-queued forever
// even after being "approved". The fix (lib/reviewRecord.js::applyFieldEdits)
// merges locally-saved edits into the record before hashing.
//
// No JS test framework in this repo (see package.json) -- same plain,
// manually runnable Node script pattern as verify_hash_parity.mjs.
//
// Run from dashboard/admin/: `node scripts/verify_edit_then_approve.mjs`

import { contentHash } from "../src/lib/contentHash.js";
import { applyFieldEdits } from "../src/lib/reviewRecord.js";

// A needs-review record flagged on TWO fields (deadline AND programs), the
// scenario that actually exposes the bug: a curator corrects one flagged
// field and wants to approve the record with the other flagged field
// accepted as-is -- the exact case tests/test_needs_review_gate.py::
// test_decision_matching_content_hash_covers_multiple_flagged_fields_atomically
// covers on the pipeline side.
const record = {
  chunk_id: "giki",
  deadline: { value: "10 Aug 2026", confidence: 0.5 },
  programs: { value: ["BS CS"], confidence: 0.4 },
  constituent_college: { value: null, confidence: null },
  admissions_open: { value: null, confidence: null },
};

async function main() {
  let failed = false;

  // The curator corrects `deadline` via FieldEditor -- saveFieldOverride
  // writes {value: "15 Aug 2026", confidence: 1.0} to Firestore, and (after
  // the fix) onFieldSaved reports it up to QueueItem as a local edit.
  const edits = { deadline: "15 Aug 2026" };

  const staleHash = await contentHash(record);
  const effectiveRecord = applyFieldEdits(record, edits);
  const fixedHash = await contentHash(effectiveRecord);

  // 1. The edit must actually change what gets hashed -- if it didn't, the
  // fix would be a no-op and the bug would still reproduce.
  if (fixedHash === staleHash) {
    console.error("FAIL: applying the edit did not change the content hash -- fix is a no-op");
    failed = true;
  } else {
    console.log("PASS: edited record hashes differently from the pre-edit record");
  }

  // 2. The fixed hash must match what the pipeline computes once it merges
  // the SAME correction via merge_overrides (extraction/review_gate.py::
  // content_hash on a record whose deadline.value == edits.deadline and
  // whose programs field is untouched) -- this is the actual value
  // stage_5_publish will compare the submitted decision against. Computed
  // independently via Python's hashlib/json for this exact fixture (deadline
  // "15 Aug 2026", programs ["BS CS"], both other fields null).
  const expectedPostMergeHash = "cc40a7ef2437553f4f3f0eb83ccc6773e52ce37c1d316b5ba4576e4e4ba73229";
  if (fixedHash !== expectedPostMergeHash) {
    console.error(`FAIL: fixed hash does not match the pipeline's post-merge hash\n  expected: ${expectedPostMergeHash}\n  actual:   ${fixedHash}`);
    failed = true;
  } else {
    console.log("PASS: fixed (post-edit) hash matches the pipeline's post-merge_overrides content_hash");
  }

  // 3. Untouched fields must survive the merge unchanged (programs stays the
  // original low-confidence value, not wiped out by editing a different field).
  if (JSON.stringify(effectiveRecord.programs) !== JSON.stringify(record.programs)) {
    console.error("FAIL: editing one field mutated an unrelated field");
    failed = true;
  } else {
    console.log("PASS: unedited fields are preserved by applyFieldEdits");
  }

  // 4. No edits at all (approve-only workflow) must be a true no-op --
  // same object identity, so the existing approve-as-is path is unaffected.
  if (applyFieldEdits(record, {}) !== record) {
    console.error("FAIL: applyFieldEdits with no edits should return the original record unchanged");
    failed = true;
  } else {
    console.log("PASS: no-edit case returns the original record (approve-only workflow unaffected)");
  }

  if (failed) process.exitCode = 1;
}

main();
