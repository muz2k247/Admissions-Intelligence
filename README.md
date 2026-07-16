# Admissions Intelligence

A solo-built system that monitors undergraduate admissions across 16 KIPS-target Pakistani institutions, extracts structured data, and presents it via a static web dashboard.

## Status
Phases A–L complete: config-driven scraper (HTML + PDF fallback + headless-browser rendering for JS-gated sources), LLM-based extraction with confidence scoring and content-level UG/PG classification, a static-published dashboard (no backend, no database), a curator admin CMS for field corrections, and an automated weekly pipeline (Gemini API + GitHub Actions cron). Currently in Phase M: hardening pipeline reliability before adding admin-managed institutions and a needs-review queue — see `CLAUDE.md`'s Commit conventions for the full phase history and approved roadmap.

## Live URLs
- **Public dashboard (students):** https://admissions-intelligence-2fc32.web.app
- **Admin CMS (curators):** https://admissions-intelligence-review.web.app

The public site's Firebase Hosting site id is `admissions-intelligence-2fc32` (the default site for this project), so its canonical URL carries the `-2fc32` suffix. **`https://admissions-intelligence.web.app` is NOT this project** — that hostname belongs to no site here and returns Firebase's "Site Not Found" page. Both sites are defined as hosting targets in [`.firebaserc`](./.firebaserc) and deployed by [`.github/workflows/deploy.yml`](./.github/workflows/deploy.yml).

## Project rules
Full architecture, hard rules (data integrity, config-driven scraping, UG/PG filtering), and subagent conventions live in [`CLAUDE.md`](./CLAUDE.md) — read that before contributing or running an agent session against this repo.

## Setup
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Dashboard frontend (static, no backend):
```powershell
cd dashboard/frontend
npm install
npm run dev
```

## Structure
```
.claude/agents/         subagent definitions
config/                  institution registry (machine-readable)
docs/                    institution registry (human-readable) + architecture notes
scraper/                 HTML/PDF fetching, headless-browser rendering for JS-gated sources
extraction/              field extraction + confidence scoring + content-classifier integration
pipeline/                orchestration: scrape -> chunk -> classify -> extract -> publish
dashboard/frontend/      public static dashboard (fetches published JSON, no backend)
dashboard/admin/         curator CMS (Firebase Auth + Firestore field overrides)
tests/fixtures/          saved HTML/PDF for testing — no live-site tests
```

## License
Not yet decided.
