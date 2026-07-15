// Additional coverage for src/lib/campusCollision.js, filling gaps not
// exercised by scripts/verify_campus_collision.mjs:
//   1. A collision positioned between the 2nd and 3rd source of a
//      3+-source institution (not the first pair).
//   2. An actual empty string "" campus (distinct from null/whitespace-only)
//      bucketing with null.
//
// Run from dashboard/admin/: `node scripts/verify_campus_collision_extra.mjs`

import { campusCollisionKey, findCampusCollision } from "../src/lib/campusCollision.js";

function src(campus, url = "https://example.edu.pk") {
  return { campus, url };
}

let failed = false;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    console.error(`FAIL (${name}): expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
    failed = true;
  } else {
    console.log(`PASS (${name})`);
  }
}

// -- collision at position 2-3, not the first pair --

check(
  "collision between 2nd and 3rd of 3 sources is caught",
  findCampusCollision([src("Lahore"), src("Karachi"), src("Karachi")]),
  ["Karachi", "Karachi"],
);

check(
  "collision between 2nd and 3rd, case/whitespace variant",
  findCampusCollision([src("Lahore"), src("Islamabad"), src("  ISLAMABAD  ")]),
  ["Islamabad", "ISLAMABAD"], // JS trims the label (s.campus?.trim()); Python keeps the raw untrimmed string -- see report
);

check(
  "first pair distinct does not mask a later collision (4 sources, 3rd/4th collide)",
  findCampusCollision([src("A"), src("B"), src("C"), src("C")]) !== null,
  true,
);

// -- empty string "" campus --

check(
  "empty string campusCollisionKey matches null/whitespace-only key",
  campusCollisionKey(""),
  campusCollisionKey(null),
);

check(
  "empty string campus collides with null campus",
  findCampusCollision([src(null), src("")]) !== null,
  true,
);

check(
  "empty string campus collides with whitespace-only campus",
  findCampusCollision([src("   "), src("")]) !== null,
  true,
);

check(
  "empty string campus does not collide with a real campus name",
  findCampusCollision([src(""), src("Lahore")]),
  null,
);

if (failed) process.exitCode = 1;
