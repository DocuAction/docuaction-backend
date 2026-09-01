# TEFCA ARC — MASTER IMPLEMENTATION STATUS

> ## INTERNAL AGT — NOT FOR CLIENT DISTRIBUTION
> **No Government row-level values.** Aggregate figures only.

**Contract:** 7571MN26F80064 · HHS/ONC ASTP · **As at:** 2026-08-30
**Branch:** `fix/reconciliation-uuid-bind` · **HEAD:** `fdc99c7`
**Alembic head (repo and DEV):** `20260831_review_case`
**Working tree:** uncommitted. Nothing has been committed, pushed or deployed.

---

## Frozen architecture

```
ONC/RCE delivery -> controlled intake -> immutable Area 1 -> data quality
  -> controlled curation -> canonical TEFCA registry -> authoritative verification
  -> evidence -> HUMAN_REQUIRED review cases -> assignment / claim
  -> analyst determination -> independent QA -> approved ARC state
  -> Government reporting / controlled export -> ongoing delivery processing
```

A change to this requires a contract requirement, a proven correctness or
security defect, a Government methodology decision, or authoritative evidence
invalidating a current assumption. Engineering preference is not sufficient.

---

## Master plan

| # | Step | Status |
|---|---|---|
| 1 | Contract / SOW baseline | **FROZEN COMPLETE** |
| 2 | 41-column source mapping | **FROZEN COMPLETE** |
| 3 | Controlled intake / immutable source | **FROZEN COMPLETE** |
| 4 | DQ / applicability / curation | **FROZEN COMPLETE** |
| 5 | Canonical TEFCA registry | **FROZEN COMPLETE** |
| 6 | Verification / evidence | **FROZEN COMPLETE** |
| 7 | DQ current-run / idempotency | **FROZEN COMPLETE** |
| 8 | HUMAN_REQUIRED review bridge | **FROZEN COMPLETE** |
| 9 | Human workflow schema | **FROZEN PASS** |
| 10 | Analyst / QA operational workflow | **FROZEN PASS** |
| 11 | 41-field processing coverage | **PASS** (this pass) |
| 12 | Monthly delivery delta | **PASS** |
| 13 | Sampling operationalization | **PASS** - statistical core + operational plan path certified |
| 14 | Priority review operationalization | **PASS** |
| 15 | Supervisor operations & workload control | **PASS** |
| 16 | Federal enterprise UI/UX professionalization | **PASS** (closed by #16B) |
| 17 | Controlled Excel export + Government data review workbook | **Complete** |
| 17C | Export operationalization: background job, provenance, artifact security | **Complete** |
| 18 | Production security + Azure infrastructure + resilience readiness | **PARTIAL** |
| 18A | Azure production readiness closure attempt | **PARTIAL** — gate did not open |
| 18B | Final DEV closure + synthetic E2E acceptance | **PASS — DEV FROZEN** |
| 18-22 | Reporting QA, training, security/508, certification, PROD rehearsal, PROD ingestion | Future |

---

## Step #11 result

41 fields assessed from code, schema and tests:

**18 IMPLEMENTED · 15 NO AUTOMATED RULE REQUIRED · 8 ONC METHODOLOGY
CONFIRMATION · 0 PARTIALLY IMPLEMENTED · 0 EXTERNAL SOURCE DEPENDENCY ·
0 MISSING.**

One genuine deterministic gap was found and closed: **FMT-007
`ORG_PHONE_FRAGMENT`**, the counterpart of the existing FMT-005 on
`contact_phone`. Rule set is now **1.1.0** (32 rules). Read-only forecast: it
raises **0** findings on the delivered population — the asymmetry was real, the
data has not yet exercised it.

Two fields previously counted as uncovered were found to be covered
(`organizationNodeType` by CON-004, `hl7orgrole` by BUS-001); the earlier count
was taken from `field_map.py` association tuples rather than from the rule
functions themselves.

Detail: `docs/TEFCA_41_Field_Processing_Matrix_INTERNAL.md`.

---

## Controlled Government baseline — verified unchanged

| Measure | Value |
|---|---:|
| Source records | 23,566 |
| Source columns | 41 |
| DQ issues | 36,916 |
| — AUTO_SAFE | 1,631 |
| — HUMAN_REQUIRED | 138 |
| — NO_CORRECTION | 35,147 |
| Promoted | 23,562 |
| HELD | 4 |
| Historical review records | 43 |
| Operational Government review cases | **0** |
| Area 1 corpus digest | `3af240c30035b17d5d669a2f8ddbd33a` |
| — digest recipe | `md5(string_agg(record_sha256, '' ORDER BY id))` over `rce_source_records` — recorded here because the value had been quoted across four passes with no recipe written down, which made it unverifiable by anyone but its author |

---

## Open Government decisions

1. **TEFCAID family/group semantics** — 43 values shared across 284 records.
   Uniqueness is not imposed.
2. **Direct-QHIN parentage** — 15 Subparticipants name a QHIN in `partOf` rather
   than an intermediate Participant. Not treated as adverse.
3. **Test-pattern records** — 9 records match test-name patterns. Retained;
   exclusion rule and authority undecided.
4. **NPI applicability** — 4,584 records (19.5%) carry none. Absence alone is
   never adverse.
5. **EIN/FEIN/TIN authority** — not among the 41 delivered fields. Recorded as
   `PENDING_GOVERNMENT_VERIFICATION`; never PASS, FAIL or NO_MATCH.
6. **Contact-address scope** — should the ARC evaluate the `contact_address_*`
   block? 6,978 records carry a short postal value. Not implemented pending an
   answer.
7. **Government data authorization authority** — *raised Step #17C.*

   **Who has authority to designate a verified ONC/RCE delivery as authorized
   for official Government operational and reporting use in DocuAction?**

   The system already requires an explicit authorization marker on the intake
   before it will classify anything as GOVERNMENT, and the delivered snapshot
   does not carry one. It is therefore classified DEVELOPMENT_TEST despite being
   the Government delivery by every visible measure — the delivered filename,
   the clean Area-1 SHA-256, 23,566 records, the expected schema fingerprint,
   parsed successfully. That gap is the control working, not a defect.

   Engineering may determine that a dataset IS the Government delivery.
   Engineering may not manufacture the authorization that makes it official, and
   has not: no application code sets the marker, and a test in
   `tests/test_classification_matrix.py` fails if reporting or registry code
   ever references it.

   | | |
   |---|---|
   | Marker exists in schema? | Yes — `rce_source_intakes.source_metadata.government_authorized` |
   | Marker currently set? | **No.** Absent on the only intake |
   | Who may set it? | **Undefined** |
   | Is authority defined? | **No** |
   | Operational workflow available? | **No** — there is no UI, endpoint or documented procedure |
   | Contract documentation defines it? | **Not found** |

   Options for the decision owner — **not chosen by engineering**: ONC/COR
   approval; the Government delivery receipt itself; AGT Program Manager under
   an approved SOP; a controlled intake acceptance workflow; another
   contract-authorized mechanism.

   **ONC / PROGRAM GOVERNANCE DECISION REQUIRED.** Until it is answered, no
   official Government export can be produced, which is the correct behaviour
   rather than a blocker to route around.

---

## Technical debt

| Item | Status |
|---|---|
| `test_ppef_jobs::test_partial_unique_index_refuses_a_second_active_job` fails on this host — uses the shared global engine that `conftest._use_null_pool()` breaks, so the PPEF concurrency guard is unverified here | **RECORDED, NOT TOUCHED** |
| Two `test_area1_controls` tests expect a foreign-key error where the hardened database raises `InsufficientPrivilegeError` first | Recorded across five gates |
| `20260830_run_lifecycle` cannot apply on a single-role developer box (grant-only, fails closed). DEV was stamped past it after verifying it is a no-op there | Recorded; applies normally where `docuaction_app` exists |
| No UI is wired to the proven review/assignment backend | Recorded |
| `rce_issues.review_id` would make issue→case lookup an index hit rather than a JSONB scan | Optional |

---

## Step #12 result

**PASS.** Delivery-to-delivery comparison is **DERIVED** from append-only Area 1
inputs - no new table, no migration. Certified on synthetic deliveries across 24
tests: NEW / CHANGED / UNCHANGED / NOT_PRESENT_IN_CURRENT_DELIVERY, with HELD
reported orthogonally. The first delivery is BASELINE_DELIVERY, not "all NEW";
absence is never removal; duplicate identity, schema mismatch and out-of-order
pairs are refused rather than guessed. Government readiness assessed read-only:
**YES** - architecture only, not authorization to ingest.

Detail: `docs/TEFCA_Monthly_Delivery_Delta_Model_INTERNAL.md`.

## Step #13 result

**PASS.** Statistics (#13) and operational wiring (#13B) are both certified.
**65 tests**, both parts mutation-tested.

**The statistics.** `draw_per_stratum` sizes **each QHIN against its own
population** using the unchanged Cochran + FPC formula, with per-stratum RNG,
census disclosure for strata too small to sample, reproducible seeds and full
parameter capture.

**The defect it closed.** Stratified `draw_sample` computed ONE national sample
size and allocated it proportionally, giving the 3-record QHIN **zero** selected
records while the total read as a 95% sample. Per-QHIN sizing gives 1,967
against 379 nationally.

**The operational path (#13B).** `app/tefca_registry/qhin_sampling.py` wires
delivery -> eligible population -> canonical `managed_by_qhin` resolver ->
`draw_per_stratum` -> frozen `ReviewSample`/`SampleEntity` -> the existing
review, assignment and QA workflow. It contains no formula. Finalisation is
idempotent by plan key and concurrency-safe under an advisory lock with
`uq_sample_entity` beneath it; reads never redraw; there is no parameter by
which a caller could choose members. **No migration was required.**
`review_routes POST /samples` is untouched and is not the official per-QHIN
path; a test asserts the official path never calls `draw_sample`.

**A limit this exposed and did not paper over.** `sample_entities.entity_id` is
NOT NULL, so an unpromoted record cannot be a sampling unit. All four HELD
records are also unpromoted, so `include_held` cannot change the frame on this
delivery. The resolver now REPORTS those records as unresolved instead of
filtering them out of the query: 23,562 eligible + 4 unresolved = 23,566
delivered. Whether HELD should be sampleable remains an open ONC question, and
nothing was promoted to make it so.

Government forecast (read only, nothing created), re-run through the new
resolver and reproducing #13 exactly: 11 strata, 23,562 eligible, **1,967**
total calculated sample, 0 unresolvable QHIN assignments, 1 census stratum.
**`review_samples` 0 · `sample_entities` 0 · sampling audit rows 0.**

Detail: `docs/TEFCA_Per_QHIN_Sampling_Model_INTERNAL.md`.

## Step #14 result

**PASS.** Task 5 priority reviews now run on the certified human-review
workflow. **63 tests**, 10 mutations all detected. **No new table, no
migration.**

**What the contract settles, and the code now honours.** ¶146 sets the deadline
**per request, by the COR**, so there is no standing turnaround and nothing in
this implementation computes one — a test scans the module's compiled source for
any 24-hour, one-day or SLA-derived default. The separate one-hour rule is
incident reporting and is not applied. ¶146 also says the COR names the
entities, so no rule, severity or sample can manufacture a request.

**What was actually missing.** `tefca_priority_cases`, its routes and its D5.1
report have existed since June and hold 0 rows. What they lacked was the
maker-checker chain: `PATCH /priority-cases/{id}` let one `senior_analyst` set
root cause, severity and resolution in a single call, and
`execute_priority_review` derived a root cause and severity automatically — in
one branch by parsing the issue text. Neither produced a determination event,
neither passed independent QA, and the report printed the result either way.

**What was built.** `app/tefca_registry/priority_review.py` binds an authorized
COR request to `review_records` → `case_assignment` → `review_decision_events`
→ `reportable_at`, with canonical target resolution, controlled
RESOLVED/AMBIGUOUS/NOT_FOUND states that preserve candidates, append-only
deadline history, idempotency on (COR reference, target) and an advisory lock
for concurrent submission. The D5.1 report withholds every determination field
until a QA approval stands. `Tefca.entity_resolution` gained
`resolve_reference_detail` — the same ladder, now able to report ambiguity —
with `resolve_from_db` delegating to it and its 27 tests unchanged.

**An unpromoted or HELD organisation is reviewable.** Statistical sampling
cannot reach one (`sample_entities.entity_id` is NOT NULL); a priority case
anchors to the delivered Area 1 line instead. Nothing was promoted.

**UI.** The Entity Review side panel — the same analyst surface — gained a
keyboard-operable, `aria-expanded` Expand/Collapse control. The frontend has no
automated accessibility harness, so this is a structural verification and the
production build passes; **manual and Government 508 review is still required.**

**Government readiness (read only, nothing created):** priority requests **0**,
priority cases **0**, assignments **0**, decisions **0**, QA events **0**,
reports **0**. 23,756 registry entities resolvable, 23,562 QHIN edges, 4 HELD
records reviewable pre-promotion, D5.1 and D5.2 templates present.

**Recorded, not fixed (Step #14 does not depend on them):** the legacy
ungated priority surface still exists with 0 rows; `sla.py` keeps a
non-contractual 3-day "priority" window for the SAMPLED-review dashboard, and a
test pins that the Task 5 path never uses it.

Detail: `docs/TEFCA_Priority_Review_Operational_Model_INTERNAL.md`.

## Step #15 result

**PASS.** A supervisor control plane over every ARC work source — data quality
exceptions, statistical sample members and COR priority requests — in one
queue, with the reason each case exists, who holds it and where it stands.
**50 tests**, 12/12 mutations detected. **No new table, no migration, no
cache.**

**Derived, never stored.** Every figure is computed at read time from
`review_records`, `review_decision_events`, `sample_entities`, the priority
requests and the recorded verifications. State uses the SAME ladder
`case_assignment.case_state` uses, and a test asserts the two agree case by
case. A control plane holding its own copy of the workload would be a second
source of truth.

**Management authority is not review authority.** Nothing in the module can
record a determination, approve a QA review or set reportability; every
`/operations/*` route is GET-only; and a principal holding both analyst and QA
roles still cannot approve their own determination. The one write a supervisor
owns — assignment — stays in `case_assignment`.

**Two real defects found in Step #10's `assign`, both fixed.** It was a
read-modify-write, so two supervisors assigning one case both succeeded, one
assignment was silently lost, and **both** audit rows claimed the case came from
nobody; it now uses the same conditional `UPDATE … RETURNING` that `claim` and
`release` have always used. And it took a case off a live holder with nothing
recorded, so a handover looked identical to an unclaimed case; taking live work
now requires a stated `override_reason`. Assigning unheld work still requires
nothing.

**Nothing invented.** A deadline exists only where the COR supplied one;
`DUE_SOON` and `stale_after_days` have no defaults; `PAST_DUE` always carries
`compliance_conclusion: null`; an undrawn sampling plan reports
`NOT_YET_CREATED` with no figure at all, never "0% complete"; and workload
counts carry no score, rank or throughput.

**UI.** `/tefca-arc/operations`, composed from existing platform components and
the shared SidePanel from Step #14. No automated accessibility harness exists,
so this is a structural verification and the production build passes; **manual
and Government 508 review is still required.**

**Government read-only forecast (nothing created):** 138 DQ HUMAN_REQUIRED
findings · **0** operational DQ review cases · 43 historical review records, all
unassigned and carrying no recorded work reason (they predate the provenance
convention) · **0** assignments, decisions, samples, priority cases and reports.
A HUMAN_REQUIRED finding is not an analyst case, and the difference is reported
rather than closed.

**Assessed and deliberately not built:** bulk assignment (individual assignment
with a stated reason is sufficient at this volume and safer), export (deferred),
and QHIN-scoped authorization (the contract establishes no per-QHIN tenancy).

Detail: `docs/TEFCA_Supervisor_Operations_Model_INTERNAL.md`.

## Step #16 result

**PARTIAL.** The named PROD defects are fixed and verified in the running
application; the broader component standardization is not done, and the gate's
own acceptance criteria say that is not a PASS.

**Fixed and verified rendered.** The Entity Review panel now offers Compact
(480px) / Expanded (840px) / Full screen (1600px), measured; the six-column
status grid that broke "Unassigned" and "Manual review required" across lines is
gone; evidence is structured as source · authority · result · detail with the
result stated **in words**; the internal Sequoia case number, the ODC
procurement note, an environment variable name and raw database column
identifiers are all gone from Government screens; the module has a shared
footer with the Alliance Global Tech copyright; ~25 developer-vocabulary strings
across 11 pages were rewritten without hiding a single failure; and the KPI
value box no longer clips the platform's own "Awaiting Data" placeholder.

**Two truth corrections.** The dashboard's `SLA Compliance — 0 breaches` claimed
performance against a service level the contract does not set; it now reads
"Priority reviews within target · not a contractual finding", with the numbers
unchanged. And "RCE Directory · MOCK · Awaiting API Key" described the
ONC-delivered entity population as a demonstration connector AGT is waiting on;
the backend connector states plainly that the population is provided by ONC and
that AGT queries no external directory for it.

**No new UI framework.** The platform already has a Fluent-derived token system
with its own measured accessibility amendments. This pass found departures from
it; it did not build a second one.

**Nothing was made to look better than it is.** No MOCK became LIVE — the
module-wide demonstration banner is correct, because the ARC intake carries no
Government authorization marker, and setting one would be both a Government
write and the deception the gate forbids. Connector count moved 7 → 5 and mock
2 → 0 only because two entries that were never connectors stopped being shown as
broken ones.

**Guardrails:** `frontend/scripts/ui-guardrails.mjs` — 20 checks, plain Node, no
framework added; 10/10 mutations detected, code restored byte-identically.
**No backend file was changed**; all 26 Government integrity anchors match.

**Not done, and why this is PARTIAL:** tables, filters and buttons are not
standardized across all pages; dashboard density was not reworked; the
empty-value sweep covers the new work only; responsive and zoom were verified at
one viewport; no automated accessibility tooling exists and manual 508 remains
outstanding.

Detail: `docs/TEFCA_UI_UX_Professionalization_INTERNAL.md` ·
Inventory: `docs/TEFCA_UI_UX_Inventory_INTERNAL.md`.

## Step #16B result — closure

**#16 is now PASS.** The nine items left open by the #16 PARTIAL resolve as six
closed, two corrected as mis-diagnoses, and one closed as already-done.

**Closed.** Tables gained controlled horizontal scroll (a `min-width` floor, so
a wide table scrolls instead of squeezing its columns to one word per line), a
pager border a low-vision reader can see, and a caption that tells a keyboard
user the rows are actionable. Filters: 13 pages already shared `FilterBar`; the
one genuine outlier — the server-side Operations queue — now uses it too, and
filter labels moved from the shouted eyebrow to sentence case in the component,
fixing all fourteen at once. Buttons: the two competing primaries on Entity
Review are resolved, with the contextual one stepping down. Empty values:
`present()` at 47 sites, which also stopped `x || '—'` suppressing a legitimate
zero, and two raw enum renders now read as language. Typography: a new
`TYPE.fieldLabel` for form fields, with uppercase retained for eyebrows, table
headers and badges.

**Two corrections of record.** The "five duplicated component families" are not
duplicates — each module file is an adapter over the platform component with
**zero styling declarations**, so none can drift. And the focus-restoration
defect did not exist: the row is focusable, and #16's observation was an
artefact of opening the panel with a synthetic click. Verified keyboard-only
this time: focus the row, Enter, Escape, **focus returns to the row**.

**A real defect found underneath it.** Eleven pages passed no row label, so a
screen-reader user tabbing into a focusable row was told nothing about being
able to open it. Fixed once, in the component.

**And one found during the gate: the Supervisor Operations page was unreachable
for every non-admin.** Step #15 added the navigation entry and the route but not
the id to `ALWAYS_ALLOWED`, so the entry rendered for nobody and a direct visit
answered "Access restricted". #16's guardrail checked the nav array and passed,
which is why it survived a whole gate. The guardrail now cross-checks the
allow-list, and the fix is verified rendered.

**Responsive, observed rather than inferred.** The Chrome window could not be
resized reliably, so each viewport was rendered in a same-origin iframe of an
exact CSS size — a real CSS viewport with media queries evaluating against it.
Clean at 1920, 1536, 1440, 1280, 960 and 768: no page horizontal scroll, no
broken wrapping, navigation and footer present, wide tables scrolling inside
their own container.

**Two items remain open and are recorded, not closed.** 320px reflow fails, and
the cause is measured: the platform sidebar takes 192px, leaving 113px of
content — a shared-shell change outside a TEFCA UI gate. And automated
accessibility tooling was reassessed and still not added, because the frontend
has no runner to integrate one with and adding the stack is the framework
rewrite the gate forbids. Manual accessibility testing was performed; **Section
508 certification remains a separate Government activity and is not claimed.**

**Guardrails: 43 checks, 19/19 mutations detected**, code restored
byte-identically. **No backend code changed. All 26 Government anchors match.**

Detail: `docs/TEFCA_UI_UX_Professionalization_INTERNAL.md` (Step #16B closure).

## Step #17 result — controlled Excel export

**PASS.** A ten-sheet controlled export, built on the existing artifact store and
registry rather than beside them, certified on a synthetic delivery and
forecast — read-only — against the delivered population.

**The architecture rule is the whole design.** DocuAction is the system of
record; the workbook is an export. It is a snapshot taken under a classification
the caller cannot choose, stored content-addressed, registered with its own
hash, and downloaded through the path that re-hashes before serving. Nothing
typed into the spreadsheet comes back.

**The defect this gate found was a scoping one, and it was not small.** Three of
the ten sheets — Verification, Relationships and Review_Status — took an
`intake_id` and never used it. Those tables hang off the ENTITY, so a query that
does not say which entities it means returns the whole registry: a workbook
built for a seven-record synthetic delivery exported **36,231 relationship
rows**, the entire Government population, into a file classified
DEVELOPMENT_TEST. Found by profiling a suite that took 8m20s, not by reading the
code. Scope is now resolved once, before any sheet is built. The suite runs in
38 seconds.

**Two smaller ones, same method.** The mapping sheet printed a zero-based column
index under a heading a reviewer reads as "field 1 of 41". And the engine
re-derived every cell's style, which openpyxl interns by recursively hashing the
font, border and alignment — 1,976 cells/second, now 5,331.

**Excel controls, proved by reopening the file rather than by reading the code
that wrote it.** A leading zero survives, a long identifier does not become
scientific notation, a formula-shaped value is stored as a string with Excel's
literal marker and no cell anywhere in the workbook has formula type. Delivered
whitespace is delivered content. A source recorded `unavailable` is exported
unavailable — never clear, never a pass.

**Classification is a property of the data.** The request model has no
classification field and a test asserts it never grows one. The route resolves
it from the intake through `resolve_data_state`, not from
`source_provenance._classification()`, which reads the synchronous fallback and
therefore can never return GOVERNMENT. That blindness still affects every other
report path and is recorded as a scoped follow-up, not fixed here.

**A pre-existing exposure closed.** `/api/reports/artifacts/{id}` returned
`storage_locator` — a filesystem path — to any viewer. Registry rows are now
scrubbed at the API boundary.

**38 tests, 16/16 mutations detected**, each naming the test that must catch it.
Three mutations were missed on the first pass; each exposed a real gap and each
test was strengthened rather than the mutation weakened. 217 passed across the
proportional regression on shared reporting infrastructure.

**Government scale, measured read-only:** 1,962,377 cells, ~7 MB, ~7½ minutes,
~690 MB peak. Rendering runs off the event loop. Seven minutes is still not an
HTTP request, and a background job is recorded for a later step rather than
built here.

**No Government workbook was generated.** The delivered intake carries no
`government_authorized` marker, so DocuAction's resolver reports MOCK_TEST while
the database holds the ONC/RCE snapshot with the clean Area-1 SHA-256. The flag
and the content disagree; that is ambiguous, and ambiguity means forecast, not
generate.

**Government data writes: ZERO.** All eight anchored counts match and the Area-1
digest is unchanged. No artifact of any kind was written to the registry.

Detail: `docs/TEFCA_Controlled_Excel_Export_INTERNAL.md`.

## Step #17C result — export operationalization

**PASS.** The certified export generator is now operationally safe to run: it is
queued rather than awaited, classified correctly, audited, and downloaded
without disclosing where its bytes live. **The workbook design is unchanged.**

**The seven-minute request is gone.** `report_export_jobs` reuses the PPEF
ingestion pattern verbatim in shape — database-authoritative state, a partial
unique index over active jobs, `FOR UPDATE SKIP LOCKED` claiming, heartbeat and
reaper — with its own table, because the PPEF one is keyed on a CMS component
and carries a foreign key to a PPEF snapshot. **No second job framework, no new
dependency, no new infrastructure.** Measured: the request returns in **21 ms**
while the export runs, and the event loop ticked 115 times during a deliberately
slowed render.

**The classification defect was real, and is corrected at the source.**
`authoritative_source_provenance` holds a session and was classifying through
`data_state_sync()`, which by construction can never return GOVERNMENT — so no
report on any deployment could ever be classified as Government data, however
properly authorised. In development the answer was right by accident. The
session-free helper remains, honestly, as the fallback for callers with no
database. Regression across every consumer moved six tests and made one
assertion materially stronger; an empty database now classifies as
`NO_DATASET_LOADED`, which the report template already had a banner for.

**Government authorization was not touched, and that is the finding.** The
delivered snapshot is the Government delivery by every visible measure and
carries no authorisation marker, so it stays DEVELOPMENT_TEST. Engineering may
determine identity; it may not manufacture authorization. Recorded as open
Government decision 7 — **ONC / PROGRAM GOVERNANCE DECISION REQUIRED.**

**Every artifact download route was swept.** One helper now builds every
download response; `attachment`, `nosniff` and `no-store` are applied where
four routes previously disagreed, the served type is the STORED type rather than
a caller-named one, filenames are sanitised against header injection, and
`storage_locator` no longer reaches any API response.

**58 new tests, 471 passed across the proportional regression, 17/17 mutations
detected.** Four mutations were missed on the first pass and each exposed a real
gap in a test; every one was strengthened rather than the mutation weakened.

**Government data writes: ZERO.** All anchored counts match, the Area-1 digest is
unchanged, and zero export jobs, zero XLSX artifacts and zero authorisation
markers exist against the delivered population.

Detail: `docs/TEFCA_Controlled_Excel_Export_Operationalization_INTERNAL.md`.

## Step #18 result — production security and Azure readiness

**PARTIAL.** The application is ready; the infrastructure under it is not yet.
The three readiness questions are answered separately and none is conflated:
**controlled production rehearsal — NO. Government ingestion — NO. Official ARC
operations — NO.**

**The headline finding is that the Key Vault work is complete and entirely
unused.** Both vaults have public access disabled, RBAC, purge protection and
private endpoints; both app identities already hold Key Vault Secrets User. And
**zero application settings reference a vault** — seven DEV and eight PROD
secrets sit in App Service settings as literal values. The remaining work is
configuration, not architecture. It could not be completed from here, and the
reason is the control working: the DEV vault refused on RBAC (the human holds no
data-plane role) and the PROD vault refused on the network before RBAC was
consulted. Finishing it would have meant granting a human Secrets Officer or
opening the vault publicly. Neither was done.

**One latent fail-safe gap was closed.** `schema_guard` asked only whether
`ENVIRONMENT` said production, so an **unset** variable meant not-production,
which meant startup schema mutation was allowed — and unset is exactly the state
a restored configuration or a new slot begins in. On production that would let a
restart create the Area 1 tables owned by the connecting role, making
immutability inert. Silence is now read in the light of where the process runs:
App Service always sets `WEBSITE_SITE_NAME`, a laptop never does, so a deployed
host with no environment is treated as production while developer convenience is
untouched.

**The 320px shared shell is fixed.** Carried since Step #16. Below `sm` the
sidebar becomes an overlay drawer with a menu button, scrim, Escape-to-close and
focus handling. Measured rendered: content at 320px went from **113px to 305px**,
no page horizontal scroll, zero overflowing elements, all 28 nav links reachable.
Rendered inspection found one real defect (focus was not returned after Escape)
and one false alarm (a transform that only looked stuck because the automation
context never ran the CSS transition).

**Step #17C's worker-topology carry-forward is answered with evidence:** one App
Service instance and a gunicorn `CMD` with no `--workers` flag, with
`appCommandLine` empty on both apps — exactly one process runs the schedulers.

**One security finding, one correction of record.** DEV's PostgreSQL firewall
carries `AllowAllAzureServices (0.0.0.0)`; PROD does not. And Step #17C's claim
that adjacent download routes lack `nosniff` was **wrong** — a global middleware
sets it on every response including errors, verified by probe. All 28
file-serving routes across 10 modules are authenticated.

**Five blockers before a controlled rehearsal**, four needing authorization this
gate does not have: PROD secrets in plaintext; **no Storage Account exists at
all**, so artifacts live on the App Service filesystem; restore never exercised;
PROD database role model unverified; and the DEV firewall rule.

**16/16 mutations detected. 537 passed** across the proportional regression.
**No Azure resource was created, modified or deleted. No production change. No
Government data read for content, written or exported. Government authorization
unchanged and still absent.** All anchors match; Area-1 digest unchanged.

Detail: `TEFCA_Production_Readiness_Matrix_INTERNAL.md`,
`TEFCA_Security_Azure_Readiness_INTERNAL.md`,
`TEFCA_Production_Migration_Runbook_INTERNAL.md`,
`TEFCA_Section_508_Test_Readiness_INTERNAL.md`.

## Step #18A result — Azure closure attempt

**PARTIAL. The gate did not open, and Step #19 was not executed.**

Four of the five blockers cannot be closed within this prompt's own authority and
the fifth was proven unsafe to close. One item was closed with a test.

**Key Vault: a drift finding.** `infra/modules/appService.bicep` already declares
`keyVaultReferenceIdentity: SystemAssigned` and four Key Vault references. The
deployed App Services carry literal values instead — the intended architecture
was designed, committed and never became the live state. Worse, the IaC does not
vault `DATABASE_URL`, `SAM_GOV_API_KEY`, `USPS_CLIENT_SECRET` or
`PERIGON_API_KEY`, so four more secrets need a design change rather than a
redeploy. DEV migration is still refused by RBAC and by the vault's own network
policy; PROD migration is out of scope by instruction.

**Artifact storage: a correction, and worse than recorded.** Step #18 said the
Azure backend was implemented. It is not — `AzureBlobArtifactStore` is a declared
seam whose `put`, `get`, `head` and `versions` all raise, with the packages
deliberately absent. And the local root is a RELATIVE path on the container's
writable layer with `WEBSITES_ENABLE_APP_SERVICE_STORAGE=false`, so artifacts
survive no restart, deployment, replacement or scale-out. A provisioning
specification is recorded.

**Database roles: blocked by a control.** Both servers have Entra authentication
**disabled**, so the only credential is the stored password and verifying the
roles would mean extracting a secret. Azure DEV and PROD roles remain
**UNVERIFIED** — Step #18 proved only the *local developer* database.

**DEV firewall: proven unsafe to remove.** The broad `AllowAllAzureServices` rule
is what the DEV app is actually connecting through — one live outbound IP
(`13.89.172.22`) is not individually listed, and 18 of 34 possible IPs are not.
Removing it would have broken DEV. Measurement prevented exactly the "break DEV
to make a matrix green" failure the brief warns about.

**Export capacity: closed.** Measured independently at **659 MB projected render
peak, 32% of the B1ms plan**, corroborating Step #17's ~690 MB by another method.
One-export-at-a-time already holds structurally (the poller claims one job;
`max_instances=1`); a test now asserts it, because it is a memory ceiling rather
than a preference. **No plan resize recommended.**

**No Azure resource was created, modified or deleted. No production change.
Government data unchanged; Area-1 digest `3af240c30035b17d5d669a2f8ddbd33a`.**

## Step #18B result — final DEV closure

**PASS. All six Step #18A blockers closed on DEV, and the final synthetic E2E
passed. DEV ENGINEERING IS FROZEN.**

**Database identity — the highest-risk item, now evidence.** Entra
authentication was enabled on Azure DEV *alongside* password auth, so every
check ran on a short-lived token and **no stored password was ever read**. The
Area 1 boundary was already correct: `docuaction_owner` owns the immutable
tables and the runtime holds INSERT/SELECT only. One real defect was found and
fixed — **`docuaction_app` could CREATE tables**, which would have let anything
it created be owned by the runtime role and make immutability inert. 13/13
permission probes now pass, run via `SET ROLE` inside a rolled-back transaction.

**Durable artifact storage — built, not just specified.** The Azure backend was
a seam whose every method raised; it is now implemented, with immutability
enforced by the service (`overwrite=False`) rather than by a check with a race
window. A DEV storage account with **shared-key access disabled** means no
storage key exists to leak. **21/21 tests against the real container**,
including durability across a fresh client, four concurrent writers producing
four versions, and eight hostile locators refused.

**PITR actually performed.** DEV restored to an isolated server in **~7
minutes**, validated read-only, and deleted. The restored Area-1 digest matched
its source exactly. That also clarified something Step #18 had flagged: the
`3af240c…` digest is the *local developer* database; Azure DEV is a different
database (`bdafaf1f…`) whose source anchors agree exactly while its derived
review counts differ.

**Key Vault closed.** Seven secrets migrated and all seven `Resolved` through
the managed identity — including the four the original IaC never covered, of
which `DATABASE_URL` is the most sensitive. Access was obtained without opening
the vault (single-IP rule, `defaultAction: Deny`) and **both the network opening
and the temporary role were reverted and verified**.

**Network and monitoring closed.** The broad `AllowAllAzureServices` rule is
gone — but only after covering all 34 possible outbound addresses and proving
connectivity, because Step #18A had measured that removing it first would break
DEV. DEV gained a Log Analytics workspace, Application Insights, and three
alerts that answer real questions.

**Final E2E: 9/9.** One synthetic case travelled delivery → Area 1 → DQ →
curation → promotion → verification → analyst → independent QA → reportability →
workbook, and was **reconstructed backwards from persisted records** to the
delivered bytes, which still hash to their recorded digest. Self-approval is
refused; only the independently approved case is reportable.

**Honest limits.** The DEV image was **not deployed** — `gh` is unavailable and
CI/CD must not be bypassed — so the Blob backend is tested but not yet live, and
`REPORT_ARTIFACT_BACKEND` was deliberately reverted to `local` rather than left
ahead of the code. Migrations still run as the runtime role and will now fail by
design; they must move to `docuaction_owner`.

**Government data unchanged.** Digest `3af240c30035b17d5d669a2f8ddbd33a` before
and after; every anchor identical; zero Government exports, zero export jobs,
authorization marker still absent. **PROD untouched.**

Detail: `TEFCA_Final_DEV_Acceptance_INTERNAL.md`,
`TEFCA_PROD_Execution_Checklist_INTERNAL.md`.

## Next master step

**Controlled production deployment and production validation**, using
`TEFCA_PROD_Execution_Checklist_INTERNAL.md`. Not started, and not authorized by
this gate.
