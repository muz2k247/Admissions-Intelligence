// Manual verification of the three plain helper functions added to
// src/components/RecordReviewRow.jsx in Phase T Task 5.2
// (chunkDiscriminator, sourceDomain, formattedFetchedAt). These have no
// external dependencies beyond the built-in URL/Date globals, so rather than
// pulling them out into an importable module (and touching the component's
// exports for a QA-only need) this script re-implements them verbatim and
// asserts against realistic chunk_id/institution_id/source_url/fetched_at
// inputs shaped per extraction/chunker.py's actual scheme. There's no JS
// test framework in this repo (see dashboard/admin/package.json), matching
// the existing manual-script pattern (scripts/verify_hash_parity.mjs,
// scripts/verify_edit_then_approve.mjs).
//
// Run from dashboard/admin/: `node scripts/verify_record_meta_helpers.mjs`

// --- verbatim copies from RecordReviewRow.jsx ---
const PDF_CHUNK_ID_SUFFIX = /__pdf_[a-z0-9_]*[0-9a-f]{10}$/;

function chunkDiscriminator(chunkId, institutionId) {
  if (!chunkId) return null;
  if (PDF_CHUNK_ID_SUFFIX.test(chunkId)) return "PDF notice";
  if (chunkId === institutionId || chunkId.startsWith(`${institutionId}__`)) return "Page";
  return chunkId;
}

function sourceDomain(sourceUrl) {
  try {
    return new URL(sourceUrl).hostname;
  } catch {
    return null;
  }
}

function formattedFetchedAt(fetchedAt) {
  if (!fetchedAt) return null;
  const date = new Date(fetchedAt);
  return Number.isNaN(date.getTime()) ? fetchedAt : date.toLocaleDateString();
}
// --- end verbatim copies ---

const CASES = [
  // -- chunkDiscriminator: plain HTML page, no campus --
  {
    name: "chunkDiscriminator: base chunk_id, no campus -> Page",
    run: () => chunkDiscriminator("giki", "giki"),
    expected: "Page",
  },
  // -- chunkDiscriminator: campus chunk_id --
  {
    name: "chunkDiscriminator: campus chunk_id (institution__campus_slug) -> Page",
    run: () => chunkDiscriminator("uet__lahore_main", "uet"),
    expected: "Page",
  },
  // -- chunkDiscriminator: real PDF chunk_id shapes --
  {
    name: "chunkDiscriminator: PDF chunk_id with path slug + hash -> PDF notice",
    run: () => chunkDiscriminator("uhs__pdf_merit_list_a1b2c3d4e5", "uhs"),
    expected: "PDF notice",
  },
  {
    name: "chunkDiscriminator: PDF chunk_id with hash only (no path slug) -> PDF notice",
    run: () => chunkDiscriminator("uhs__pdf_a1b2c3d4e5", "uhs"),
    expected: "PDF notice",
  },
  {
    name: "chunkDiscriminator: PDF chunk_id on a campus-scoped base -> PDF notice",
    run: () => chunkDiscriminator("uet__lahore_main__pdf_datesheet_1234567890", "uet"),
    expected: "PDF notice",
  },
  // -- edge case: institution_id itself contains substring "pdf" --
  {
    name: "chunkDiscriminator: institution_id contains 'pdf', no campus, base chunk -> Page (no misfire)",
    run: () => chunkDiscriminator("pdf_college", "pdf_college"),
    expected: "Page",
  },
  {
    name: "chunkDiscriminator: institution_id contains 'pdf', with ordinary campus, base chunk -> Page (no misfire)",
    run: () => chunkDiscriminator("pdf_college__lahore", "pdf_college"),
    expected: "Page",
  },
  {
    // Regression test for a bug the code-reviewer/qa loop caught before this
    // shipped: a campus slug that itself starts with "pdf_" (e.g. campus
    // "PDF Campus" -> slug "pdf_campus") must not make a genuine page-level
    // chunk_id (containing the literal substring "__pdf_" but NOT ending in
    // chunker.py's actual PDF suffix shape -- __pdf_<slug>_<10-hex-hash>)
    // misidentify as a PDF notice. Fixed by anchoring the PDF check on the
    // real suffix shape instead of a loose substring match.
    name: "chunkDiscriminator: campus slug starting with 'pdf_' on a non-PDF chunk -> Page (regression, not a misfire)",
    run: () => chunkDiscriminator("university__pdf_campus", "university"),
    expected: "Page",
  },
  // -- null/undefined chunk_id --
  {
    name: "chunkDiscriminator: null chunk_id -> null, no throw",
    run: () => chunkDiscriminator(null, "giki"),
    expected: null,
  },
  {
    name: "chunkDiscriminator: undefined chunk_id -> null, no throw",
    run: () => chunkDiscriminator(undefined, "giki"),
    expected: null,
  },
  // -- unrecognized chunk_id shape (doesn't match institution_id at all) --
  {
    name: "chunkDiscriminator: chunk_id unrelated to institution_id -> returned verbatim",
    run: () => chunkDiscriminator("some_other_chunk", "giki"),
    expected: "some_other_chunk",
  },

  // -- sourceDomain --
  {
    name: "sourceDomain: valid https:// URL -> hostname",
    run: () => sourceDomain("https://admissions.giki.edu.pk/apply?year=2026"),
    expected: "admissions.giki.edu.pk",
  },
  {
    name: "sourceDomain: malformed URL -> null, no throw",
    run: () => sourceDomain("not-a-url"),
    expected: null,
  },
  {
    name: "sourceDomain: empty string -> null, no throw",
    run: () => sourceDomain(""),
    expected: null,
  },
  {
    name: "sourceDomain: null -> null, no throw",
    run: () => sourceDomain(null),
    expected: null,
  },
  {
    name: "sourceDomain: undefined -> null, no throw",
    run: () => sourceDomain(undefined),
    expected: null,
  },

  // -- formattedFetchedAt --
  {
    name: "formattedFetchedAt: valid ISO string -> formatted date, differs from raw ISO",
    run: () => {
      const raw = "2026-07-16T18:57:46Z";
      const formatted = formattedFetchedAt(raw);
      return formatted !== raw && typeof formatted === "string" ? "DIFFERS_FROM_RAW" : formatted;
    },
    expected: "DIFFERS_FROM_RAW",
  },
  {
    name: "formattedFetchedAt: invalid/garbage string -> falls back to raw string unchanged",
    run: () => formattedFetchedAt("not-a-real-date"),
    expected: "not-a-real-date",
  },
  {
    name: "formattedFetchedAt: null -> null",
    run: () => formattedFetchedAt(null),
    expected: null,
  },
  {
    name: "formattedFetchedAt: undefined -> null",
    run: () => formattedFetchedAt(undefined),
    expected: null,
  },
  {
    name: "formattedFetchedAt: empty string -> null (falsy short-circuit, not treated as invalid-date fallback)",
    run: () => formattedFetchedAt(""),
    expected: null,
  },
];

function main() {
  let failed = 0;
  for (const { name, run, expected } of CASES) {
    let actual;
    let threw = false;
    try {
      actual = run();
    } catch (err) {
      threw = true;
      actual = `THREW: ${err && err.message}`;
    }
    const pass = !threw && actual === expected;
    if (pass) {
      console.log(`PASS: ${name}`);
    } else {
      failed += 1;
      console.error(`FAIL: ${name}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`);
    }
  }
  console.log(`\n${CASES.length - failed}/${CASES.length} passed`);
  if (failed > 0) process.exitCode = 1;
}

main();
