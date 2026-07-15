# Admin CMS — one-time manual setup (Phase K)

The admin CMS lets 1–2 curators review and correct extracted fields. Its
corrections are stored in Firestore and merged into the published static
`records.json` at pipeline stage 5 (`pipeline/overrides.py`). The public
dashboard never reads Firestore — it stays 100% static.

Most of the CMS is code (in this repo). A few steps can only be done by a
human in the Firebase Console / CLI — they create cloud resources or need
credentials an agent must never hold. Do these once, in order.

**Note on repo state:** `firestore.rules` exists in the repo now. The two-site
`firebase.json` (public + admin targets, plus the `firestore` rules pointer),
the `.firebaserc` `targets` mapping, and the `deploy.yml` wiring land together
with the admin app itself (the deploy chunk of Phase K) — run steps 1–2 and 4
below only once that config is present, or the `firebase deploy` commands they
reference won't resolve.

## Prerequisites
- Firebase CLI installed and logged in (`firebase login`) as an owner of the
  `admissions-intelligence-2fc32` project.

## 1. Create the second Hosting site (for the admin app)
The project already has one Hosting site (the public dashboard). The admin app
is a separate site so it builds/deploys independently and only it carries the
Firebase SDK.

```bash
firebase hosting:sites:create admissions-intelligence-review
```

Pick any available name; `admissions-intelligence-review` is suggested (avoid
putting "admin" in the URL — it's cosmetic only, real access control is Auth +
rules, not an obscure URL).

## 2. Map firebase.json's hosting targets to sites
`firebase.json` uses two hosting targets, `public` and `admin`. Bind them to
actual sites (this writes the `targets` block into `.firebaserc`):

```bash
firebase target:apply hosting public admissions-intelligence-2fc32
firebase target:apply hosting admin  admissions-intelligence-review
```

Until this is done, `firebase deploy` against the two-site `firebase.json` will
fail — this is the expected gate, not a bug.

## 3. Enable Firebase Authentication (Google sign-in)
In the Firebase Console → Authentication → Sign-in method, enable **Google**.
This is what gates the admin app; no other provider is needed.

## 4. Deploy the Firestore rules
`firestore.rules` makes the `overrides` collection public-read (the pipeline
reads it unauthenticated, holding no secret) and write-locked to a curator UID
allowlist.

```bash
firebase deploy --only firestore:rules
```

## 5. Fill in the curator UID allowlist
A user's UID only exists after they first sign into the admin app (step done
once the admin app ships). Then:
1. Have each curator sign in once to the deployed admin app.
2. Copy their UID from Firebase Console → Authentication → Users.
3. Add each UID to the `allow write` list in `firestore.rules`.
4. Re-deploy the rules (`firebase deploy --only firestore:rules`).

Until the allowlist has real UIDs, **no one can write** — the correct
fail-closed default. Never widen the write rule to `if request.auth != null`
(that allows any Google account on earth); keep it pinned to specific UIDs.

Note the `overrides` collection is public-read, so the audit metadata a curator
edit stores (`verified_by`, `verified_at`, `original`) is publicly readable even
though it never reaches `records.json`. Keep that metadata non-sensitive:
`verified_by` must be the opaque Firebase UID, never an email or display name.

## 6. Needs-Review queue (Phase Q)

Two more Firestore locations, same public-read / allowlisted-curator-write
pattern as `overrides` (already covered by the rules deploy in step 4 — no
extra step needed once `firestore.rules` includes them):

- **`review_decisions/{chunkId}`** — a curator's approve/reject call on a
  record `extraction/review_gate.py` flagged as low-confidence, read by
  `pipeline/review.py::fetch_review_decisions()` at publish time. Keyed by
  chunk_id + a `content_hash` of the four reviewable field values the
  curator was looking at — if a later re-scrape changes any of them, the
  hash no longer matches and the record re-queues instead of trusting a
  stale decision.
- **`settings/review_gate`** — a single document (`{enabled, threshold}`)
  letting curators tune or disable the confidence gate from the admin CMS
  without a code deploy, read by `pipeline/review.py::fetch_review_settings()`.
  Missing/unreadable defaults to `{enabled: true, threshold: 0.8}` (fail-safe:
  if the toggle itself can't be read, low-confidence data still gets queued
  rather than silently publishing).

## Notes
- These steps are the admin-CMS analogue of Phase F's one-time deploy-token
  minting: a human does them in the console, never an agent, and no secret from
  them ever enters this repo.
- The `GEMINI_API_KEY` GitHub Actions secret (for the CI pipeline) is a
  separate Phase L concern, documented there.
