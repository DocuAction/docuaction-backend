# DocuAction TEFCA ARC — QA/UAT DEV execution matrix (2026-09-14)

Prepared by DEV for formal QA/UAT. QA determines PASS/FAIL; every DEV Status below is a readiness label, never a result. 
Workbook: `DocuAction_TEFCA_ARC_Formal_QA_UAT_Test_Cases_DEV_READY_2026-09-14.xlsx` (sheets *Test Cases DEV-Ready*, *DEV Screen Map*, *QA Data Preparation*).

RETEST_REQUIRED cases depend on the Release 1.0 remediation build (backend PR #62 + the closure branch, frontend PR #44 + the closure branch) being deployed to DEV; that deployment needs human environment approval.

## DEV screen map

| Navigation label | Route | Section | Roles offered | Primary API | Notes |
|---|---|---|---|---|---|
| Mission Control | /tefca-arc | TEFCA ARC · OVERVIEW | all TEFCA roles | /api/tefca/dashboard/summary, /api/tefca/arc/operations/dashboard | Operational status by workflow state |
| ONC/RCE Deliveries | /tefca-arc/deliveries | TEFCA ARC · DATA | all; register/create cycle = Program Manager or Admin | /api/tefca/rce/delivery-jobs, /api/tefca/rce/deliveries/{id}/dashboard | Delivery ID, file SHA-256, received at/by, counts, stages (Provenance block added 2026-09-14) |
| Registry & QHIN Relationships | /tefca-registry | TEFCA ARC · DATA | all | /api/tefca/registry/stats, /hierarchy | QHIN → Participant → Subparticipant tree |
| Entities | /tefca-registry/entities | TEFCA ARC · DATA | all | /api/tefca/registry/entities | Entity list; open one → /tefca-registry/entity?id=… |
| Data Import (Non-Official) | /tefca-arc/import | TEFCA ARC · DATA | reviewer and above | /api/tefca/registry/import/* | Synthetic/non-official imports only |
| Supervisor Operations | /tefca-arc/operations | TEFCA ARC · REVIEW OPERATIONS | all (writes: senior_analyst+) | /api/tefca/arc/operations/* | Search a case id → row → Open Verification Workspace |
| QHIN Assignment | /tefca-arc/assignment | TEFCA ARC · REVIEW OPERATIONS | senior_analyst and above | /api/tefca/workflow/* | Bulk assignment by QHIN |
| My Reviews | /tefca-arc/my-reviews | TEFCA ARC · REVIEW OPERATIONS | all (own queue) | /api/tefca/workflow/my-reviews | Analyst queue incl. RETURNED cases |
| Priority Reviews | /tefca-arc/priority | TEFCA ARC · REVIEW OPERATIONS | all | /api/tefca/arc/priority-requests | Task 5 |
| Findings | /tefca-arc/findings | TEFCA ARC · REVIEW OPERATIONS | all | /api/tefca/findings | Demonstration population labelled |
| Data-Quality Issues | /tefca-registry/issues | TEFCA ARC · EVIDENCE | all | /api/tefca/rce/deliveries/{id}/issues |  |
| Sources & Connectors | /tefca-arc/connectors | TEFCA ARC · EVIDENCE | all | /api/tefca/status, /api/tefca/qa/connector-health | PECOS = NPPES proxy / not connected (truthful) |
| Contract Reports | /tefca-arc/reports | TEFCA ARC · REPORTING & OUTPUT | all (generate/release: Program Manager or Admin) | /api/reports | Register, generate, download DOCX/HTML/CSV/ZIP, PM release |
| Audit & Decision History | /tefca-arc/audit | TEFCA ARC · REPORTING & OUTPUT | QA Lead, Program Manager, Admin | /api/tefca/audit-trail | Platform log + registry decision lineage merged (2026-09-14); search by case id |
| Analytics | /tefca-arc/analytics | TEFCA ARC · REPORTING & OUTPUT | all | /api/tefca/dashboard/trends |  |
| Platform Health & Technical QA | /tefca-arc/qa | TEFCA ARC · REPORTING & OUTPUT | QA Lead, Program Manager, Admin | /api/tefca/qa/* | QA sweep requires qalead |
| Learning Center | /tefca-arc/help | TEFCA ARC · LEARNING | all | /api/learning/TEFCA_ARC | 16 modules, 5 role paths, knowledge checks |
| Verification Workspace | /tefca-arc/workspace?review=<REV id> | (opened from Supervisor Operations, My Reviews or QHIN Assignment) | all (act: holder/reviewer; QA panel: qalead+) | /api/tefca/workflow/reviews/{id}/workspace, /api/tefca/arc/reviews/{id}/{claim\|determination\|qa} | Sections A–F + Processing Trace + Case actions |

## QA data preparation (synthetic, disposable)

P1. **Login** — Sign in as Test Admin (or Program Manager).
P2. **ONC/RCE Deliveries** — Left navigation → TEFCA ARC · DATA → ONC/RCE Deliveries → 'Register official delivery'. Upload the synthetic pipe-delimited file `docs/qa/fixtures/synthetic_rce_delivery_QA.txt` (41-field header, 6 synthetic organisations under the unassigned OID arc 9.99.777). Period: 'September 2026 (synthetic)'; Source: 'SYNTHETIC QA FIXTURE'; Delimiter: pipe.
P3. **ONC/RCE Deliveries → delivery card** — Wait for the delivery card to reach stage READY FOR REVIEW (the delivery poller runs every 5 s: intake → quality → curation → promotion → reconciliation). Open the delivery: the Provenance block shows Delivery ID, File SHA-256, Received at/by, declared and received counts, 41 delivered fields per record. Reconciliation must read PASSED (A–F populations close).
P4. **ONC/RCE Deliveries → Create review cycle** — On the delivery card press 'Create review cycle' (Program Manager or Admin). The official per-QHIN sample is drawn once (census for a 4-record population) and every member is verified against NPPES / LEIE / SAM and linked to a review case (REV-2026-…). If 'remaining' is not 0, press again; it never redraws.
P5. **Supervisor Operations** — Left navigation → Supervisor Operations: the new cases appear as unassigned. Note the case ids. These are the DISPOSABLE synthetic cases for REV-/QA-/AUD-/RPT- tests. Never use REV-2026-000238 (real ONC case) for any write.
P6. **DEV configuration (operator)** — Prerequisite check: DEV app setting ENTITY_RESOLVER_SOURCE must be 'db' (verified 2026-09-14). With 'mock' the review cycle cannot resolve delivered organisations and creates no cases.

## Test cases

### AUTH-001 — Authentication (Test Admin)

- **DEV screen:** Login (/login)
- **Navigation:** DEV Login page
- **Preconditions:** DEV available; QA credential current
- **Test data:** testadmin@docuaction.io
- **Steps:** Enter credential; Sign in
- **Expected on screen:** Dashboard/Mission Control loads; user name and role shown in the rail
- **Expected API:** POST /api/auth/login 200 → {access_token, user}
- **Evidence:** Screenshot of landing page
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### AUTH-002 — Authentication (Reviewer)

- **DEV screen:** Login
- **Navigation:** DEV Login page
- **Preconditions:** Reviewer credential current
- **Test data:** reviewer@docuaction.io
- **Steps:** Sign out; sign in as Reviewer
- **Expected on screen:** Reviewer-authorised pages load; Audit & Decision History and Platform Health are NOT offered in the navigation (role floor qalead)
- **Expected API:** GET /api/auth/me 200 role=reviewer
- **Evidence:** Screenshot of navigation
- **Screen available:** YES
- **DEV status:** RETEST_REQUIRED (navigation role floor added 2026-09-14)
- **DEV note:** Reviewer must not see Audit & Decision History / Platform Health entries; opening them directly shows a QA Lead access required state

### AUTH-003 — Authentication (QA Lead)

- **DEV screen:** Login
- **Navigation:** DEV Login page
- **Preconditions:** QA Lead credential current
- **Test data:** qalead@docuaction.io
- **Steps:** Sign out; sign in as QA Lead
- **Expected on screen:** QA-authorised pages load; Audit & Decision History and Platform Health ARE offered
- **Expected API:** GET /api/auth/me 200 role=qalead
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – re-confirm navigation

### AUTH-004 — Authentication (Any)

- **DEV screen:** Login
- **Navigation:** DEV Login page
- **Preconditions:** —
- **Test data:** Any user + wrong password
- **Steps:** Attempt one login with a wrong password
- **Expected on screen:** Server-reported invalid-credential reason shown (not generic 'Login failed')
- **Expected API:** POST /api/auth/login 401 with reason
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### SESSION-001 — Authentication (Reviewer)

- **DEV screen:** Any TEFCA screen
- **Navigation:** Stay signed in for 16 minutes
- **Preconditions:** Non-admin session
- **Test data:** —
- **Steps:** Leave a TEFCA page open past 15 minutes; then click any navigation entry
- **Expected on screen:** Session ends cleanly: redirect to /login with no error loop; no repeated failed requests
- **Expected API:** GET /api/auth/me 401 once
- **Evidence:** Screenshot + browser network tab (1 failed request)
- **Screen available:** YES
- **DEV status:** READY (new: documents the 15-minute non-admin session; refresh flow is a separately authorised change)
- **DEV note:** Expected behaviour today; do not log as a defect unless a loop/storm occurs

### NAV-001 — Navigation (Test Admin)

- **DEV screen:** Supervisor Operations
- **Navigation:** Left navigation → TEFCA ARC · REVIEW OPERATIONS → Supervisor Operations
- **Preconditions:** A synthetic case exists (QA Data Preparation P2–P5)
- **Test data:** Case id from P5 (or REV-2026-000245)
- **Steps:** Search the case id; open the row/panel
- **Expected on screen:** Case row found; 'Open Verification Workspace' link present
- **Expected API:** GET /api/tefca/arc/operations/work-queue 200
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### TRACE-001 — Processing Trace (Test Admin)

- **DEV screen:** Verification Workspace → Processing Trace
- **Navigation:** Supervisor Operations → case → Open Verification Workspace
- **Preconditions:** Case from P5
- **Test data:** Case id
- **Steps:** Open the workspace; read the eight-stage Processing Trace
- **Expected on screen:** Trace loads for the correct case; stages 1–8 show Complete / Pending / Not available truthfully
- **Expected API:** GET /api/tefca/workflow/reviews/{id}/workspace 200
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### SRC-001 — Delivery (Test Admin)

- **DEV screen:** ONC/RCE Deliveries → delivery card → Provenance; and Verification Workspace → Section A
- **Navigation:** Left navigation → ONC/RCE Deliveries → click the synthetic delivery
- **Preconditions:** Synthetic delivery registered (P2–P3)
- **Test data:** Delivery from P2
- **Steps:** Read the Provenance block on the delivery card; then open a case from that delivery and read Section A → Delivery
- **Expected on screen:** Delivery ID, File, File SHA-256 (64 hex), Received at, Received by, Declared record count, Records received, 'Delivered fields per record: 41 (documented field map v…)' all shown. Section A shows the same Delivery ID and SHA-256; an absent value shows as '—', never disappears
- **Expected API:** GET /api/tefca/rce/deliveries/{id}/dashboard → intake_id, sha256; workspace.source.delivery.intake_id/sha256
- **Evidence:** Screenshots of both blocks
- **Screen available:** YES
- **DEV status:** RETEST_REQUIRED (DEF-001 fixed 2026-09-14: Provenance block added to the Deliveries screen; Section A no longer hides absent values)
- **DEV note:** QA's earlier observation was made on the Deliveries card, which showed filename/received/records only

### MAP-001 — 41-Field Mapping (Test Admin)

- **DEV screen:** Verification Workspace → Section A → Delivered fields table
- **Navigation:** Open case from P5 → Section A → 'View all 41 delivered fields'
- **Preconditions:** Case created from the synthetic delivery (P4). A legacy case that did not come through a delivery shows 'This case has no Area 1 record' — use a P5 case
- **Test data:** Case id from P5
- **Steps:** Expand 'View all N delivered fields'
- **Expected on screen:** 41 field names in delivered order; each 'populated' or 'not provided'; no fabricated values
- **Expected API:** workspace.source.delivered_values has 41 keys
- **Evidence:** Screenshot of the expanded table
- **Screen available:** YES (with a pipeline case)
- **DEV status:** RETEST_REQUIRED (blocked earlier because the case used had no Area 1 record; navigation and data path now documented)

### REL-001 — Relationships (Test Admin)

- **DEV screen:** Verification Workspace → Section A → Relationship; Registry & QHIN Relationships
- **Navigation:** Open case from P5 → Section A → Relationship block; also Left navigation → Registry & QHIN Relationships
- **Preconditions:** Case from P5 (Participant or Subparticipant)
- **Test data:** Case id from P5
- **Steps:** Read Level / QHIN / Parent organisation and the basis line
- **Expected on screen:** QHIN → Participant → Subparticipant chain shown from canonical edges; basis reads 'Canonical relationship edges written at promotion. Not inferred…'; a missing parent shows the unresolved note, never an invented parent
- **Expected API:** workspace.source.relationship.resolved=true with qhin / parent_organization
- **Evidence:** Screenshot
- **Screen available:** YES (with a pipeline case)
- **DEV status:** RETEST_REQUIRED (blocked earlier: no Area 1 record on the case used)

### REL-002 — Relationships (Test Admin)

- **DEV screen:** Verification Workspace → Section A → Relationship
- **Navigation:** Open a HELD/unpromoted synthetic record (P3 shows 2 held rows)
- **Preconditions:** Synthetic delivery (row with malformed NPI is HELD)
- **Test data:** Held row from P3
- **Steps:** Open the held record's detail (Data-Quality Issues / Section A)
- **Expected on screen:** System reports the relationship as not promoted / unresolved rather than manufacturing a parent
- **Expected API:** relationship.resolved=false with note
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### EVD-001 — Evidence (Test Admin)

- **DEV screen:** Verification Workspace → Section D Verification / Processing Trace stage 4–5
- **Navigation:** Open case from P5 → 'Processing & validation' and 'Evidence' sections
- **Preconditions:** Case verified by the review cycle (P4)
- **Test data:** Case id from P5
- **Steps:** Inspect the verification dimensions and coverage summary
- **Expected on screen:** Coverage lists applicable controls only; each dimension shows MATCH / NO_MATCH / SOURCE_UNAVAILABLE / NOT_FOUND distinctly; unavailable is never rendered as non-compliance
- **Expected API:** workspace.verification.available=true with dimensions[]
- **Evidence:** Screenshot
- **Screen available:** YES (with a pipeline case)
- **DEV status:** RETEST_REQUIRED (blocked earlier: the case used had no verification evidence)

### EVD-002 — Evidence (Test Admin)

- **DEV screen:** Verification Workspace → Evidence detail
- **Navigation:** Open case from P5 → expand one dimension/control
- **Preconditions:** As EVD-001
- **Test data:** Case id from P5
- **Steps:** Expand a control
- **Expected on screen:** Source, observation/result and retrieval timestamp visible where available; missing pieces read 'not available'
- **Expected API:** workspace.evidence / verification.dimensions[i] carries source, observed_at
- **Evidence:** Screenshot
- **Screen available:** YES (with a pipeline case)
- **DEV status:** READY

### EVD-003 — Evidence (Test Admin)

- **DEV screen:** Verification Workspace → Evidence
- **Navigation:** As EVD-001
- **Preconditions:** As EVD-001
- **Test data:** Case id from P5
- **Steps:** Locate a SOURCE_UNAVAILABLE control (SAM.gov is unavailable in DEV)
- **Expected on screen:** Labelled Source unavailable; not counted as non-compliance/contradiction
- **Expected API:** —
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### REV-001 — Analyst Workflow (Reviewer)

- **DEV screen:** My Reviews / Verification Workspace → Case actions
- **Navigation:** Sign in as Reviewer → Supervisor Operations or My Reviews → open an UNCLAIMED P5 case → Claim
- **Preconditions:** Unclaimed synthetic case
- **Test data:** Case id from P5
- **Steps:** Click Claim; refresh; reopen
- **Expected on screen:** State CLAIMED, holder = reviewer; persists after refresh
- **Expected API:** POST /api/tefca/arc/reviews/{id}/claim 200 (409 if already held)
- **Evidence:** Screenshot before/after
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – re-run on a P5 case

### REV-002 — Analyst Workflow (Reviewer)

- **DEV screen:** Verification Workspace → Case actions → Analyst determination
- **Navigation:** As REV-001 on the claimed case
- **Preconditions:** Claimed case
- **Test data:** Rationale 'too short'
- **Steps:** Submit a determination with a rationale under 10 characters
- **Expected on screen:** Rejected; no determination persists
- **Expected API:** POST …/determination 422 'rationale: String should have at least 10 characters'
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### REV-003 — Analyst Workflow (Reviewer)

- **DEV screen:** Verification Workspace → Case actions
- **Navigation:** As REV-002
- **Preconditions:** Claimed case
- **Test data:** CONFIRM + rationale ≥ 10 chars citing the evidence
- **Steps:** Submit a valid determination
- **Expected on screen:** Determination, actor, rationale and timestamp shown in the decision history; state SUBMITTED FOR QA
- **Expected API:** POST …/determination 200 event_type=ANALYST_DETERMINATION seq 1
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### QA-001 — Independent QA (Reviewer)

- **DEV screen:** Verification Workspace → Case actions
- **Navigation:** Same Reviewer, same case
- **Preconditions:** Case in SUBMITTED FOR QA
- **Test data:** —
- **Steps:** Look for the QA panel; attempt a QA action
- **Expected on screen:** No APPROVE/RETURN/ESCALATE panel for the reviewer; the panel text says QA Lead required
- **Expected API:** POST …/qa 403 'Required: qalead'
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### QA-002 — Independent QA (QA Lead)

- **DEV screen:** Verification Workspace → Case actions → Independent QA panel
- **Navigation:** Sign in as QA Lead → Supervisor Operations → open the SUBMITTED FOR QA case
- **Preconditions:** Reviewer determination exists (REV-003)
- **Test data:** Case id
- **Steps:** Choose APPROVE, reason ≥ 10 chars, submit
- **Expected on screen:** State APPROVED, reportable; QA actor/timestamp distinct from the reviewer
- **Expected API:** POST …/qa 200 qa_action=APPROVE reportable=true
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### QA-003 — Independent QA (QA Lead)

- **DEV screen:** Verification Workspace → Case actions → Independent QA panel (select: APPROVE / RETURN / ESCALATE)
- **Navigation:** QA Lead → open a SECOND P5 case that is in SUBMITTED FOR QA (a reviewer must claim + determine it first)
- **Preconditions:** Disposable case in state SUBMITTED FOR QA. The panel is offered ONLY in that state: an APPROVED or CLAIMED case shows no QA panel
- **Test data:** Second case id
- **Steps:** Open the action select → choose 'RETURN — back to the analyst for a fresh determination' → reason ≥ 10 chars → submit
- **Expected on screen:** State RETURNED; the decision history keeps the original determination AND the QA return with reason; My Reviews (reviewer) shows the case under Returned; reviewer can submit a fresh determination and it returns to SUBMITTED FOR QA
- **Expected API:** POST …/qa 200 qa_action=RETURN reportable=false; GET …/history shows 2 events; my-reviews counts.returned=1
- **Evidence:** Screenshots of the select, the history and My Reviews
- **Screen available:** YES
- **DEV status:** RETEST_REQUIRED (AK-001: RETURN exists in the build QA used; it is offered only for SUBMITTED FOR QA cases. Role aliases such as 'qa_lead' now resolve in the shell too)
- **DEV note:** Verified end-to-end on the synthetic runtime 2026-09-14: RETURN → RETURNED → rework → APPROVE

### QA-004 — Independent QA (QA Lead)

- **DEV screen:** Verification Workspace → Independent QA panel
- **Navigation:** As QA-003 on a third SUBMITTED FOR QA case
- **Preconditions:** Disposable case in SUBMITTED FOR QA; a Program Manager account to escalate to
- **Test data:** Third case id; PM user
- **Steps:** Choose ESCALATE → pick the escalation target → reason → submit
- **Expected on screen:** State ESCALATED; not reportable; history shows the escalation with target and reason
- **Expected API:** POST …/qa 200 qa_action=ESCALATE; case state ESCALATED
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** READY (verified on the synthetic runtime 2026-09-14)
- **DEV note:** PM resolves via 'supersede' (program_manager)

### AUD-001 — Audit (Test Admin or QA Lead)

- **DEV screen:** Audit & Decision History
- **Navigation:** Left navigation → TEFCA ARC · REPORTING & OUTPUT → Audit & Decision History → search box → enter the case id
- **Preconditions:** Case that went through REV-003 / QA-002 or QA-003
- **Test data:** Case id
- **Steps:** Search the case id; set Event type = review
- **Expected on screen:** Rows for review_case_claimed, analyst_determination_recorded, qa_return / qa_approve with actor, timestamp and details (reason); denied attempts appear as authentication rows
- **Expected API:** GET /api/tefca/audit-trail?search=REV-… entries[] with source=registry and platform
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** RETEST_REQUIRED (fixed 2026-09-14: decision lineage was not in this screen — it showed only the platform authentication log)

### RPT-001 — Reporting (QA Lead → Program Manager/Admin)

- **DEV screen:** Contract Reports
- **Navigation:** Left navigation → Contract Reports → Generate draft (Task 3 Weekly D3.1) → Report register
- **Preconditions:** An APPROVED synthetic case exists
- **Test data:** Report type: Task 3 Weekly Progress (D3.1)
- **Steps:** Generate; open the register row; download HTML/DOCX/CSV/ZIP
- **Expected on screen:** Register entry with Report ID DA-ARC-…, generator, provenance, DEVELOPMENT / TEST marking; downloads open; PDF may be unavailable on a host without the PDF engine (503) — record, do not fail the case for PDF
- **Expected API:** POST /api/reports/generate 200; GET /api/reports lists it
- **Evidence:** Screenshots + downloaded files
- **Screen available:** YES
- **DEV status:** READY (verified on the synthetic runtime 2026-09-14: HTML/DOCX/CSV/ZIP; PDF engine host-dependent)
- **DEV note:** Generation requires program_manager or admin

### RPT-002 — Reporting (Test Admin)

- **DEV screen:** Contract Reports
- **Navigation:** Left navigation → Contract Reports
- **Preconditions:** —
- **Test data:** —
- **Steps:** Inspect the schedule/delivery metadata on the page and in a generated report header
- **Expected on screen:** Unavailable schedule/delivery metadata reads 'Not specified' / 'Not available', never an invented date
- **Expected API:** —
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** READY

### RPT-003 — Reporting (Program Manager)

- **DEV screen:** Contract Reports → register row → Release
- **Navigation:** Left navigation → Contract Reports → row → PM review action
- **Preconditions:** A generated report
- **Test data:** Report ID
- **Steps:** Record PM_REVIEWED with a note
- **Expected on screen:** Release history shows PM_REVIEWED, actor and time; external release remains a PM decision
- **Expected API:** POST /api/reports/{id}/release 200
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** READY (new; verified on the synthetic runtime)

### RBAC-001 — RBAC (Viewer)

- **DEV screen:** Verification Workspace / My Reviews
- **Navigation:** Sign in as Viewer → Supervisor Operations → open a case
- **Preconditions:** Synthetic case
- **Test data:** Case id
- **Steps:** Attempt Claim; look for determination/QA controls
- **Expected on screen:** No write controls offered; a forced request is denied
- **Expected API:** POST …/claim 403 'Required: reviewer, Current: viewer'
- **Evidence:** Screenshot + network tab
- **Screen available:** YES
- **DEV status:** READY (verified on the synthetic runtime 2026-09-14)
- **DEV note:** Viewer sees identifiers masked (PII floor)

### DATA-001 — Data Integrity (Test Admin)

- **DEV screen:** Verification Workspace — real ONC case
- **Navigation:** Supervisor Operations → search REV-2026-000238 → Open Verification Workspace
- **Preconditions:** REV-2026-000238 exists in DEV
- **Test data:** REV-2026-000238 (READ ONLY)
- **Steps:** Inspect only. Do not claim, determine or QA
- **Expected on screen:** Section A is read-only (chip); no control edits delivered values; values unchanged after viewing
- **Expected API:** GET workspace 200; no POST issued
- **Evidence:** Screenshot + network tab showing no POST
- **Screen available:** YES
- **DEV status:** READY
- **DEV note:** Never perform a write on this case

### DATA-002 — Data Integrity (Test Admin)

- **DEV screen:** ONC/RCE Deliveries → delivery card
- **Navigation:** Left navigation → ONC/RCE Deliveries → official delivery
- **Preconditions:** Official delivery present
- **Test data:** Official delivery
- **Steps:** Inspect Provenance and Processing; confirm no edit control exists
- **Expected on screen:** No UI workflow changes delivered source values; SHA-256 constant across views
- **Expected API:** Area 1 has no mutating route
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** READY

### UI-001 — Customer Truth (Test Admin)

- **DEV screen:** Priority Reviews
- **Navigation:** Left navigation → Priority Reviews
- **Preconditions:** —
- **Test data:** —
- **Steps:** Read the Configured Review Targets wording
- **Expected on screen:** States values are internal / not contractual
- **Expected API:** —
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### UI-002 — Customer Truth (Test Admin)

- **DEV screen:** Findings
- **Navigation:** Left navigation → Findings
- **Preconditions:** —
- **Test data:** —
- **Steps:** Read the population banner
- **Expected on screen:** Demonstration/test population clearly labelled
- **Expected API:** —
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### UI-003 — Customer Truth (Test Admin)

- **DEV screen:** Supervisor Operations
- **Navigation:** Left navigation → Supervisor Operations
- **Preconditions:** —
- **Test data:** —
- **Steps:** Read the population caveat
- **Expected on screen:** Does not imply an official ONC workload
- **Expected API:** —
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – no change

### SRC-002 — Source Truth (Test Admin)

- **DEV screen:** Sources & Connectors
- **Navigation:** Left navigation → Sources & Connectors
- **Preconditions:** —
- **Test data:** —
- **Steps:** Read the PECOS / Medicare enrollment rows and the connector health strip
- **Expected on screen:** PECOS reads Unavailable / NPPES proxy — never 'Live'; NPPES Live; SAM.gov Unavailable (key/upstream); IQVIA 'optional, not contracted'
- **Expected API:** GET /api/tefca/status connector_health.pecos = partial|unavailable, pecos_backing = nppes_proxy
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** RETEST_REQUIRED (fixed in PR #62: DEV currently shows pecos 'available')

### SEC-001 — Security (Test Admin)

- **DEV screen:** Multiple screens
- **Navigation:** Login, error states, Audit, Reports, Sources
- **Preconditions:** —
- **Test data:** —
- **Steps:** Review the screens used during QA for secrets
- **Expected on screen:** No passwords, tokens, connection strings, e-mail addresses in /health
- **Expected API:** GET /health has no alert_email (fixed PR #62)
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** RETEST_REQUIRED (DEV /health still exposes the operator e-mail until the remediation build is deployed)

### REG-001 — Regression (Test Admin)

- **DEV screen:** Findings / Supervisor Operations / Priority Reviews / My Reviews
- **Navigation:** Spot-check
- **Preconditions:** —
- **Test data:** —
- **Steps:** Spot-check the customer-truth wording
- **Expected on screen:** Corrected wording present
- **Expected API:** —
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – re-check after deploy

### REG-002 — Regression (Test Admin)

- **DEV screen:** Supervisor Operations → Verification Workspace
- **Navigation:** Repeat NAV-001 and TRACE-001
- **Preconditions:** —
- **Test data:** —
- **Steps:** Repeat NAV-001 and TRACE-001
- **Expected on screen:** Normal click path and trace remain available
- **Expected API:** —
- **Evidence:** Screenshot
- **Screen available:** YES
- **DEV status:** PASS (QA 2026-09-14) – re-check after deploy

### LMS-001 — Learning Center (Program Manager / Analyst / QA Lead / Viewer)

- **DEV screen:** Learning Center
- **Navigation:** Left navigation → TEFCA ARC · LEARNING → Learning Center
- **Preconditions:** —
- **Test data:** —
- **Steps:** Open the landing page; use the search; open 'Program modules' → a module; use Previous/Next; read the Knowledge check; switch theme; view at 390 px
- **Expected on screen:** 16 modules (role-scoped: PM 16, QA Lead 16, Reviewer 14, Admin 14, Viewer 12), five role paths, workflow figure with caption, Previous/Next, Knowledge check and Related training sections, knowledge version 1.2.0, dark theme, no horizontal scroll at 390 px
- **Expected API:** GET /api/learning/TEFCA_ARC modules[] length per role
- **Evidence:** Screenshots light/dark/mobile
- **Screen available:** YES
- **DEV status:** READY (verified on the synthetic runtime 2026-09-14)

### ISO-001 — Federal isolation (Test Admin)

- **DEV screen:** Any GovCon route typed directly (e.g. /rfqs, /ats, /pricing?rfq=1)
- **Navigation:** Type the route in the address bar
- **Preconditions:** TEFCA_ARC deployment profile (NOT the shared DEV slot, which runs profile ALL)
- **Test data:** —
- **Steps:** Open the route
- **Expected on screen:** 'Not available in this deployment' with one heading; no GovCon navigation; browser network tab shows no /api/rfq, /api/ats… request
- **Expected API:** GET /api/ats/jobs 404 NOT_FOUND
- **Evidence:** Screenshot + network tab
- **Screen available:** N/A on shared DEV (profile ALL)
- **DEV status:** BLOCKED on shared DEV until a TEFCA_ARC slot exists (human decision)
- **DEV note:** Verified on the synthetic runtime under TEFCA_ARC 2026-09-14
