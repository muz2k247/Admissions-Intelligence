// Manual verification of formattedFinishedAt, a plain helper added to
// src/App.jsx that formats the pipeline health.json's `finished_at` timestamp
// for the new footer ("Data last updated ..."). It has no external
// dependencies beyond the built-in Date global, so rather than pulling it out
// into an importable module (and touching App.jsx's exports for a QA-only
// need) this script re-implements it verbatim and asserts against realistic
// inputs shaped per pipeline/health.py's actual output. There's no JS test
// framework in this repo (see dashboard/frontend/package.json), matching the
// existing manual-script pattern in dashboard/admin/scripts/*.mjs (e.g.
// verify_record_meta_helpers.mjs).
//
// Run from dashboard/frontend/: `node scripts/verify_footer_helpers.mjs`

// --- verbatim copy from src/App.jsx ---
function formattedFinishedAt(finishedAt) {
  if (!finishedAt) return null;
  const date = new Date(finishedAt);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
// --- end verbatim copy ---

const CASES = [
  {
    name: "real health.py shape: ISO with +00:00 offset and 6-digit microseconds -> formatted, differs from raw",
    run: () => {
      const raw = "2026-07-16T18:57:46.231200+00:00";
      const formatted = formattedFinishedAt(raw);
      return formatted !== null && typeof formatted === "string" && formatted !== raw
        ? "OK_FORMATTED"
        : { formatted };
    },
    expected: "OK_FORMATTED",
  },
  {
    name: "ISO without fractional seconds, Z suffix -> formatted, differs from raw",
    run: () => {
      const raw = "2026-07-16T18:57:46Z";
      const formatted = formattedFinishedAt(raw);
      return formatted !== null && typeof formatted === "string" && formatted !== raw
        ? "OK_FORMATTED"
        : { formatted };
    },
    expected: "OK_FORMATTED",
  },
  {
    name: "null -> null",
    run: () => formattedFinishedAt(null),
    expected: null,
  },
  {
    name: "undefined -> null",
    run: () => formattedFinishedAt(undefined),
    expected: null,
  },
  {
    name: "empty string -> null (falsy short-circuit)",
    run: () => formattedFinishedAt(""),
    expected: null,
  },
  {
    name: "garbage/unparseable string -> null (NOT the raw string echoed back)",
    run: () => formattedFinishedAt("not-a-date"),
    expected: null,
  },
  {
    name: "numeric epoch-like string ('1700000000000') -> Date parses as NaN in ISO-string branch, so null",
    run: () => formattedFinishedAt("1700000000000"),
    expected: null,
  },
  {
    name: "plain date-only string ('2026-07-16') -> formatted (Date parses date-only ISO as valid, midnight UTC)",
    run: () => {
      const raw = "2026-07-16";
      const formatted = formattedFinishedAt(raw);
      return formatted !== null && typeof formatted === "string" && formatted !== raw
        ? "OK_FORMATTED"
        : { formatted };
    },
    expected: "OK_FORMATTED",
  },
];

// Explicit microsecond-precision parse check: verify the exact value Node
// derives from the 6-digit-fraction real-world timestamp shape, since JS
// Date parsing of fractional seconds beyond milliseconds (3 digits) is a
// known cross-engine edge case (V8 truncates/rounds rather than throwing,
// but this hasn't always been guaranteed across engines).
function microsecondPrecisionCheck() {
  const raw = "2026-07-16T18:57:46.231200+00:00";
  const date = new Date(raw);
  const isValid = !Number.isNaN(date.getTime());
  const isoMs = date.toISOString();
  console.log(`\nMicrosecond-precision parse check for "${raw}":`);
  console.log(`  valid date: ${isValid}`);
  console.log(`  date.getTime(): ${date.getTime()}`);
  console.log(`  date.toISOString(): ${isoMs}`);
  console.log(
    `  interpretation: Node's V8 parses the first 3 fractional digits (231) as milliseconds ` +
      `and appears to ${isoMs.includes(".231") ? "truncate (not round)" : "round"} the remaining digits (200 microseconds), ` +
      `since .2312 rounds to .231ms either way here. This input parses successfully either way.`
  );
  return isValid;
}

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
    const pass = !threw && JSON.stringify(actual) === JSON.stringify(expected);
    if (pass) {
      console.log(`PASS: ${name}`);
    } else {
      failed += 1;
      console.error(`FAIL: ${name}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`);
    }
  }
  console.log(`\n${CASES.length - failed}/${CASES.length} passed`);

  const microsecondsOk = microsecondPrecisionCheck();
  if (!microsecondsOk) {
    failed += 1;
    console.error("FAIL: microsecond-precision timestamp did not parse as a valid Date");
  }

  if (failed > 0) process.exitCode = 1;
}

main();
