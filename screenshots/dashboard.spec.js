// Captures the dashboard's default (Undergraduate-only) view for visual
// verification, once per project (desktop + mobile — see playwright.config.js).
// Screenshots land in .tmp/screenshots/<project>-*.png (gitignored).
import { test, expect } from "@playwright/test";
import path from "node:path";

const SHOT_DIR = path.resolve(".tmp/screenshots");

test("default Undergraduate view renders and fits the viewport", async ({ page }, testInfo) => {
  const project = testInfo.project.name;

  await page.goto("/");
  // Gate the screenshot on a real record actually rendering, not just on
  // network settling. The committed public/data/*.json always contain
  // records, so a healthy dashboard shows RecordCards; `.record-card__title`
  // is rendered ONLY in that state — never by the loading skeletons
  // (`record-card--skeleton`, no title) or the error/empty states. So if the
  // data fetch fails and the app renders blank, this assertion times out and
  // the harness FAILS, instead of silently screenshotting a broken shell.
  await expect(page.locator(".record-card__title").first()).toBeVisible({ timeout: 15_000 });
  // Let fonts/layout settle for a clean capture now that content is present.
  await page.waitForLoadState("networkidle");

  await page.screenshot({
    path: path.join(SHOT_DIR, `${project}-default.png`),
    fullPage: true,
  });

  // Non-negotiable (CLAUDE.md Presentation layer): no horizontal scroll at any
  // breakpoint. Assert the document isn't wider than the viewport.
  const overflow = await page.evaluate(() => {
    const el = document.scrollingElement || document.documentElement;
    return { scrollWidth: el.scrollWidth, innerWidth: window.innerWidth };
  });
  expect(
    overflow.scrollWidth,
    `${project}: horizontal overflow (scrollWidth ${overflow.scrollWidth} > innerWidth ${overflow.innerWidth})`,
  ).toBeLessThanOrEqual(overflow.innerWidth + 1); // +1 tolerates sub-pixel rounding
});
