# Institution Registry

Human-readable companion to `config/institutions.yaml`, which is the source of truth all scraper/extraction code reads. If these two ever disagree, the YAML wins.

**All 15 sources below were verified against their live sites during registry-building.** This file describes *structure* (URLs, campuses, format) — it deliberately does not record deadlines or fees, which change frequently and are the scraper's job to capture at runtime.

15 distinct admitting portals cover the 16 KIPS-target institutions (CMH / Army Medical College / Federal Medical & Dental College fold under NUMS, their actual admitting body).

## Verified sources

| Institution | Canonical admissions URL | Format | Structure / notes |
|---|---|---|---|
| UET Lahore | admission.uet.edu.pk | HTML | **Corrected** to `admission.` (singular). Sub-campuses handled in-portal. Taxila is a separate portal. |
| UET Taxila | admissions.uettaxila.edu.pk | HTML | Separate chartered university, distinct source. |
| NUST | ugadmissions.nust.edu.pk | HTML | UG-only; PG on separate portal → clean URL split (rare). |
| GIKI | admissions.giki.edu.pk | HTML | Single campus (Topi). Graduate on separate portal. |
| PIEAS | admissions.pieas.edu.pk | HTML | Single campus. BS/MS/PhD on same site → content filtering needed. |
| FAST-NU | admissions.nu.edu.pk | HTML | One portal, 6 campuses, campus chosen in-application. |
| Punjab University | admissions.pu.edu.pk | HTML + PDF | **Corrected** to `admissions.` (plural). Notices/merit lists PDF-heavy on pu.edu.pk. **Trap: puchd.ac.in = India.** |
| COMSATS | admissions.comsats.edu.pk | HTML | **Corrected**: single unified system for all 7 campuses. Earlier per-subdomain model was wrong. |
| LUMS | admission.lums.edu.pk | HTML | Single campus (DHA Lahore). App portal at admissions.lums.edu.pk. |
| ITU | itu.edu.pk/admissions | HTML | **Corrected** app portal to admissions.itu.edu.pk/login. **Trap: itu.edu = US.** |
| IST | ist.edu.pk/admission | HTML | UG portal ugadmission.ist.edu.pk. Branch campuses (Karachi, Kahuta) exist; admissions centralized. |
| Air University (main) | portals.au.edu.pk/admissions | HTML + PDF | Islamabad ×2, Kamra, Multan, Kharian + MBBS. |
| Air University (Karachi) | kc.au.edu.pk | HTML + PDF | **Corrected** from unverified: genuinely distinct source (own schedule/email/site). |
| UHS | uhs.edu.pk/admissions.php | HTML + PDF | **Admitting body** for all Punjab public-sector medical/dental colleges. Separate MBBS/BDS portals. Merit lists PDF. |
| NUMS | numspak.edu.pk/admissions-details.php | HTML + PDF | **Corrected to +PDF. Admitting body** for constituent/affiliated/military colleges (CMH/AMC/etc.), centralized MDCAT. |
| AKU | aku.edu/admissions/mbbs | HTML + PDF | **Corrected to +PDF, campus-scoped.** Base aku.edu (no .pk). **Trap: also runs East Africa MBChB** — target Pakistan MBBS only. Own entry test. |
| Bahria University | bahria.edu.pk (AdmissionRoadmap) | HTML | Single system, campus as URL parameter. Includes Health Sciences (medical) campuses. **Trap: bui.edu.pk is legacy.** |

## Corrections made during verification (vs. the pre-verification draft)

- **UET**: canonical portal is `admission.uet.edu.pk` (singular), not `apply.`.
- **PU**: application portal is `admissions.pu.edu.pk` (plural), not `admission.`.
- **COMSATS**: it's ONE unified admission system, not 7 per-campus subdomain scrape targets — this was the most significant structural fix.
- **ITU**: current app portal is `admissions.itu.edu.pk`, not `application.itu.edu.pk`.
- **NUMS**: format is HTML+PDF (advertisements/merit lists are PDFs), not HTML-only.
- **AKU**: format is HTML+PDF and must be scoped to the Pakistan MBBS (the site also serves East Africa medical programs).
- **Air University Karachi**: promoted from "unverified guess" to a confirmed distinct source.

## Named traps (wrong-domain risks the scraper must avoid)

- `admissions.puchd.ac.in` → Panjab University **Chandigarh, India** (not PU Lahore).
- `itu.edu` (no `.pk`) → a **US** institution (not ITU Lahore).
- `bui.edu.pk` → **legacy** Bahria domain; use `bahria.edu.pk`.

## Structural patterns (schema in `CLAUDE.md`)

- **Single-URL, no campus split**: GIKI, PIEAS, LUMS, ITU, IST, AKU (single Pakistan MBBS).
- **Unified portal, campus chosen in-application (model as ONE source)**: NUST, FAST-NU, COMSATS, PU, Bahria, UET Lahore.
- **Genuinely separate campus sources (model as MULTIPLE sources)**: UET (Lahore vs Taxila), Air University (main vs Karachi).
- **Admitting-body (one portal allocates across many colleges; college is a per-record result)**: UHS, NUMS.

## What still can't be guaranteed

Verification confirms URLs resolve and page structure/format as of the check. It does **not** freeze deadlines or fees — those change through the admission season and must be read live by the scraper, never hardcoded here. That separation is the real accuracy safeguard.