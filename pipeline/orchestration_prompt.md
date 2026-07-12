# Admissions Intelligence Pipeline Orchestration Prompt

**Context**: This prompt is designed to be run as a scheduled cloud routine (via the `/schedule` skill / `RemoteTrigger`). It orchestrates all five pipeline stages end-to-end:
1. **Stage 1**: Scrape institutions
2. **Stage 2**: Chunk for classification
3. **Stage 3**: Content-classifier (Claude agent)
4. **Stage 4**: Extract and build final records (undergrad-only — Postgraduate-classified chunks are excluded here, not just hidden downstream)
5. **Stage 5**: Build & publish static data (writes `dashboard/frontend/public/data/{records,institutions}.json`), then commit and push to `main`

The dashboard fetches `/data/records.json` and `/data/institutions.json` directly — there is no backend and no database. Cloud Run and Firestore were dropped entirely (Phase E); Firebase Hosting only serves static files.

**This routine does not deploy.** It runs in an isolated cloud sandbox with no access to local files, local services, or local environment variables — there's nowhere in that sandbox to safely hold the Firebase deploy credential. Instead, the routine's job ends at "the new data is live in git." Pushing to `main` under `dashboard/frontend/**` triggers `.github/workflows/deploy.yml`, a separate GitHub Actions workflow that builds and deploys using a `FIREBASE_TOKEN` GitHub Actions secret — a credential this routine's prompt never sees, sets, or needs (Phase F).

---

## Instructions

### Overview
You are orchestrating a data pipeline with five stages. Your job is to:
1. Run stages 1-2 (scraper + chunking)
2. Spawn the content-classifier agent to handle stage 3
3. Run stage 4 (extraction build) once classifier is done
4. Run stage 5 (build & publish static data), then commit and push — GitHub Actions takes it from there
5. Report final results

**Key principle**: Fail gracefully. If any stage errors, log it clearly and stop — do NOT proceed to later stages.

---

### Step 1: Run Stages 1-2 (Scraper + Chunking)

Run the orchestration script to fetch data and produce chunks:

```bash
cd /root/work/d--Admissions-Intelligence
python -m pipeline.run_full stage1_2 --out-scraped .tmp/scraped --out-chunks .tmp/chunks/chunks.json
```

**Expected outputs:**
- `.tmp/scraped/`: One JSON file per source (e.g., `giki.json`, `uet__lahore.json`, etc.)
- `.tmp/chunks/chunks.json`: JSON array of chunk objects with id, institution_id, source_url, raw_text

**Error handling:**
- If no scraped records exist: Script exits 1. **STOP** and report "Scraper failed to produce output."
- If chunks.json is empty or has 0 chunks: Script exits 1. **STOP** and report "No chunks produced; check scraper output."
- If partial failure (some sources fail, others succeed): Script exits 0 with warnings. **PROCEED** (partial data is valid).

**Verify chunks.json exists and is non-empty before proceeding to Step 2.**

---

### Step 2: Invoke Content-Classifier Agent

Spawn the content-classifier agent to classify all chunks from Step 1 into Undergraduate/Postgraduate/Ambiguous.

**Agent invocation:**

Use the Agent tool (not Bash) to invoke content-classifier:

```
Read the chunks file at .tmp/chunks/chunks.json
Classify each chunk into Undergraduate, Postgraduate, or Ambiguous.
Write output to .tmp/chunks/classified.json in the exact format:
{
  "Undergraduate": [list of chunk IDs],
  "Postgraduate": [list of chunk IDs],
  "Ambiguous": [{"id": "...", "reason": "mixed-degree-level" | "no-signal" | "extraction-broken" | "not-admissions-content"}, ...]
}

Only JSON output, no explanation.
```

**Wait for completion:**
- Check `.tmp/chunks/classified.json` exists (agent output file)
- Verify it contains valid JSON
- If file doesn't appear within 15 minutes: **ABORT** and report "Classifier timed out"
- If file exists but is malformed JSON: **ABORT** and report "Classifier output is invalid JSON"

**Important:** Do NOT proceed to Step 3 until classified.json is present and valid.

---

### Step 3: Run Stage 4 (Extraction Build)

Once classifier finishes, run the extraction build to merge classifier results with field extraction:

```bash
python -m pipeline.run_full stage4 --out-scraped .tmp/scraped --classified .tmp/chunks/classified.json --out .tmp/extracted
```

**Expected outputs:**
- `.tmp/extracted/`: One JSON file per chunk ID (e.g., `chunk_001.json`, `chunk_002.json`, etc.)
- Each file is a valid ExtractedRecord with institution_id, campus, source_url, chunk_id, degree_level, deadline, fee, programs, etc.

**Error handling:**
- If classified.json is missing: Script exits 1. **STOP** and report "Classifier output not found."
- If extraction build fails: Script exits 1. **STOP** and report the error.
- If no records extracted: Script exits 1. **STOP** and report "No records extracted."
- If partial extraction (some records fail, others succeed): Script exits 0 with warnings. **PROCEED**.

**Note:** Postgraduate-classified chunks are excluded from `.tmp/extracted/` entirely at this stage (undergrad-only project scope) — the stage 4 summary line reports how many were excluded. This is expected, not a failure.

---

### Step 4: Run Stage 5 (Build & Publish Static Data), Commit and Push

Build the static data the dashboard fetches, then commit and push it — this is the routine's last step; GitHub Actions handles the actual deploy from here:

```bash
python -m pipeline.run_full stage5 --extracted .tmp/extracted
git add dashboard/frontend/public/data/records.json dashboard/frontend/public/data/institutions.json
git commit -m "chore: publish pipeline data ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
git push origin main
```

**Expected behavior:**
- `stage5` reads `.tmp/extracted/*.json` fully into memory first; only once that succeeds — and only if at least one record was found — does it write `dashboard/frontend/public/data/records.json` and `institutions.json`, both as one atomic unit (temp files first, then replaced together). A read failure or an empty/wrong `--extracted` path never touches (or blanks out) the previously-published live data.
- If neither file actually changed content since the last run (no new deadlines/fees/programs, nothing scraped differently), `git commit` will report nothing to commit — that's a normal outcome, not a failure. Skip the push in that case rather than erroring.
- `git push` lands the new data on `main` under `dashboard/frontend/public/data/`, which triggers `.github/workflows/deploy.yml` to build and deploy. This routine's job is done at that point — it does not wait for or verify the GitHub Actions run.

**Error handling:**
- If `stage5` exits 1 (unreadable `.tmp/extracted/`, zero records found, a malformed record, or `config/institutions.yaml` unreadable): **STOP** before touching `git` at all. Report the specific error; the live dashboard's previously-published data is untouched.
- If `git push` fails (no push credentials on this checkout, network, conflict with a concurrent push): **STOP** and report the error. The new data exists locally in this sandbox but never reached `main`, so nothing deploys — retry next cycle. (Whether this routine's git checkout actually has push permission to the repo is unverified as of Phase F — if every run fails here, that's the first thing to check.)

---

### Step 5: Verify Data Integrity

Once extraction build completes, verify the output is valid before publishing:

```bash
# Count records
ls .tmp/extracted/*.json | wc -l

# Spot-check a record is valid JSON
cat .tmp/extracted/$(ls .tmp/extracted/*.json | head -1) | python -m json.tool > /dev/null && echo "OK"
```

**Checks:**
- `.tmp/extracted/` directory exists and contains ≥1 JSON file
- Sample records parse as valid JSON
- Each record has required fields: `source_url`, `chunk_id`, `degree_level`

If any check fails: **ABORT** and report the specific failure — do not proceed to Step 4.

---

### Step 6: Report Results

Report the final status to the user:

```
✅ PIPELINE COMPLETE

Stages executed:
  1. Scraper: [N] sources processed
  2. Chunking: [M] chunks produced
  3. Classification: [K] classified
  4. Extraction: [P] records extracted ([Q] postgraduate excluded)
  5. Publish & push: [P] records + [I] institutions published, pushed to main (or "no changes to commit")

GitHub Actions will build and deploy: https://admissions-intelligence-2fc32.web.app

Next run: [Next scheduled time]
```

If any stage failed, report:

```
❌ PIPELINE FAILED

Stage: [which stage]
Error: [specific error message]
Action: Review logs above. Previous data retained in dashboard.
Next attempt: [Next scheduled time]
```

---

## Error Scenarios & Recovery

| Scenario | Action |
|----------|--------|
| Scraper fails for all sources | Report error; stop. Previous data retained. |
| Scraper partial failure (some sources fail) | Continue with partial data; warn user. |
| No chunks produced | Stop before classifier; report "No data to classify." |
| Classifier times out (>15 min) | Stop; report "Classifier unavailable." Retry next cycle. |
| Classifier output invalid | Stop; report "Classifier output malformed." Retry next cycle. |
| Extraction build fails | Stop; report error. Previous data retained. |
| No records extracted | Stop; report "No records produced." Previous data retained. |
| Partial extraction (some records fail) | Continue; warn user. Some records extracted. |
| Stage 5 finds zero records / bad `--extracted` path | Stop before writing anything. Previously-published live data untouched. |
| Nothing changed since last publish | `git commit` has nothing to commit — skip the push, report success with no deploy triggered. |
| `git push` fails | Stop; report error. New data never reached `main`; nothing deploys; retry next cycle. |
| GitHub Actions build/deploy fails | Outside this routine's visibility — surfaces in the Actions run log, not here. Check https://github.com/muz2k247/Admissions-Intelligence/actions if the live site stops updating despite successful routine runs. |

---

## Environment & Assumptions

- **Working directory**: Admissions Intelligence project root (repo root of the routine's git checkout)
- **Python**: venv activated with all dependencies installed (`pip install -r requirements.txt`)
- **Git**: the routine's checkout must have push access to `main` on the connected repo — unverified as of Phase F, confirm on the first real run (see Step 4 error handling)
- **Disk**: `.tmp/` directory writable, sufficient space for extracted records (~1-10MB typical)
- **Agent availability**: Content-classifier agent must be functional and available — the routine's `allowed_tools` must include `Agent`, not just `Bash`/`Read`/`Write`/`Edit`/`Glob`/`Grep`
- **Next run**: Scheduled job will re-run on next cron cycle (interval set when the routine is created via `/schedule`; minimum 1 hour)

---

## Quick Reference: File Paths

```
.tmp/scraped/                                                ← Stage 1 output (raw HTML)
.tmp/chunks/chunks.json                                       ← Stage 2 output (chunk array)
.tmp/chunks/classified.json                                   ← Stage 3 output (classifier results)
.tmp/extracted/                                                ← Stage 4 output (final ExtractedRecords, undergrad-only)
dashboard/frontend/public/data/{records,institutions}.json    ← Stage 5 output (static dashboard data, git-tracked)

Dashboard fetches: /data/records.json, /data/institutions.json (same-origin, no backend)
Push to main under dashboard/frontend/** triggers: .github/workflows/deploy.yml (build + Firebase Hosting deploy)
Live site: https://admissions-intelligence-2fc32.web.app
```

---

## Testing Locally (Before Scheduling)

To test the full pipeline before scheduling:

```bash
# Terminal 1: Run this prompt manually up through stage 5
# (Copy the steps above and execute them; stop before `git push`
#  unless you actually want to trigger a real GitHub Actions deploy)

# Terminal 2: Start the frontend against the locally-generated data
cd dashboard/frontend
npm run dev
```

`vite dev` serves `public/` the same way `dist/` gets served in production, so a local `stage5` run followed by `npm run dev` is enough to see real data in the dashboard without pushing or deploying anything.

Once all steps complete successfully, you can proceed with scheduling via the `/schedule` skill.
