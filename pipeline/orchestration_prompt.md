# Admissions Intelligence Pipeline Orchestration Prompt

**Context**: This prompt is designed to be run as a scheduled cloud job (via CronCreate/schedule). It orchestrates all five pipeline stages end-to-end:
1. **Stage 1**: Scrape institutions
2. **Stage 2**: Chunk for classification
3. **Stage 3**: Content-classifier (Claude agent)
4. **Stage 4**: Extract and build final records (undergrad-only — Postgraduate-classified chunks are excluded here, not just hidden downstream)
5. **Stage 5**: Sync extracted records to Firestore (no-op if `FIREBASE_PROJECT_ID` isn't set — local dev stays file-based)

The dashboard backend reads from Firestore when `FIREBASE_PROJECT_ID` is configured (production), falling back to `.tmp/extracted/` locally, and serves results via `/api/records`.

---

## Instructions

### Overview
You are orchestrating a data pipeline with five stages. Your job is to:
1. Run stages 1-2 (scraper + chunking)
2. Spawn the content-classifier agent to handle stage 3
3. Run stage 4 (extraction build) once classifier is done
4. Run stage 5 (Firestore sync) so the deployed dashboard actually serves the new data
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

### Step 4: Run Stage 5 (Firestore Sync)

Push the newly built extracted records to Firestore so the deployed dashboard (Cloud Run + Firebase Hosting) actually serves them — without this step, a correct local extraction never reaches production:

```bash
python -m pipeline.run_full stage5 --extracted .tmp/extracted
```

**Expected behavior:**
- If `FIREBASE_PROJECT_ID` is not set in the environment: exits 0 immediately, no-op. This is normal for local/dev runs.
- If set: clears the `extracted_records` Firestore collection and writes the new batch, only after all local records have been read successfully (so a read failure never wipes Firestore's last-good data).

**Error handling:**
- If `.tmp/extracted/` is missing or contains an unreadable record: Script exits 1 before touching Firestore. **STOP** and report the error; Firestore's previous data is untouched.
- If the Firestore delete or a write fails: Script exits 1. **STOP** and report the error.

---

### Step 5: Verify Data Integrity

Once extraction build completes, verify the output is valid for the dashboard:

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

If any check fails: **ABORT** and report the specific failure.

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
  5. Firestore sync: [R] records synced (or skipped — not configured)

Data is ready at: http://localhost:8000/api/records
Dashboard: http://localhost:5173

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
| Firestore sync fails (read or write error) | Stop; report error. Firestore's previous data untouched. |
| `FIREBASE_PROJECT_ID` not set | Skip sync silently (expected for local/dev runs). |

---

## Environment & Assumptions

- **Working directory**: Admissions Intelligence project root (`d:\Admissions-Intelligence` on Windows)
- **Python**: venv activated with all dependencies installed (`pip install -r requirements.txt`)
- **Disk**: `.tmp/` directory writable, sufficient space for extracted records (~1-10MB typical)
- **Agent availability**: Content-classifier agent must be functional and available
- **Next run**: Scheduled job will re-run on next cron cycle (every 6 hours default)

---

## Quick Reference: File Paths

```
.tmp/scraped/                     ← Stage 1 output (raw HTML)
.tmp/chunks/chunks.json           ← Stage 2 output (chunk array)
.tmp/chunks/classified.json       ← Stage 3 output (classifier results)
.tmp/extracted/                   ← Stage 4 output (final ExtractedRecords, undergrad-only)
Firestore `extracted_records`     ← Stage 5 output (production data source)

Backend reads from: Firestore (if FIREBASE_PROJECT_ID set) else .tmp/extracted/
Dashboard serves: http://localhost:5173
API: http://localhost:8000/api/records
```

---

## Testing Locally (Before Scheduling)

To test the full pipeline before scheduling:

```bash
# Terminal 1: Run this prompt manually
# (Copy the steps above and execute them)

# Terminal 2: Start the backend
python -m uvicorn dashboard.backend.main:app --reload

# Terminal 3: Start the frontend (if needed)
cd dashboard/frontend
npm run dev
```

Once all steps complete successfully, you can proceed with scheduling via the `/schedule` skill.
