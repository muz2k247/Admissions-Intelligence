# JS-Rendering Audit — Admissions Portal Sources

Purpose: determine which of the 17 scraped sources are genuinely gated behind client-side JS
rendering (plain HTTP GET returns a thin/empty shell with real content injected by JS after
load), to decide which need a headless-browser fetch path (`render: js` in
`config/institutions.yaml`).

**Evidence basis:** raw HTML captured by the project's existing scraper via plain
`requests.get()` (no JS execution), saved at `.tmp/scraped/*.json`, fetched 2026-07-10. This is
the same class of evidence that established the pre-existing `ist` finding, so all rows are
directly comparable. Audit compiled 2026-07-13 by the `research` subagent. WebFetch could not be
used for live spot-checks — Cloudflare returned HTTP 403 on `ist.edu.pk` when attempted directly.

| Source | Verdict (static-ok / js-gated) | Evidence | Underlying API found? | Notes | Date checked |
|---|---|---|---|---|---|
| uet (admission.uet.edu.pk) | static-ok | Raw HTML `<body>` contains real rendered nav/content: `>Undergraduate Admissions<`, `>Undergraduate Admission Process Schedule - Fall 2026<`, `>ECAT 2026 Phase 2- Result Announcement<` as literal unescaped tag text. Bootstrap-based server-rendered site. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| uet_taxila (admissions.uettaxila.edu.pk) | static-ok | Real rendered text incl. `>Undergraduate Admissions 2026 – Merit List<`, `>Admission Eligibility<`, `>Merit Lists<`. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| nust (ugadmissions.nust.edu.pk) | static-ok | Real rendered text incl. `>Last Date: 18 Jun 2026<`, `>Last Date: 25 Jul 2026<`, `>Eligibility Criteria for UG Programmes<`. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| giki (giki.edu.pk/admissions/admissions-undergraduates/) | static-ok | Real rendered text incl. `>Application Deadline<`, `>Merit List<`, `>Undergraduate Admission Policy<`. Corroborated by successful `degree_level: Undergraduate` classification in extraction output, unlike ist's fully-null extraction. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| pieas (admissions.pieas.edu.pk) | static-ok | Real rendered text incl. `>Fee Structure<`, `>(Last Date August 12, 2024)<`, `>Login to PRINT BS Offer Letter (Last Date: August 21, 2024)<`. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| fast_nu (admissions.nu.edu.pk) | static-ok | Server-rendered ASP.NET/Metronic page, real body text incl. `>Instructions<`, a real campus/contact table. No SPA-shell markers, no inline fetch/API calls. | N/A (not applicable — page is server-rendered) | Not JS-gated, but content is thin because this URL is a **login/registration portal**, not an info page — a content-availability gap, not a rendering problem. Don't fix with headless browser. | 2026-07-13 (evidence fetched 2026-07-10) |
| pu (admissions.pu.edu.pk) | static-ok | **Known from prior investigation, not re-verified.** Produces 200K+ characters of real text — PDF-heavy, scraper's PDF fallback already pulls linked PDF text successfully. | N/A | Sparsity, where it exists, is a separate chunking-granularity issue (Phase J), not JS-gating. | pre-established (not re-verified this session) |
| comsats (admissions.comsats.edu.pk) | static-ok | Real rendered text incl. `>UnderGraduate Programs<`, `>MS Admission Criteria<`, `>HEC National Fee Refund Policy 2024<`. No inline fetch/API calls found. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| lums (admission.lums.edu.pk) | static-ok | Real `<p>` body paragraphs with substantive prose on merit-based admission. Drupal megamenu-heavy but real article content present. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| itu (itu.edu.pk/admissions) | static-ok | Real rendered text incl. `>Eligibility Criteria<`, `>Fee Structure<`, `>Admissions Calendar<`, `>Online Admission Deadline<`. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| ist (ist.edu.pk/admission) | **js-gated** | **Known from prior investigation, spot-checked this session.** Real scrape produced only 54 characters of text from a 14KB HTML page. Raw HTML is a Next.js app-router shell — `<body>` contains only skeleton/`animate-pulse` placeholder divs and a `<template data-dgst="BAILOUT_TO_CLIENT_SIDE_RENDERING">` marker; all real content lives inside `self.__next_f.push(...)` RSC-stream JSON, not literal HTML text. Corroborated by extraction output: every field is `null` with reason `"extraction-broken"`. | No — grepped raw shell HTML for `fetch(`, `axios`, `/api/`, `graphql`, `XMLHttpRequest`; none found. The RSC payload is Next.js's internal streaming protocol, not a conventional REST/GraphQL endpoint callable directly. | Genuinely needs a headless-browser fetch path. WebFetch to ist.edu.pk also returned HTTP 403 (Cloudflare) this session — a separate access hurdle on top of JS-gating, worth monitoring if headless fetch hits the same block. | pre-established; explicit-tag spot-check performed 2026-07-13 |
| air_university — Islamabad & Punjab (portals.au.edu.pk/admissions/) | static-ok | Real rendered text incl. `>ADMISSIONS OPEN (Fall-2026)<`, `>Admission Advertisement<`, `>Eligibility Criteria<`. Uses Blazor scoped-CSS attributes but on server-rendered markup already containing literal text — consistent with Blazor Server, not Blazor WASM. | N/A | Worth a second look if this source's extraction quality is ever poor, in case some sub-pages use Blazor WASM instead. | 2026-07-13 (evidence fetched 2026-07-10) |
| air_university — Karachi (kc.au.edu.pk/.../admission_schedule.aspx) | static-ok | Real rendered text incl. `>Application Deadline  Phase-I   (<`, `>Application Deadline  Phase-II   (<`. Classic ASP.NET WebForms page, fully server-rendered. | N/A | One linked PDF 404'd during the 2026-07-10 scrape — unrelated dead link, not a rendering issue for the main HTML page. | 2026-07-13 (evidence fetched 2026-07-10) |
| uhs (public-mbbs.uhs.edu.pk) | static-ok | **Known from prior investigation, not re-verified.** Produces 200K+ characters of real text — PDF-heavy, scraper's PDF fallback pulls linked PDF text successfully. | N/A | Sparsity, where it exists, is a chunking-granularity issue (Phase J), not JS-gating. | pre-established (not re-verified this session) |
| nums (numspak.edu.pk/admissions-details.php) | static-ok | Real rendered text incl. `>Fee Waiver Policy<`, `>Merit Lists<`, `>NUMS MDCAT - 2026...<`. Classic server-rendered PHP page. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| aku (aku.edu/admissions/mbbs/) | static-ok | Real `<p>` body paragraphs with substantive MBBS programme prose, plus `>Key Admissions Dates<`, `>Eligibility Criteria<`. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |
| bahria (bahria.edu.pk/Home/AdmissionRoadmap?programType=UnderGraduate) | static-ok | Real rendered text incl. `>Fee Structure Undergraduate<`, `>Admission Quota<`, a real server-rendered program table. | N/A | — | 2026-07-13 (evidence fetched 2026-07-10) |

## Summary

Of the 17 sources, only **`ist`** shows genuine evidence of JS-gating (empty server-rendered
shell, content only in Next.js RSC stream JSON). No underlying conventional API/JSON endpoint
was found to hit directly, so `ist` is the only source where a headless-browser (`render: js`)
fetch path is justified based on current evidence. All other 16 sources return real, literal,
server-rendered admissions text on a plain HTTP GET — a headless browser would add cost
(~100–300MB Chromium download per scheduled run) without benefit for those sources.

Two secondary, non-JS-gating observations, not blockers for this audit's verdict:
- `fast_nu`'s admissions.nu.edu.pk resolves to a login/registration form rather than an
  informational page — its sparse extraction is a content-availability gap, not rendering. Out
  of scope for this audit; worth flagging separately if extraction quality on fast_nu stays low.
- `air_university` (Islamabad & Punjab) uses Blazor scoped-CSS markup; current evidence shows
  real content server-rendered, but if this source's extraction quality is ever poor, worth a
  second look in case some sub-pages use Blazor WASM instead of Blazor Server.
