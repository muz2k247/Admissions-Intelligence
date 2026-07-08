# Admissions Intelligence

A solo-built system that monitors undergraduate admissions across 16 KIPS-target Pakistani institutions, extracts structured data, and presents it via a web dashboard with PDF export.

## Status
Phase A (institution registry) complete — 15 verified sources covering 16 KIPS-target institutions. Phase B (scraper) not yet started. Not yet functional end-to-end.

## Project rules
Full architecture, hard rules (data integrity, config-driven scraping, UG/PG filtering), and Claude Code agent conventions live in [`CLAUDE.md`](./CLAUDE.md) — read that before contributing or running an agent session against this repo.

## Setup
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
`requirements.txt` will be added once the scraper's dependencies are locked in.

## Structure
```
.claude/agents/       Claude Code subagent definitions
.claude/skills/        project-scoped skills (if any)
config/                 institution registry (machine-readable)
docs/                   institution registry (human-readable) + architecture notes
scraper/                HTML/PDF fetching
extraction/             field extraction + confidence scoring
dashboard/              web dashboard + PDF export
tests/fixtures/          saved HTML/PDF for testing — no live-site tests
```

## License
Not yet decided.