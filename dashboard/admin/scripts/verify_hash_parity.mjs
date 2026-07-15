// Cross-language parity check: extraction/review_gate.py::content_hash()
// (Python) and src/lib/contentHash.js::contentHash() (this app, JS) must
// produce byte-identical digests for the same record, since a curator's
// approve/reject decision written by this app is later verified against the
// pipeline's own recomputed hash. There's no JS test framework in this repo
// (see dashboard/admin/package.json) and adding one just for a single
// parity check isn't worth the tooling weight, so this is a plain, manually
// runnable Node script instead of a committed test suite entry.
//
// Run from dashboard/admin/: `node scripts/verify_hash_parity.mjs`
//
// Each expected digest below was computed by actually running Python's
// hashlib/json against the matching fixture (see the comment on each case),
// and several mirror specific cases tests/test_review_gate.py covers on the
// Python side -- if this script's output ever stops matching, the two
// implementations have drifted apart and any content_hash comparison in
// production (pipeline/run_full.py's decision-matching logic) would
// silently break.

import { contentHash } from "../src/lib/contentHash.js";

const CASES = [
  {
    name: "plain fixture (scalar deadline, programs list, admissions_open)",
    record: {
      deadline: { value: "2026-08-15", confidence: 0.6 },
      programs: { value: ["BS CS", "BS EE"], confidence: 0.9 },
      constituent_college: { value: null, confidence: null },
      admissions_open: { value: "Open", confidence: 0.8 },
    },
    // Matches tests/test_review_gate.py::test_hash_matches_known_fixture_value
    expected: "a8ec6b836d0b93c3bdde741264daed6e660f57b8eb668cd02e0a91903315af4b",
  },
  {
    name: "non-ASCII constituent_college",
    record: {
      deadline: { value: null, confidence: null },
      programs: { value: null, confidence: null },
      constituent_college: { value: "Allāma Iqbal Medical College", confidence: 0.6 },
      admissions_open: { value: null, confidence: null },
    },
    // Matches tests/test_review_gate.py::test_hash_does_not_escape_non_ascii_characters
    expected: "2accb0a5e6964e0d7257bf4f15d25fa34479ecb497f287a42a5882197c50add3",
  },
  {
    name: "multi-entry deadline (list of {label, date})",
    record: {
      deadline: {
        value: [
          { label: "Engineering", date: "2026-08-15" },
          { label: "CS", date: "2026-08-20" },
        ],
        confidence: 0.9,
      },
      programs: { value: null, confidence: null },
      constituent_college: { value: null, confidence: null },
      admissions_open: { value: null, confidence: null },
    },
    // Matches the shape covered by
    // tests/test_review_gate.py::test_hash_handles_multi_entry_deadline_shape
    // -- computed directly via Python's hashlib/json for this exact fixture.
    expected: "f7f1f1683935604d6c57640a11e7f84f7478e537fc2d4bbf0f1694b32458ec73",
  },
  {
    name: "empty-list programs (distinct from null)",
    record: {
      deadline: { value: null, confidence: null },
      programs: { value: [], confidence: 0.9 },
      constituent_college: { value: null, confidence: null },
      admissions_open: { value: null, confidence: null },
    },
    // Matches the shape covered by
    // tests/test_review_gate.py::test_hash_distinguishes_empty_list_from_null
    // -- computed directly via Python's hashlib/json for this exact fixture.
    expected: "2402cd63512cb58ab7d76be6f5306300aee92ab69c0b44168fe0f89ebb099ca9",
  },
];

async function main() {
  let failed = false;
  for (const { name, record, expected } of CASES) {
    const actual = await contentHash(record);
    if (actual !== expected) {
      console.error(`FAIL (${name}):\n  expected: ${expected}\n  actual:   ${actual}`);
      failed = true;
      continue;
    }
    console.log(`PASS (${name}): ${actual}`);
  }
  if (failed) process.exitCode = 1;
}

main();
