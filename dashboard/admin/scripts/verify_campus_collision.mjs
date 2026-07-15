// Verifies src/lib/campusCollision.js's collision detection, mirroring
// scraper/config.py::campus_collision_key/find_campus_collision (see that
// module's docstring for the full rationale, and tests/test_scraper.py /
// tests/test_institutions_registry.py for the equivalent Python-side
// coverage). No JS test framework in this repo (see package.json) -- same
// plain, manually runnable Node script pattern as verify_hash_parity.mjs /
// verify_edit_then_approve.mjs.
//
// Run from dashboard/admin/: `node scripts/verify_campus_collision.mjs`

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

check("null and blank and whitespace-only collapse to the same key", [
  campusCollisionKey(null),
  campusCollisionKey(""),
  campusCollisionKey("   "),
], ["", "", ""]);

check("case + leading/trailing whitespace normalize to the same key",
  campusCollisionKey("  LAHORE MAIN  "), campusCollisionKey("Lahore Main"));

check("distinct campuses produce distinct keys",
  campusCollisionKey("Islamabad") !== campusCollisionKey("Karachi"), true);

check("two sources with no campus collide",
  findCampusCollision([src(null), src(null)]) !== null, true);

check("two sources with the identical campus collide",
  findCampusCollision([src("Lahore"), src("Lahore")]) !== null, true);

check("case/whitespace-variant campuses collide",
  findCampusCollision([src("Islamabad"), src("  ISLAMABAD  ")]) !== null, true);

check("whitespace-only campus collides with no-campus",
  findCampusCollision([src(null), src("   ")]) !== null, true);

check("distinct campuses (Air University shape) do not collide",
  findCampusCollision([src("Islamabad"), src("Karachi")]), null);

check("a single source never collides", findCampusCollision([src(null)]), null);
check("an empty source list never collides", findCampusCollision([]), null);

if (failed) process.exitCode = 1;
