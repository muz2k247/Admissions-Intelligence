// Screenshot-verification harness for the dashboard (repo-root dev tooling,
// deliberately NOT wired into CI). `npm run screenshots` builds the dashboard,
// serves the built output via `vite preview`, and captures desktop + mobile
// screenshots into .tmp/screenshots/ so a change can be visually verified.
//
// The committed dashboard/frontend/public/data/*.json guarantee the build has
// real data to render. Screenshots run only against this local preview server
// — never a live site (CLAUDE.md network-isolation rule).
import { defineConfig, devices } from "@playwright/test";

const PREVIEW_URL = "http://localhost:4173";

export default defineConfig({
  testDir: "screenshots",
  outputDir: ".tmp/playwright",
  // Build once, then serve dist/ for the whole run. Playwright starts this,
  // waits for the URL to respond, and tears it down when the run finishes.
  webServer: {
    command: "npm run build && npm run preview",
    cwd: "dashboard/frontend",
    url: PREVIEW_URL,
    timeout: 120_000,
    // Always build+serve fresh so the screenshots reflect the CURRENT working
    // tree. Reusing a server left running on :4173 would silently verify a
    // stale build (this tool is local-only, so a CI-gated reuse flag would be
    // "reuse" in practice). If the port is already occupied, Playwright fails
    // loudly — that's preferable to a misleading pass. Stop any stray
    // `vite preview`/`dev` on 4173 before running.
    reuseExistingServer: false,
  },
  use: {
    baseURL: PREVIEW_URL,
  },
  projects: [
    {
      name: "desktop",
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      // Chromium-based Android device: keeps the whole run on the one browser
      // engine we install (Chromium) and matches this project's audience —
      // mobile in Pakistan is predominantly Android/Chrome.
      name: "mobile",
      use: { ...devices["Pixel 5"] },
    },
  ],
});
