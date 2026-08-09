"""AGT-REQ-001 — Volume II, Requirements Specification."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402

OUT = pathlib.Path(r"C:\Imran_Coding projects\DocuAction\backend\docs\enterprise")
OUT.mkdir(parents=True, exist_ok=True)

D = AGTDoc(doc_id="AGT-REQ-001",
           title="DocuAction TEFCA ARC Platform",
           subtitle="Prepared for the Assistant Secretary for Technology Policy / "
                    "Office of the National Coordinator for Health IT (ASTP/ONC)",
           version="1.0", date="August 2026")
D.cover("Volume II — Requirements Specification")
D.doc_control([
    ("Document ID", "AGT-REQ-001"),
    ("Document Title", "Volume II — Requirements Specification"),
    ("Version", "1.0"),
    ("Status", "Released"),
    ("Date", "August 2026"),
    ("Contract Number", "7571MN26F80064"),
    ("Contractor", "Alliance Global Tech, Inc. (AGT)"),
    ("CAGE / UEI", "8ERE8 / MP2FLV1MAW93"),
    ("Author", "Imran Siddiqui, Chief Executive Officer"),
    ("Classification", "CONFIDENTIAL — Controlled Unclassified Information (CUI)"),
    ("Related Documents", "AGT-EX-001, AGT-DA-001, AGT-SA-001, AGT-TE-005, "
                          "AGT-TE-006"),
])
D.page_break()
D.toc()

D.h1("Introduction")
D.p("This volume specifies the business, functional and non-functional "
    "requirements for the DocuAction TEFCA ARC platform, traces each to its "
    "contractual source, and records the implementation status of each against "
    "actual test evidence.")
D.p("Status values used throughout carry precise meanings and are not "
    "interchangeable:")
D.table(["Status", "Meaning"],
        [("Implemented", "Built, deployed and evidenced by a passing test or a "
                         "recorded measurement"),
         ("Partial", "Built and working, but a stated dependency or scope "
                     "limitation prevents full satisfaction"),
         ("Not Implemented", "Not built"),
         ("Not Applicable", "Out of scope for this contract"),
         ("Deferred", "Accepted as required, scheduled beyond the current "
                      "period")],
        widths=(1.3, 5.2))
D.callout("A requirement is only marked Implemented where evidence exists. "
          "Where a capability is built but cannot operate — the SAM.gov "
          "connector without an API key, for instance — the status is Partial "
          "and the blocking dependency is named. Marking such a requirement "
          "Implemented would misrepresent operational readiness.", "METHOD")
D.page_break()

# ── PART A ───────────────────────────────────────────────────────────────────
D.h1("Part A — Business Requirements Specification (BRS)")

BRS_COLS = ["ID", "Requirement", "Source", "Priority", "Status"]
BRS_W = (0.62, 3.5, 0.85, 0.65, 0.9)

D.h2("A.1 Task 1 — Kick-Off and Onboarding")
D.table(BRS_COLS, [
    ("BR-001", "Conduct 60-minute kick-off and status meetings weekly for the "
     "first 90 days of performance", "SOW Task 1", "Must", "Implemented"),
    ("BR-002", "Transition to bi-weekly 30-minute status meetings after the "
     "initial 90-day period", "SOW Task 1", "Must", "Implemented"),
    ("BR-003", "Develop and maintain a transition plan covering onboarding and "
     "eventual closeout", "SOW Task 1", "Must", "Implemented"),
    ("BR-004", "Establish written communication protocols identifying points of "
     "contact and escalation paths", "SOW Task 1", "Must", "Implemented"),
    ("BR-005", "Identify and onboard key personnel to the contract",
     "SOW Task 1", "Must", "Implemented"),
    ("BR-006", "Establish a deliverable submission and acceptance process with "
     "the COR", "SOW Task 1", "Must", "Implemented"),
], BRS_W)

D.h2("A.2 Task 2 — Review Methodology and Control Framework")
D.table(BRS_COLS, [
    ("BR-010", "Develop a documented entity review methodology covering QHINs, "
     "Participants and Sub-Participants", "SOW Task 2", "Must", "Implemented"),
    ("BR-011", "Define an accuracy validation process specifying which "
     "attributes are validated against which authoritative source",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-012", "Create a stratification methodology for dividing the entity "
     "population into review strata", "SOW Task 2", "Must", "Implemented"),
    ("BR-013", "Create a prioritisation methodology for ordering review effort "
     "by risk", "SOW Task 2", "Must", "Implemented"),
    ("BR-014", "Define B1 (No Discrepancy) classification criteria",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-015", "Define B2 (Minor / Administrative) classification criteria",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-016", "Define B3 (Inexplicable) classification criteria requiring "
     "human resolution", "SOW Task 2", "Must", "Implemented"),
    ("BR-017", "Define B4 (Non-Compliant) classification criteria",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-018", "Design a statistical sampling approach using Cochran's formula",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-019", "Apply a finite population correction to sample sizes",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-020", "Calculate and report confidence intervals for discrepancy rates",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-021", "Document the control framework governing review execution",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-022", "Version classification rules so the rule applied to any past "
     "classification is recoverable", "SOW Task 2", "Must", "Implemented"),
    ("BR-023", "Permit methodology change without code modification",
     "SOW Task 2", "Should", "Implemented"),
    ("BR-024", "Define severity levels for identified discrepancies",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-025", "Define the escalation path for B4 findings",
     "SOW Task 2", "Must", "Implemented"),
    ("BR-026", "Document data sources, their authority and their limitations",
     "SOW Task 2", "Must", "Implemented"),
], BRS_W)

D.h2("A.3 Task 3 — Retrospective Review")
D.table(BRS_COLS, [
    ("BR-030", "Draw a statistical sample at 95% confidence from the entity "
     "population", "SOW Task 3", "Must", "Implemented"),
    ("BR-031", "Review Participants and Sub-Participants within the drawn "
     "sample", "SOW Task 3", "Must", "Implemented"),
    ("BR-032", "Verify each entity against NPPES", "SOW Task 3", "Must",
     "Implemented"),
    ("BR-033", "Verify each entity against PECOS", "SOW Task 3", "Must",
     "Implemented"),
    ("BR-034", "Verify each entity against the OIG LEIE exclusion list",
     "SOW Task 3", "Must", "Implemented"),
    ("BR-035", "Verify each entity against SAM.gov registration and exclusions",
     "SOW Task 3", "Must", "Partial"),
    ("BR-036", "Verify each entity against the TEFCA entity data", "SOW Task 3",
     "Must", "Not Implemented"),
    ("BR-037", "Verify each entity against state licensure registries",
     "SOW Task 3", "Should", "Not Implemented"),
    ("BR-038", "Classify every reviewed entity into B1, B2, B3 or B4",
     "SOW Task 3", "Must", "Implemented"),
    ("BR-039", "Generate weekly review reports", "SOW Task 3", "Must",
     "Implemented"),
    ("BR-040", "Include discrepancy rates in every report", "SOW Task 3",
     "Must", "Implemented"),
    ("BR-041", "Include confidence intervals in every report", "SOW Task 3",
     "Must", "Implemented"),
    ("BR-042", "Include sampling statistics (population, sample size, seed) in "
     "every report", "SOW Task 3", "Must", "Implemented"),
    ("BR-043", "Document which data sources were used for each review cycle",
     "SOW Task 3", "Must", "Implemented"),
    ("BR-044", "Document limitations and exceptions in every report, with a "
     "section that cannot be empty", "SOW Task 3", "Must", "Implemented"),
    ("BR-045", "Record the raw response from each authoritative source for "
     "audit reconstruction", "SOW Task 3", "Must", "Implemented"),
    ("BR-046", "Validate NPI check digits using the CMS algorithm",
     "SOW Task 3", "Must", "Implemented"),
    ("BR-047", "Distinguish a source outage from a source finding of 'no "
     "record'", "SOW Task 3", "Must", "Implemented"),
    ("BR-048", "Export review results to Excel for ONC consumption",
     "SOW Task 3", "Should", "Implemented"),
    ("BR-049", "Reproduce any drawn sample exactly from its recorded seed",
     "SOW Task 3", "Should", "Implemented"),
], BRS_W)

D.h2("A.4 Task 4 — Ongoing Review")
D.table(BRS_COLS, [
    ("BR-050", "Establish and maintain a bi-weekly ongoing review cadence",
     "SOW Task 4", "Must", "Implemented"),
    ("BR-051", "Generate quarterly aggregated review reports", "SOW Task 4",
     "Must", "Implemented"),
    ("BR-052", "Track and document methodology improvements between cycles",
     "SOW Task 4", "Must", "Implemented"),
    ("BR-053", "Review newly submitted QHIN registry entries", "SOW Task 4",
     "Must", "Implemented"),
    ("BR-054", "Include per-week trend analysis within quarterly reports",
     "SOW Task 4", "Must", "Implemented"),
    ("BR-055", "Surface undated review records explicitly rather than dropping "
     "them from trend series", "SOW Task 4", "Must", "Implemented"),
    ("BR-056", "Tie each review cycle to its sample and its report as one "
     "auditable unit", "SOW Task 4", "Should", "Implemented"),
    ("BR-057", "Compare cycle-over-cycle discrepancy rates", "SOW Task 4",
     "Should", "Implemented"),
], BRS_W)

D.h2("A.5 Task 5 — Priority Reviews")
D.table(BRS_COLS, [
    ("BR-060", "Accept ad hoc priority review requests from ONC", "SOW Task 5",
     "Must", "Implemented"),
    ("BR-061", "Perform root cause analysis on each priority review subject",
     "SOW Task 5", "Must", "Implemented"),
    ("BR-062", "Assess and record severity for each priority finding",
     "SOW Task 5", "Must", "Implemented"),
    ("BR-063", "Provide corrective action recommendations", "SOW Task 5",
     "Must", "Implemented"),
    ("BR-064", "Generate a discrete report for each priority review",
     "SOW Task 5", "Must", "Implemented"),
    ("BR-065", "Include priority review findings in the quarterly aggregated "
     "report", "SOW Task 5", "Must", "Implemented"),
    ("BR-066", "Track priority review turnaround time", "SOW Task 5", "Should",
     "Implemented"),
], BRS_W)

D.h2("A.6 Task 6 — Contract Closeout")
D.table(BRS_COLS, [
    ("BR-070", "Produce a contract closeout report", "SOW Task 6", "Must",
     "Deferred"),
    ("BR-071", "Transfer methodology documentation to the Government",
     "SOW Task 6", "Must", "Deferred"),
    ("BR-072", "Transfer control framework documentation to the Government",
     "SOW Task 6", "Must", "Deferred"),
    ("BR-073", "Transfer tools and source code to the Government",
     "SOW Task 6", "Must", "Deferred"),
    ("BR-074", "Deliver an educational presentation to ONC staff", "SOW Task 6",
     "Must", "Deferred"),
    ("BR-075", "Deliver government rights materials", "SOW Task 6", "Must",
     "Deferred"),
    ("BR-076", "Provide a documented operations handover", "SOW Task 6",
     "Should", "Deferred"),
], BRS_W)
D.callout("Task 6 requirements are marked Deferred, not Not Implemented. They "
          "are contractually required and scheduled for the closeout period; "
          "they are not yet due.", "NOTE")

D.h2("A.7 Cross-Cutting Requirements")
D.table(BRS_COLS, [
    ("BR-090", "Implement security controls consistent with NIST SP 800-53 "
     "Rev 5 at the Moderate baseline", "NIST 800-53", "Must", "Partial"),
    ("BR-091", "Categorise the system per FIPS 199 as Moderate", "FIPS 199",
     "Must", "Implemented"),
    ("BR-092", "Apply CUI handling procedures to registry data",
     "NIST 800-171", "Must", "Implemented"),
    ("BR-093", "Protect personally identifiable information", "Privacy Act",
     "Must", "Implemented"),
    ("BR-094", "Handle protected health information per the HIPAA Security "
     "Rule where in scope", "HIPAA", "Must", "Implemented"),
    ("BR-095", "Meet Section 508 / WCAG 2.2 AA accessibility conformance",
     "Section 508", "Must", "Partial"),
    ("BR-096", "Maintain performance evidence suitable for CPARS reporting",
     "FAR 42.15", "Must", "Implemented"),
    ("BR-097", "Maintain an immutable audit trail of all review actions",
     "NIST AU-9", "Must", "Implemented"),
    ("BR-098", "Enforce role-based access control across all protected "
     "endpoints", "NIST AC-3", "Must", "Implemented"),
    ("BR-099", "Encrypt data in transit using TLS", "NIST SC-8", "Must",
     "Implemented"),
    ("BR-100", "Encrypt data at rest", "NIST SC-28", "Must", "Implemented"),
    ("BR-101", "Terminate sessions on credential change", "NIST 800-63B",
     "Must", "Implemented"),
    ("BR-102", "Rate-limit authentication attempts", "NIST AC-7", "Must",
     "Implemented"),
    ("BR-103", "Perform static application security testing", "NIST RA-5",
     "Must", "Implemented"),
    ("BR-104", "Perform dynamic application security testing", "NIST RA-5",
     "Must", "Partial"),
    ("BR-105", "Perform software composition analysis on dependencies",
     "NIST RA-5", "Must", "Implemented"),
    ("BR-106", "Maintain a plan of action and milestones for open findings",
     "NIST CA-5", "Must", "Implemented"),
    ("BR-107", "Retain review records for seven years", "NARA / FAR", "Must",
     "Partial"),
    ("BR-108", "Provide point-in-time database recovery", "NIST CP-9", "Must",
     "Implemented"),
    ("BR-109", "Document and rehearse a restoration procedure", "NIST CP-10",
     "Must", "Partial"),
], BRS_W)

D.h2("A.8 Requirement counts")
D.table(["Group", "Count"],
        [("Task 1 — Kick-off and onboarding", "6"),
         ("Task 2 — Methodology and control framework", "17"),
         ("Task 3 — Retrospective review", "20"),
         ("Task 4 — Ongoing review", "8"),
         ("Task 5 — Priority reviews", "7"),
         ("Task 6 — Contract closeout", "7"),
         ("Cross-cutting", "20"),
         ("Total business requirements", "85")],
        widths=(4.0, 1.2))
D.page_break()

# ── PART B ───────────────────────────────────────────────────────────────────
D.h1("Part B — Functional Requirements Specification (FRS)")
D.p("Functional requirements state what the system does to satisfy a business "
    "requirement, and the acceptance criterion by which satisfaction is judged.")
FR_COLS = ["FR ID", "BR", "Functional Requirement", "Module", "Pri.",
           "Acceptance Criteria"]
FR_W = (0.55, 0.5, 2.1, 1.0, 0.4, 2.0)
D.table(FR_COLS, [
    ("FR-001", "BR-030", "Compute sample size from population, confidence "
     "level, margin of error and expected proportion using Cochran's formula "
     "with finite population correction", "sampling_engine", "Must",
     "Given a population and parameters, the computed size matches the "
     "hand-calculated Cochran value"),
    ("FR-002", "BR-049", "Draw a random sample from a supplied seed and return "
     "that seed with the sample", "sampling_engine", "Must",
     "Re-drawing with the returned seed yields an identical entity set"),
    ("FR-003", "BR-020", "Compute a Wilson score confidence interval for the "
     "observed discrepancy rate", "sampling_engine", "Must",
     "Interval remains within [0,1] at counts of 0 and n"),
    ("FR-004", "BR-032", "Query NPPES by NPI and record found / not found plus "
     "the raw payload", "connectors", "Must",
     "A known-good NPI returns found=true; an unassigned NPI returns "
     "found=false"),
    ("FR-005", "BR-033", "Query PECOS enrolment status by NPI", "connectors",
     "Must", "Returns enrolment state or an explicit unavailable result"),
    ("FR-006", "BR-034", "Query the OIG LEIE exclusion list and report "
     "excluded or clear", "connectors", "Must",
     "An excluded party returns excluded=true; absence returns clear"),
    ("FR-007", "BR-035", "Query SAM.gov Entity Management (v3) for "
     "registration status", "connectors", "Must",
     "With a valid key, an Active registration returns "
     "registration_current=true"),
    ("FR-008", "BR-035", "Query SAM.gov Exclusions (v4) independently of the "
     "registration record", "connectors", "Must",
     "Debarred entity returns excluded=true even with no SAM registration"),
    ("FR-009", "BR-035", "Fall back to legal-name search when no UEI is held, "
     "flagging multi-match results as ambiguous", "connectors", "Should",
     "A name matching more than one entity returns ambiguous=true and is not "
     "auto-resolved"),
    ("FR-010", "BR-047", "Represent every source outcome as one of verified, "
     "not_found, not_checked, unavailable or failed", "review_service", "Must",
     "An unreachable source yields unavailable, never not_found"),
    ("FR-011", "BR-038", "Evaluate verification results against active rules "
     "in priority order and return the first match", "bucket_classifier",
     "Must", "Classification cites the matching rule code and version"),
    ("FR-012", "BR-017", "Evaluate disqualifying (B4) rules before all others",
     "bucket_classifier", "Must",
     "An excluded entity with otherwise clean sources classifies B4, not B1"),
    ("FR-013", "BR-022", "Store rule version on every classification record",
     "review_service", "Must",
     "Each review record carries the rule version that produced it"),
    ("FR-014", "BR-023", "Load classification rules from the database rather "
     "than code", "bucket_classifier", "Must",
     "A rule change takes effect without redeployment"),
    ("FR-015", "BR-016", "Route B3 classifications to a human review queue",
     "review_routes", "Must", "B3 records appear as pending until resolved"),
    ("FR-016", "BR-016", "Record reviewer resolution, rationale and timestamp "
     "on B3 resolution", "review_routes", "Must",
     "Resolved record shows resolution, rationale and effective bucket"),
    ("FR-017", "BR-046", "Validate NPI check digits and flag failures without "
     "rejecting the import", "csv_import", "Must",
     "An invalid NPI imports with a flag and an audit entry"),
    ("FR-018", "BR-045", "Store a hash and timestamp of every source response",
     "connectors", "Must", "Each verification record carries response_hash"),
    ("FR-019", "BR-039", "Generate a weekly report containing all mandatory "
     "sections", "report_generator", "Must",
     "Report contains ten sections; limitations is never empty"),
    ("FR-020", "BR-051", "Generate a quarterly report including a per-week "
     "trend series", "report_generator", "Must",
     "Quarterly report contains a week-indexed B1–B4 series"),
    ("FR-021", "BR-048", "Export an archived report to Excel without "
     "recomputation", "report_excel", "Should",
     "Excel figures match the archived report exactly"),
    ("FR-022", "BR-097", "Record an audit entry for every state-changing "
     "action", "audit", "Must",
     "Audit write failure never aborts the originating transaction"),
    ("FR-023", "BR-098", "Enforce minimum role on every protected endpoint",
     "security", "Must",
     "Insufficient role returns 403; missing token returns 401"),
    ("FR-024", "BR-101", "Invalidate existing sessions when a password changes",
     "admin_users", "Must", "Prior tokens are rejected after a password set"),
    ("FR-025", "BR-102", "Rate-limit login attempts per source address",
     "security", "Must", "Excess attempts return 429"),
    ("FR-026", "BR-031", "Import entities from CSV, recording per-row errors "
     "on the batch", "csv_import", "Must",
     "A batch reporting n errors exposes n error detail entries"),
    ("FR-027", "BR-053", "Support entity lifecycle state transitions subject "
     "to a state machine", "lifecycle", "Must",
     "An invalid transition is refused and the refusal is audited"),
    ("FR-028", "BR-107", "Soft-delete entities so referenced evidence is not "
     "orphaned", "routes", "Should",
     "Deleted entity leaves listings but its review records remain resolvable"),
    ("FR-029", "BR-062", "Assess and store severity on priority review "
     "findings", "review_routes", "Must", "Severity persisted and reportable"),
    ("FR-030", "BR-026", "Expose connector operational status on a health "
     "endpoint", "main", "Should",
     "Health response lists each connector and whether it is live"),
], FR_W, font_size=7.5)
D.page_break()

# ── PART C ───────────────────────────────────────────────────────────────────
D.h1("Part C — Non-Functional Requirements")
D.table(["NFR ID", "Category", "Requirement", "Target", "Measured", "Status"],
        [("NFR-01", "Performance", "CSV import throughput",
          "100 entities in under 30 s", "50 rows in 11.94 s (measured)",
          "Implemented"),
         ("NFR-02", "Performance", "Entity verification latency",
          "Under 5 s per entity", "1.84 s mean, n=10 (measured)",
          "Implemented"),
         ("NFR-03", "Performance", "Report generation",
          "Under 10 s", "0.86 s weekly, 0.90 s quarterly (measured)",
          "Implemented"),
         ("NFR-04", "Performance", "Read endpoint latency",
          "Under 3 s", "0.71–1.36 s mean across four endpoints (measured)",
          "Implemented"),
         ("NFR-05", "Availability", "Uptime during business hours",
          "99.5%", "Not measured — no continuous monitoring", "Partial"),
         ("NFR-06", "Scalability", "Entity population supported",
          "96,000+", "Not load tested at population scale", "Partial"),
         ("NFR-07", "Scalability", "Concurrent request handling",
          "Not specified", "Not Executed — no load test performed",
          "Not Implemented"),
         ("NFR-08", "Security", "Control baseline",
          "NIST SP 800-53 Rev 5 Moderate", "0 High findings from static "
          "analysis; 3 High accepted with rationale", "Partial"),
         ("NFR-09", "Security", "Transport encryption", "TLS 1.2 or higher",
          "TLS enforced on all hosts", "Implemented"),
         ("NFR-10", "Security", "Authentication", "Bearer token with expiry",
          "JWT with role claims; revocation on credential change",
          "Implemented"),
         ("NFR-11", "Accessibility", "Conformance",
          "Section 508 / WCAG 2.2 AA", "Not independently audited", "Partial"),
         ("NFR-12", "Maintainability", "Rule change without code change",
          "Required", "Rules are database rows with versions and effective "
          "dates", "Implemented"),
         ("NFR-13", "Auditability", "Action traceability",
          "Every state change recorded", "Immutable audit log with actor, IP "
          "and timestamp", "Implemented"),
         ("NFR-14", "Recoverability", "Point-in-time restore",
          "14 days production", "14-day retention verified on the production "
          "server", "Implemented"),
         ("NFR-15", "Recoverability", "Recovery time objective",
          "To be established", "Not Executed — restore never rehearsed",
          "Not Implemented"),
         ("NFR-16", "Portability", "Deployment target",
          "Azure App Service (Linux)", "Deployed and verified on dev and "
          "production", "Implemented")],
        widths=(0.55, 0.85, 1.55, 1.15, 1.7, 0.75), font_size=7.5)
D.callout("NFR-05, NFR-06, NFR-07 and NFR-15 are reported against what was "
          "actually measured. No load test, soak test or restore rehearsal has "
          "been performed, so no availability, scalability or recovery-time "
          "figure is claimed.", "EVIDENCE")
D.page_break()

# ── PART D ───────────────────────────────────────────────────────────────────
D.h1("Part D — Requirements Traceability Matrix (RTM)")
D.p("Each row traces a requirement from its contractual source through to the "
    "component that satisfies it and the evidence that demonstrates it.")
D.table(["Req ID", "SOW", "Description", "Component", "Status", "Test Evidence"],
        [("BR-014/017", "Task 2", "B1–B4 classification criteria",
          "bucket_classifier", "Implemented", "test_rules_engine.py (39 tests)"),
         ("BR-018/019", "Task 2", "Cochran sampling with FPC",
          "sampling_engine", "Implemented", "test_review_reports.py"),
         ("BR-020", "Task 2", "Wilson confidence intervals", "sampling_engine",
          "Implemented", "test_review_reports.py"),
         ("BR-022", "Task 2", "Rule versioning", "review_rules table",
          "Implemented", "v1 retired, v2 active — verified on dev"),
         ("BR-032", "Task 3", "NPPES verification", "connectors",
          "Implemented", "AGT-TE-005; 5/5 reachable, 391 ms mean"),
         ("BR-033", "Task 3", "PECOS verification", "connectors",
          "Implemented", "AGT-TE-005; 5/5 reachable, 242 ms mean"),
         ("BR-034", "Task 3", "OIG LEIE verification", "connectors",
          "Implemented", "AGT-TE-005; 5/5 reachable, 428 ms mean"),
         ("BR-035", "Task 3", "SAM.gov verification", "connectors",
          "Partial", "Built; 0/5 reachable — API key not provisioned"),
         ("BR-036", "Task 3", "TEFCA entity data verification", "—",
          "Not Implemented", "No direct access; ONC-provided"),
         ("BR-037", "Task 3", "State registry verification", "—",
          "Not Implemented", "No standardised interface — see strategy paper"),
         ("BR-038", "Task 3", "Entity classification", "review_service",
          "Implemented", "AGT-TE-005 — 24 of 25 operational tests passed"),
         ("BR-039/044", "Task 3", "Weekly report with limitations",
          "report_generator", "Implemented",
          "docs/audit/WEEKLY_REPORT_SAMPLE.md"),
         ("BR-046", "Task 3", "NPI check digit validation", "npi_validator",
          "Implemented", "test_rules_engine.py"),
         ("BR-047", "Task 3", "Outage vs finding distinction", "review_service",
          "Implemented", "test_rules_engine.py"),
         ("BR-051/054", "Task 4", "Quarterly report with weekly trend",
          "report_generator", "Implemented", "test_review_reports.py"),
         ("BR-060/065", "Task 5", "Priority review workflow", "review_routes",
          "Implemented", "AGT-TE-005"),
         ("BR-070/076", "Task 6", "Closeout deliverables", "—", "Deferred",
          "Scheduled for closeout period"),
         ("BR-097", "Cross", "Immutable audit trail", "audit",
          "Implemented", "test_monday_workflow.py"),
         ("BR-098", "Cross", "Role-based access control", "security",
          "Implemented", "AGT-TE-006 — 5/5 RBAC scenarios passed"),
         ("BR-101", "Cross", "Session termination on credential change",
          "admin_users", "Implemented",
          "Verified: old credential returns 401 after rotation"),
         ("BR-103", "Cross", "Static application security testing", "CI",
          "Implemented", "Bandit — 0 High, 12 Medium, 124 Low"),
         ("BR-104", "Cross", "Dynamic application security testing", "CI",
          "Partial", "Two pipelines built; Not Executed"),
         ("BR-105", "Cross", "Software composition analysis", "CI",
          "Implemented", "pip-audit — 1 finding, accepted (RA-004)"),
         ("BR-108", "Cross", "Point-in-time restore", "Azure PostgreSQL",
          "Implemented", "14-day retention verified"),
         ("BR-109", "Cross", "Restore rehearsal", "—", "Partial",
          "Procedure documented; Not Executed")],
        widths=(0.75, 0.5, 1.55, 1.15, 0.75, 1.85), font_size=7.5)
D.page_break()

# ── PART E ───────────────────────────────────────────────────────────────────
D.h1("Part E — Business Rules Catalog")
D.p("These are the invariants that govern platform behaviour. Several exist "
    "because their absence produced a defect that reached testing; those are "
    "noted, since a rule with a known failure behind it is more likely to be "
    "respected than one asserted abstractly.")
D.table(["Rule ID", "Name", "Description", "Rationale / Origin"],
        [("BR-RULE-001", "B4 evaluates first",
          "Disqualifying rules carry priority 5 and are evaluated before any "
          "B1/B2/B3 rule",
          "At priority 50 an excluded entity with clean NPPES and PECOS matched "
          "the B1 rule and was reported 'No Discrepancy' — the most "
          "consequential error the engine could make"),
         ("BR-RULE-002", "Query success is not a verdict",
          "A connector's success flag means the query completed; the finding "
          "lives in the payload",
          "Reading success as 'entity is clean' classified every entity as "
          "excluded in early testing"),
         ("BR-RULE-003", "Unavailable is not not_found",
          "Unavailable means the source did not answer and is excluded from "
          "scoring; not_found means the source answered 'no record' and is a "
          "finding",
          "Conflating them either manufactures findings from outages or hides "
          "real findings behind them"),
         ("BR-RULE-004", "Coverage counts implemented connectors only",
          "Confidence is computed over connectors that exist, not over every "
          "conceivable source",
          "Counting unbuilt connectors as gaps makes full coverage unreachable "
          "by construction and the platform appear broken rather than "
          "incomplete"),
         ("BR-RULE-005", "B3 requires human resolution",
          "B3 classifications are never auto-resolved",
          "B3 exists precisely for cases automated logic cannot settle"),
         ("BR-RULE-006", "Review records are append-only",
          "Classifications are not updated or deleted; resolutions are "
          "recorded as new state",
          "An auditor must be able to see what was concluded at the time, not "
          "only the latest view"),
         ("BR-RULE-007", "Reports are archived as delivered",
          "Exports derive from the archived report data, never recomputed",
          "A report rendering different figures as Excel than as HTML would be "
          "worse than no export"),
         ("BR-RULE-008", "Real NPIs on development only",
          "Production is never seeded with test identifiers",
          "Production data must originate from ONC"),
         ("BR-RULE-009", "Samples are reproducible",
          "The random seed is returned with the sample and reproduces it "
          "exactly",
          "A sample that cannot be reproduced cannot be defended"),
         ("BR-RULE-010", "Rule version recorded on every classification",
          "Each review record stores the rule version applied",
          "Determines exactly which records a methodology change affects"),
         ("BR-RULE-011", "SAM is a disqualifier, not a requirement",
          "SAM conditions fire only on a positive finding",
          "Requiring SAM verification for B1 would drop the entire registry out "
          "of B1 while SAM has no API key"),
         ("BR-RULE-012", "Retire rules, never delete them",
          "Superseded rule versions are marked retired and kept",
          "Historical classifications cite them; deletion orphans the record"),
         ("BR-RULE-013", "Invalid NPIs flag, never reject",
          "An NPI failing its check digit imports with a flag",
          "Existing registry data predates the rule; refusing the import would "
          "break a working system to enforce it"),
         ("BR-RULE-014", "Audit writes never abort the transaction",
          "An audit failure is logged but does not roll back the action",
          "Losing the action because its audit entry failed is worse than the "
          "missing entry"),
         ("BR-RULE-015", "Limitations sections cannot be empty",
          "Every report includes disclosed limitations",
          "An empty limitations section reads as 'no limitations', which is "
          "never true"),
         ("BR-RULE-016", "Deletes are soft",
          "Entities are flagged deleted, not removed",
          "Review records, verifications and samples reference them; a hard "
          "delete orphans reported evidence"),
         ("BR-RULE-017", "Delete is not silently idempotent",
          "Deleting an already-deleted entity returns 409",
          "A cleanup reporting success for rows it did not touch hides a "
          "targeting bug"),
         ("BR-RULE-018", "Null confidence is not zero confidence",
          "When nothing answered, confidence is null",
          "Zero means 'measured and bad'; null means 'not measured'"),
         ("BR-RULE-019", "Only answering sources can conflict",
          "A conflict requires two sources that both responded",
          "Otherwise an outage is reported as a disagreement"),
         ("BR-RULE-020", "Unbuilt connectors report not_checked",
          "Never unavailable",
          "'Unavailable' implies recovery and invites a retry; 'not "
          "implemented' needs a decision"),
         ("BR-RULE-021", "Sessions terminate on credential change",
          "Password change stamps a revocation time",
          "NIST SP 800-63B / OWASP ASVS"),
         ("BR-RULE-022", "Import errors must be recoverable",
          "A batch reporting n errors exposes n error details",
          "A count with no detail leaves an auditor unable to act on it"),
         ("BR-RULE-023", "Dead sources are deactivated only on repeat "
          "confirmation",
          "Two independent probes must agree before a source is disabled",
          "A single aggressive sweep over-reported failures by 34%"),
         ("BR-RULE-024", "DAST findings are not defects until reproduced",
          "Every finding is manually reproduced before any code change",
          "Both findings in the first Security Validation run were false "
          "positives in the tests, not application defects")],
        widths=(0.85, 1.15, 2.05, 2.45), font_size=7.5)
D.page_break()

# ── PART F ───────────────────────────────────────────────────────────────────
D.h1("Part F — Gap Analysis")
D.table(["Gap ID", "Requirement", "Current State", "Gap", "Pri.",
         "Recommendation", "Effort"],
        [("G-01", "BR-035 SAM.gov verification",
          "Connector built for both v3 and v4; no API key",
          "Federal registration and debarment unverified", "High",
          "Obtain a SAM.gov public API key via the entity account", "Low"),
         ("G-02", "BR-035 SAM exact matching",
          "Registry holds no UEI", "Matching is name-based and can be ambiguous",
          "High", "Request UEI in ONC-supplied entity data", "Medium"),
         ("G-03", "BR-036 TEFCA entity data",
          "No direct access", "RCE attributes unverified", "High",
          "Obtain data extract from ONC", "Low"),
         ("G-04", "BR-037 State registries",
          "Not implemented", "State licensure unverified", "Medium",
          "Defer pending ONC guidance; document as not checked", "High"),
         ("G-05", "BR-104 DAST",
          "Two CI pipelines built, never run",
          "No dynamic testing evidence", "High",
          "Set HAWK_API_KEY and trigger both workflows", "Low"),
         ("G-06", "NFR-15 Recovery time objective",
          "Restore documented, never rehearsed", "RTO unmeasured", "High",
          "Rehearse a restore into a scratch server and record actual RTO",
          "Low"),
         ("G-07", "NFR-05/06/07 Load characteristics",
          "Single-request serial measurements only",
          "Availability and scalability unproven", "Medium",
          "Run a load test at representative population scale", "Medium"),
         ("G-08", "BR-090 Secret management",
          "Secrets in App Service configuration",
          "No rotation or access audit via Key Vault", "Medium",
          "Complete Key Vault migration from an approved network", "Low"),
         ("G-09", "NFR-11 Accessibility",
          "Not independently audited",
          "Section 508 conformance unverified", "Medium",
          "Commission an accessibility audit", "Medium"),
         ("G-10", "BR-107 Seven-year retention",
          "No automated retention or purge policy",
          "Retention asserted but not enforced", "Medium",
          "Implement archival and purge jobs per the retention schedule",
          "Medium"),
         ("G-11", "Production host currency",
          "Customer-facing host served by the legacy platform",
          "Deployed fixes are not live on that host", "High",
          "Execute the documented DNS cutover", "Low"),
         ("G-12", "Bulletin source coverage",
          "58 feeds refuse the collector with 401/403",
          "Recoverable coverage lost to bot protection", "Low",
          "Adjust request headers and re-probe", "Low")],
        widths=(0.5, 1.15, 1.25, 1.2, 0.4, 1.4, 0.5), font_size=7.5)
D.page_break()

D.h1("Revision History")
D.table(["Version", "Date", "Author", "Description"],
        [("1.0", "August 2026", "Imran Siddiqui", "Initial release")],
        widths=(0.9, 1.3, 1.7, 2.6))

path = OUT / "AGT-REQ-001_Requirements_Specification.docx"
D.save(path)
print(f"saved {path.name}: {path.stat().st_size:,} bytes")
