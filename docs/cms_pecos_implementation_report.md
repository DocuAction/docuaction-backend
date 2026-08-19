# TEFCA Evidence Architecture — Implementation Report

Run date: 2026-08-19 · Dev only · **Not deployed to production**

---

## 1. Starting Git commit

`78d88d76567c09b1bf9ca709e3a03d969773da2d` (branch `main`, clean tree apart from
the untracked spec file).

## 2. Feature branch

`feature/tefca-cms-pecos-evidence`

## 3. Original test baseline

Measured before any code change: **1003 passed, 29 skipped, 0 failed** (639.22s).

The 29 skips are environmental and pre-existing — no local Postgres reachable
(`DATABASE_URL`), `BULLETIN_AUTH_ENABLED` off, and one live-run test needing
`DEMO_EMAIL`/`DEMO_PASSWORD`. Note the brief said 1012 tests; the measured
collection is 1032 (1003 + 29). The measured figure is what is used here.

## 4. Files and components changed

**New (8):**

| File | Lines | Purpose |
|---|---|---|
| `app/Tefca/cms_ppef.py` | 643 | CMS PPEF + Revocation clients, PPEF component model, capability health |
| `app/Tefca/evidence_dimensions.py` | 249 | Six dimensions, disposition vocabulary, evidence item/provenance types |
| `app/Tefca/applicability.py` | 338 | Applicability engine |
| `app/Tefca/address_evidence.py` | 282 | D4 address reconciliation |
| `app/Tefca/evidence_assembly.py` | 820 | Dimension assembly — where the spec rules become code |
| `app/Tefca/evidence_service.py` | 286 | Orchestration, website corroboration, persistence flattening |
| `alembic/versions/20260819_tefca_dimension_evidence.py` | 84 | Migration |
| `tests/test_cms_pecos_evidence.py` | 854 | 64 tests |

**Modified (2), additively — 267 insertions, 0 deletions:**

* `app/Tefca/models.py` — added `TEFCADimensionEvidence`.
* `app/Tefca/routes.py` — added 4 endpoints.

**Frontend (3):**

* `src/app/tefca-arc/components/EvidenceDimensions.js` — new.
* `src/app/tefca-arc/components/TefcaReviewWorkspace.js` — renders it alongside the existing ledger.
* `src/app/tefca-arc/connectors/page.js` — corrected PECOS entry, added CMS Revocation, added CMS capability panel.

**Untouched, verified by `git status`:** `app/tefca_registry/ai/` (AI Control
Plane) and `app/bulletin_intelligence/` — 0 changes. No existing test file was
modified, deleted, weakened or skipped.

## 5. Database migrations

One: `20260819_dim_evidence`, down-revision `20260817_audit_fields`. Chain
validated — single head, linear. Creates `tefca_dimension_evidence` and six
indexes. **No existing table is altered, dropped or backfilled.**

## 6. CMS datasets and endpoints actually used

| Purpose | Endpoint | Auth |
|---|---|---|
| PPEF Enrollment | `data.cms.gov/data-api/v1/dataset/2457ea29-fc82-48b0-86ec-3b0755de7515/data` | keyless |
| Revoked Providers | `data.cms.gov/data-api/v1/dataset/a6496a7d-4e19-479a-a9ad-d4c0a49e07c3/data` | keyless |

Query contract verified live: `filter[FIELD]=value`, `size` + `offset` paging,
`/data/stats` for counts. Enrollment holds 2,978,925 rows.

## 7. CMS fields and relational keys actually used

Enrollment (all 11, verified): `NPI`, `MULTIPLE_NPI_FLAG`, `PECOS_ASCT_CNTL_ID`,
`ENRLMT_ID`, `PROVIDER_TYPE_CD`, `PROVIDER_TYPE_DESC`, `STATE_CD`, `FIRST_NAME`,
`MDL_NAME`, `LAST_NAME`, `ORG_NAME`.

Revocation (all 12, verified): `ENRLMT_ID`, `NPI`, `FIRST_NAME`, `MDL_NAME`,
`LAST_NAME`, `ORG_NAME`, `MULTIPLE_NPI_FLAG`, `STATE_CD`, `PROVIDER_TYPE_DESC`,
`REVOCATION_RSN`, `REVOCATION_EFCTV_DT`, `REENROLLMENT_BAR_EXPRTN_DT`.

Keys: `ENROLLMENT.ENRLMT_ID` as the join anchor; `REASGN_BNFT_ENRLMT_ID` and
`RCV_BNFT_ENRLMT_ID` for reassignment.

## 8. PPEF relational model implemented — and the gap found

Implemented as one relational source with `ENRLMT_ID` linkage, per Amendment 3.

**Only the Enrollment component is published by CMS.** Established by three
independent checks:

1. The DCAT catalogue carries a single PPEF entry whose five distributions are
   three API versions and two CSVs of the *same* Enrollment extract
   (`PPEF_Enrollment_Extract_2026.07.17.csv`, `...2026.04.01.csv`).
2. All three API distributions probed — identical 11-field Enrollment schema.
3. Catalogue-wide search for `REASGN` / `PRACTICE_LOCATION` / `ADDITIONAL_NPI`
   matched only the **Revalidation** datasets, which the spec forbids
   substituting.

Practice Location, Reassignment, Additional NPIs and Secondary Specialty
therefore return `UNAVAILABLE` with reason
`ppef_component_not_published_via_cms_data_api`. No joins were invented, no
data fabricated, and the absence never becomes a failure. The relational code
(fan-out over enrolment ids, one-to-many collection, the Amendment 5 hop from
`RCV_BNFT_ENRLMT_ID` back to a receiving organisation) is implemented and
tested; one entry in `PPEF_COMPONENT_DATASETS` turns each component on.

## 9. ADDITIONAL_NPIS handling (Amendment 2)

When `MULTIPLE_NPI_FLAG = Y` and the PPEF NPI differs from the RCE NPI, the
result is `UNRESOLVED_MULTIPLE_NPI` with `rule_applied =
AMENDMENT_2_MULTIPLE_NPI_FLAG` — **never** `CONFLICT`. PECOS contributes no
identity conflict at all (`field_conflicts` is empty by construction on that
item). Preserved: primary PECOS NPI, RCE NPI, NPPES result, `ENRLMT_ID`, PAC ID
(`PECOS_ASCT_CNTL_ID`), and the matching relationship. Multiple NPIs are one
body of PECOS evidence, never multiple votes.

## 10. ONC/HHS/RCE fields discovered

Present 30/30: `resourceType`, `id`, `identifier[]` (us-npi + TEFCA identifier),
`active`, `type[]` (QHIN/PARTICIPANT/SUBPARTICIPANT), `name`, `telecom[]`,
`address[]`, `partOf`, `_qhin`. `alias[]` 4/30, `meta` 1/30.

**Not supplied:** HCID, Exchange Purpose, NPI Type 1/Type 2 marker, provider or
entity type. Reported as `NOT_SUPPLIED_BY_ONC`; never inferred, never derived
from PECOS. Full mapping table in `docs/tefca_evidence_dimension_mapping.md`.

## 11. Evidence-dimension mapping

See `docs/tefca_evidence_dimension_mapping.md` §1 — ONC field → semantic meaning
→ dimension → authoritative source → external corroboration.

## 12. Applicability rules implemented

Inputs: ONC TEFCA class, NPPES enumeration type (Type 1/Type 2), NPPES taxonomy,
Medicare relevance, methodology overrides, and evidence available for the review
(PECOS presence beats a taxonomy guess).

Three states, not two: `REQUIRED`, `CORROBORATIVE`, `NOT_APPLICABLE`. Because
ONC supplies no provider type, Medicare relevance can be `UNDETERMINED` — which
resolves to `CORROBORATIVE`, never to a requirement. Corroborative evidence can
never fail an entity.

Categories: Individual Provider, Provider Organization, Public Health Agency,
Payer, HIE/HIN/QHIN, Unknown. Payer / Public Health / QHIN → PECOS
`NOT_APPLICABLE` unless evidence establishes relevance. There is no
"Hospital ⇒ all sources mandatory" rule, and a test asserts its absence.

## 13. Reassignment logic

RCE relationship is primary; PECOS reassignment corroborates only. Agreement →
`CORROBORATED`; RCE present with no reassignment → `NOT_APPLICABLE` or `REVIEW`
by Medicare applicability; different organisations → `REVIEW` with all
organisations presented; non-provider entity → `NOT_APPLICABLE`. No path
produces `FAIL`, and no path produces a score.

## 14. Address reconciliation

Hierarchy ONC → NPPES → PECOS Practice Location → USPS → website (supplemental).
Each source row stores SOURCE, ORIGINAL, NORMALIZED, MATCH RESULT, RETRIEVAL
TIME, DATASET ANCHOR. The ONC value is row one and is never replaced. Results:
MATCH / PARTIAL_MATCH / CONFLICT / NOT_FOUND / UNAVAILABLE; CONFLICT is `REVIEW`,
never `FAIL`. Multiple practice locations each get their own row.

Reuses the shipped `USPSNormalizer`. One gap found and fixed **in the D4 layer**:
that normaliser tokenises `21201` and `21201-0000` as different, so addresses
differing only in ZIP+4 scored as non-matches. Comparison now canonicalises
ZIP+4 → ZIP5; the shared normaliser is untouched so its other callers and tests
are unaffected.

## 15. Website fallback behaviour

Supplemental only, opt-in per review (`include_website`, default off). DNS
failure, timeout, 403, 429, 5xx, SSL and anti-bot all → `UNAVAILABLE`,
`affects_determination: false`. No website supplied → `NOT_FOUND`, same. No URL
is guessed from an email domain or organisation name. Results are never `PASS`
or `FAIL`.

## 16. Revocation negative-result semantics (Amendment 1)

Negative lookup returns exactly `NO_ACTIVE_REVOCATION_RECORD_FOUND`, carried with
a scope note stating it is not evidence of enrolment, eligibility, or good
standing. Enrolment is answered separately by PPEF. A positive match is `REVIEW`
pending identity matching — never automatic rejection. All Amendment-1 fields
captured. Verified live: NPI 1801839063 returns 5 revocation rows (one-to-many is
real, which is why no code takes row [0]).

## 17. Point-in-time evidence storage (Amendment 6)

Every lookup records `query_timestamp` plus `dataset_version_anchor` — the CMS
dataset UUID, which CMS re-mints per quarterly release and which therefore pins
the exact publication. `update_cadence: "quarterly"` and `realtime: false` travel
with every CMS item; the data is never labelled real-time. The HTTP
`Last-Modified` header is stored separately and labelled transport metadata, not
an as-of date. Evidence is append-only: a re-run inserts a new
`generation_timestamp`, never updating or deleting a prior generation.

## 18. UI changes

* **Decision Workspace** — new "Evidence by Dimension" panel in the existing
  evidence slot, alongside (not replacing) the source ledger. Six dimensions in
  the specified order, each with disposition, plain-language gloss, rationale,
  and per-source drill-down showing fields checked, matches, conflicts,
  retrieval timestamp, dataset anchor, cadence and rule applied.
* **Connector Hub** — the misleading "PECOS — Provider Enrollment (NPPES proxy)"
  entry now reads "CMS / PECOS Public Provider Enrollment" with its components
  as capabilities; CMS Revoked Providers is a separate entry; a CMS Systems panel
  shows capability-level AVAILABLE / DEGRADED / UNAVAILABLE.
* **Accessibility** — status is text first, badge second (never colour alone);
  drill-down uses native `<details>`/`<summary>` for keyboard operability;
  NOT_APPLICABLE and UNAVAILABLE are spelled out rather than shown as blanks.

No page, route or workflow was restructured.

## 19. Connector and health changes

New `GET /api/tefca/connectors/cms-systems` reports two systems — CMS PPEF (five
capabilities) and CMS Revocation — with AVAILABLE / DEGRADED / UNAVAILABLE. An
unpublished component is DEGRADED, not UNAVAILABLE: the system answers, one
capability is not offered. The response states explicitly that CMS availability
never affects an entity determination. `/api/tefca/connectors/status` is
unchanged.

## 20. Audit changes

The existing audit trail is extended, not replaced. `log_tefca_event` fires on
every evidence generation. `tefca_dimension_evidence` stores per item: entity,
review, dimension, source, dataset, PPEF component, source record id, query id,
query timestamp, dataset anchor, disposition, fields evaluated, field matches,
field conflicts, original values, normalised values, rule applied, note,
retrieved-at, generation timestamp, and analyst annotation fields. New endpoint
`GET /api/tefca/entities/{ref}/evidence-history` returns every preserved
generation.

## 21. New tests

64, all passing, **no network** (injected fake CMS client), 0.75s. Coverage:
enrolment match / non-match / multi-record; unavailable, timeout, malformed,
rate-limited; revocation match, negative semantics, multiple matches, three
controls separately identifiable; practice-location linkage, multiple locations,
missing location; reassignment linkage, multiple reassignments, not-applicable,
conflicting RCE/PECOS organisations; Type 1/Type 2 alignment and divergence;
multiple NPI and `MULTIPLE_NPI_FLAG=Y`; address normalisation, partial, conflict,
never-overwrite, supplemental exclusion; website unreachable/403/429/5xx/absent;
provenance completeness; generation preservation; applicability per entity type
including methodology override; and structural invariants — no score anywhere,
six dimensions always present and ordered, PPEF never presented as separate
systems, and **no dimension is ever auto-failed**.

## 22. Complete regression results

**1067 passed, 29 skipped, 0 failed** (713.27s) = baseline 1003 + 64 new, with
the identical 29 pre-existing skips. Zero regressions.

One precision note: the full-suite run collected test files at start, and one
new-file test was strengthened after that run began. That file was re-run at
final state — 64/64 passing. All other 56 files ran at final state in the
full-suite run.

## 23. Performance impact

Test suite 639s → 713s. The 64 new tests contribute 0.75s; the remainder is
run-to-run variance in the existing network-dependent tests, not new cost.

Runtime per entity: NPPES, OIG, SAM, PPEF Enrollment and Revocation issue
concurrently; the three relational components short-circuit without network
while unpublished. PPEF paging is 100 rows/request, capped at 200 records per
lookup with a `records_truncated` flag rather than a silent cut.

## 24. Security and privacy findings

* No API key, credential or BAA is required for either CMS dataset; both are
  public domain.
* Only NPI — a public identifier — is transmitted to CMS. No PHI, no patient
  data, no internal identifiers leave the system.
* All four new endpoints are RBAC-gated at `viewer` and return 401 unauthenticated
  (verified).
* Website corroboration is off by default; when enabled it fetches only a URL ONC
  supplied, never a guessed one.
* No new secret, token or credential is introduced or logged.

## 25. Unresolved TIN/EIN issue

Out of scope and **confirmed absent from the data**: the verified 11-field
Enrollment schema and 12-field Revocation schema contain no EIN or TIN. Nothing
in this implementation scrapes, derives, infers, stores or exposes a tax
identifier, and no code path produces one. Any future tax-identifier
corroboration needs a different authorised source and a separate privacy review.

## 26. Warnings and regressions

* **No regressions.** Zero test failures; no existing test altered.
* **Warning — the PPEF gap is the headline.** Four of the five relational
  components the spec assumes are not published by CMS today. Address
  corroboration from PECOS, reassignment corroboration, and ADDITIONAL_NPIS
  resolution are therefore all `UNAVAILABLE` in practice. The code is complete
  and tested for them; the data is not there.
* **Warning — pre-existing misnomer.** `PECOSConnector` in `connectors.py`
  queries **NPPES**, not PECOS, and feeds `ValidationEngine.REQUIRED_SOURCE_KEYS`
  as `pecos`. It was left untouched to avoid changing B1–B4 behaviour, so the
  legacy `pecos` key and the new `cms_ppef_enrollment` key mean different things.
  Renaming it is a data migration (the identifier appears in stored rows), not an
  edit — flagged for a decision.
* **Note.** `datetime.utcnow()` deprecation warnings follow the existing codebase
  convention; not changed unilaterally.
* Migration not yet applied to any database — no dev deployment was performed.

## 27. Recommendations for next enhancement

1. **Decide on the legacy `pecos` key.** Either rename it to `nppes_enrollment`
   with a data migration, or repoint `ValidationEngine` at
   `cms_ppef_enrollment`. Today one name means two things.
2. **Ingest the PPEF quarterly CSV package** if CMS continues not to serve the
   relational files. That unlocks Practice Location, Reassignment and Additional
   NPIs with no change to the evidence layer — only a loader and one mapping
   entry per component.
3. **Analyst annotation endpoint** for `analyst_notes` / `reviewed_by` on
   dimension evidence rows; the columns and audit path exist, the write endpoint
   does not.
4. **Wire dimension evidence into reports** so the exported deliverable carries
   the same provenance the workspace shows.
5. **Watch the CMS dataset UUID** — a new quarterly release mints a new UUID, and
   pinning is currently by constant. A resolver that discovers the current UUID
   from the catalogue, while keeping historical anchors intact, would prevent
   silent staleness.

---

**STOPPED. No production deployment. Awaiting approval.**
