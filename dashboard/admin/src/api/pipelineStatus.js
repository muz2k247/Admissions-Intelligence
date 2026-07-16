// Reads the pipeline workflow's run history straight from GitHub's public
// REST API -- unauthenticated, since this repo is public and run history
// isn't sensitive. No new credential: this is display-only status for the
// curator, entirely separate from the Firestore-backed schedule config
// above and from pipeline/schedule_gate.py's own (authenticated, GITHUB_
// TOKEN-based) use of the same API to decide whether to dispatch.
const REPO = import.meta.env.VITE_GITHUB_REPO || "muz2k247/Admissions-Intelligence";
const WORKFLOW_FILE = "pipeline.yml";

export async function fetchLastPipelineRun() {
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/runs?per_page=1`;
  const resp = await fetch(url, { headers: { Accept: "application/vnd.github+json" } });
  if (!resp.ok) {
    throw new Error(`GitHub API returned HTTP ${resp.status}`);
  }
  const body = await resp.json();
  const run = Array.isArray(body?.workflow_runs) ? body.workflow_runs[0] : null;
  if (!run) return null;
  return {
    status: run.status,
    conclusion: run.conclusion,
    createdAt: run.created_at,
    htmlUrl: run.html_url,
  };
}
