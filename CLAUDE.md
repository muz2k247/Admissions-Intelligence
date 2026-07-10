# CLAUDE.md — Admissions Intelligence & Publishing Pipeline

## What this project is
A solo-built system that monitors undergraduate admissions across 16 KIPS-target Pakistani institutions (15 distinct admitting portals — see `docs/institution_registry.md`), extracts structured data, and presents it. Cost minimization is a standing constraint: prefer direct APIs, self-hosted scraping, and lighter models over paid SaaS.

## Current phase scope — read this before building anything
**In scope right now:** scrape → extract → present (web dashboard). Build only this.

**Don't build ahead of scope, and don't guess what comes next.** The roadmap beyond this phase isn't fixed here — treat it as unknown rather than filling in assumptions about what future features will be or how they'll work. If a design choice today would only make sense in service of some imagined future feature, that's a sign to stop and confirm scope instead of proceeding.

**Keep the data honest regardless.** Every extracted record retains its source URL and per-field confidence score, not because a specific future feature needs them, but because they're cheap to keep now and expensive to reconstruct later if anything downstream ever needs them.

## Target institutions
Full list, URLs, formats, and campus structure: `docs/institution_registry.md`. Machine-readable version lives at `config/institutions.yaml` — this is what all scraper/extraction code reads from. **Never hardcode an institution's URL, selector, or campus list directly in code.** If a change requires editing anything other than `config/institutions.yaml`, that's a design smell.

Three structural patterns exist across the 15 sources, and the config schema must represent all three through the *same* shape — not as special cases bolted on later:

- **Single-URL** (GIKI, PIEAS, LUMS, ITU, IST, etc.): one institution, one source, no campus distinction.
- **Multi-campus** (UET, FAST, COMSATS, Air University, Bahria): one institution, several sources, each tied to a named campus.
- **Admitting-body** (UHS, NUMS): one or more sources admit on behalf of several named constituent colleges. Don't model these as flat, unrelated institutions — a record extracted from one of these sources belongs to a specific constituent college, and that has to be capturable per-record, not assumed from the source alone.

Every institution is one or more **sources**; every source has a `campus` field that's simply `null` when there's no meaningful campus split. This makes single-URL the default/simple case, not an exception:

```yaml
- id: giki
  admitting_body: false
  sources:
    - campus: null
      url: https://admissions.giki.edu.pk
      format: html

- id: uet
  admitting_body: false
  sources:
    - campus: "Lahore (Main)"
      url: https://apply.uet.edu.pk
      format: html
    - campus: "Taxila"
      url: https://admissions.uettaxila.edu.pk
      format: html

- id: uhs
  admitting_body: true
  sources:
    - campus: null
      url: https://public-mbbs.uhs.edu.pk
      format: html+pdf
      constituent_colleges: [King Edward Medical University, Allama Iqbal Medical College, Nishtar Medical University, Allied]
```

`constituent_colleges` on a source is the set the extractor is allowed to match a record against — the extractor determines which specific college a given record belongs to (never assumed from the source URL alone), and if it can't tell, that field is `null` like any other uncertain field under the hard rules below.

## Hard rules (non-negotiable)
1. **Never infer, calculate, or guess a missing field.** If a fee, deadline, or other detail isn't explicitly stated on the source page, the field is `null`. No defaults, no backfilling.
2. **Field-level confidence, not record-level.** Every extracted field carries its own confidence score. A strong deadline field doesn't excuse a weak fee field.
3. **UG/PG filtering happens at content level, not URL level.** Many institutions mix undergraduate and postgraduate announcements on the same page. Use the `content-classifier` subagent (see below) — never assume a page's URL tells you the degree level. **The project is undergrad-only in scope** (as of 2026-07): Postgraduate-classified chunks are excluded from extraction output entirely, not merely hidden by a UI filter — see `extraction/run.py`'s `build_extracted_records`. Ambiguous is not the same failure type as Postgraduate and stays in the output, reviewable via its reason code; the dashboard just doesn't blend it into the default view (it's an explicit opt-in filter, defaulting to Undergraduate-only).
4. **Every record keeps its source URL.** No exceptions, even for high-confidence extractions.
5. **A correct answer late beats a wrong answer immediately.** When in doubt, leave it null or route to `Ambiguous` rather than force a value.

## Security & Secrets Management (CRITICAL)

**ABSOLUTE RULES — NEVER BREAK THESE:**

1. **Never commit secrets to git.** This includes:
   - `.env` file (should be in `.gitignore`)
   - `firebase-key.json` or any service account keys
   - API keys, tokens, passwords
   - Private encryption keys
   - Database credentials
   
   If you accidentally commit a secret, immediately rotate it in Firebase Console / Google Cloud. Committed secrets are compromised.

2. **Use `.env` file for all local configuration:**
   - Copy `.env.example` → `.env` locally (never commit `.env`)
   - All credentials go in `.env`, referenced as environment variables
   - Backend reads from `os.environ.get("VARIABLE_NAME")`
   - Frontend reads from `import.meta.env.VITE_VARIABLE_NAME`

3. **Never log, print, or mention secrets in any form:**
   - Don't log `os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_JSON")` even for debugging
   - Don't include secret values in error messages
   - Don't mention secrets in commit messages, PR descriptions, or code comments
   - Don't paste secrets in conversations with Claude or any LLM (I will never ask for them)

4. **Never hardcode secrets in code:**
   - Bad: `FIREBASE_KEY = "eyJhbGc..."`
   - Good: `FIREBASE_KEY = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_JSON")`

5. **Cloud deployment uses cloud-native secret management:**
   - Cloud Run: Set secrets via Google Cloud Console (not in Dockerfile or dockerfile build args)
   - GitHub Actions: Use GitHub Secrets (not env vars in yaml files)
   - Never pass secrets as command-line arguments

6. **Service account keys are treated as highly sensitive:**
   - Firebase service accounts have admin access to Firestore
   - Restrict to minimal required permissions (read/write `extracted_records` collection only)
   - Store locally in `.env` only during development
   - Rotate keys regularly (Firebase Console: Service Accounts tab → Manage Keys)
   - If key is exposed: Delete immediately in Firebase Console, regenerate, update `.env`

7. **If you discover a secret has been exposed:**
   - STOP immediately
   - Rotate the key/credential in the cloud console (Firebase, GCP, etc.)
   - Update all `.env` files with new credentials
   - Verify the commit containing the secret is not reachable (force-push only if it's local, never if pushed)
   - Document what happened for audit purposes

8. **Testing and development:**
   - Use separate Firebase project for development (not production credentials)
   - Test fixtures should never contain real secrets
   - Mock credentials for unit tests (use fake but valid-format strings)
   - Integration tests should use development-only service accounts

## Format handling
HTML is primary for all 15 sources. PDF fallback is required for Punjab University and UHS/NUMS notices specifically (both post supplementary PDFs — date sheets, merit lists). Build the PDF path as a fallback the HTML scraper calls, not a separate parallel pipeline.

## Subagents
Defined in `.claude/agents/`. Follow the design/build loop for any non-trivial code:
1. Write/edit code.
2. Spawn `code-reviewer` — reports issues, does not fix.
3. Spawn `qa` — writes and runs tests, does not fix.
4. Parent agent applies fixes from both reports.
5. Ship only after both pass.

Use `research` for institution site investigation (prefer official `.edu.pk` domains over aggregators; timestamp anything deadline/fee-related). Use `content-classifier` for UG/PG routing on scraped chunks — `Ambiguous` results carry a reason code; don't treat all `Ambiguous` items as the same failure type.

**Thought → Action → Review**: for any change touching the hard rules above (null-handling, confidence scoring, UG/PG routing, source URL retention), state what you're checking for before you act, then verify the result against that stated check before moving on — don't just run the subagent loop and assume PASS means done.

## Repo structure
```
.claude/agents/       subagent definitions
config/                institutions.yaml (registry, machine-readable)
docs/                  institution_registry.md + architecture notes
scraper/                HTML/PDF fetching, keyed off config/institutions.yaml
extraction/             field extraction + confidence scoring + content-classifier calls
dashboard/              web dashboard
tests/fixtures/<institution>/   saved HTML/PDF for QA — never test against live sites
.tmp/                   intermediates, never committed
```

## Presentation layer (Phase D)
The dashboard must work flawlessly on both desktop and mobile — not desktop-first with mobile as an afterthought. Mobile-first responsive layout, no horizontal scroll, adaptive navigation by breakpoint, and accessible contrast/touch targets are non-negotiable, not nice-to-haves. If a `ui-ux-pro-max`-style design skill is available in the environment, use it for any UI decision (layout, component, styling, chart) — it already treats mobile+desktop as one system rather than two separate builds.

The dashboard's default view is Undergraduate-only (no PDF export — dropped as unnecessary scope). Ambiguous records are reachable only via an explicit opt-in filter for manual review, never blended into the default view alongside Undergraduate.

## Commit conventions
Commit after each phase is functionally complete and passes the code-reviewer/qa loop — not mid-phase. Phases for this stage of the project:
- Phase A: institution registry (`config/institutions.yaml`, `docs/institution_registry.md`)
- Phase B: scraper (config-driven, HTML + PDF fallback)
- Phase C: extraction schema + content-classifier integration
- Phase D: dashboard

Plain, descriptive commit messages tied to the phase (e.g. `feat: config-driven scraper for pilot institutions`) — no need for heavier conventions than that at solo scale.

## Notes for future phases
Future additions should consume the same `config/institutions.yaml` and the same extracted-record schema (source URL + per-field confidence) rather than introducing a second data model. Flag it if a future addition would require reshaping data already in place.
