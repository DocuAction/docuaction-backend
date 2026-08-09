"""AGT-EA-001 — Volume III, Enterprise Architecture."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402

OUT = pathlib.Path(r"C:\Imran_Coding projects\DocuAction\backend\docs\enterprise")
OUT.mkdir(parents=True, exist_ok=True)

D = AGTDoc(doc_id="AGT-EA-001",
           title="DocuAction TEFCA ARC Platform",
           subtitle="Prepared for the Assistant Secretary for Technology Policy / "
                    "Office of the National Coordinator for Health IT (ASTP/ONC)",
           version="1.0", date="August 2026")
D.cover("Volume III — Enterprise Architecture")
D.doc_control([
    ("Document ID", "AGT-EA-001"),
    ("Document Title", "Volume III — Enterprise Architecture"),
    ("Version", "1.0"), ("Status", "Released"), ("Date", "August 2026"),
    ("Contract Number", "7571MN26F80064"),
    ("Contractor", "Alliance Global Tech, Inc. (AGT)"),
    ("CAGE / UEI", "8ERE8 / MP2FLV1MAW93"),
    ("Author", "Imran Siddiqui, Chief Executive Officer"),
    ("Classification", "CONFIDENTIAL — Controlled Unclassified Information (CUI)"),
    ("Related Documents", "AGT-EX-001 (Executive), AGT-REQ-001 (Requirements), "
                          "AGT-DA-001 (Data Architecture), AGT-SD-001 (System "
                          "and Module Design)"),
])
D.page_break()
D.toc()

# ── 1 ────────────────────────────────────────────────────────────────────────
D.h1("1. Enterprise Architecture Overview")
D.h2("1.1 Purpose")
D.p("This volume describes the architecture of the DocuAction platform across "
    "the business, application, information, integration, security, "
    "infrastructure and operations layers. It states how the platform is "
    "organised and why, so that a reviewer can judge whether the structure "
    "supports the contractual obligations rather than only whether the "
    "individual features exist.")

D.h2("1.2 Relationship to the other volumes")
D.table(["Document", "Title", "Relationship to this volume"],
        [("AGT-EX-001", "Executive Documentation",
          "Supplies scope, assumptions, constraints and the risk register that "
          "this architecture must operate within. Constraints in §6 of that "
          "volume drive the integration design in §5 here."),
         ("AGT-REQ-001", "Requirements Specification",
          "Supplies the requirements this architecture satisfies. Control "
          "mapping in §6.2 here traces to the cross-cutting requirements "
          "BR-090 to BR-109."),
         ("AGT-DA-001", "Enterprise Data Architecture",
          "Owns the data model. §4 here summarises and cross-references it "
          "rather than restating it, so the two cannot disagree."),
         ("AGT-SD-001", "System and Module Design",
          "Specifies the internal design of each module named in §3.2 here. "
          "This volume says what the modules are; that one says how each works.")],
        widths=(1.1, 1.6, 3.8))
D.callout("Where this volume and AGT-DA-001 both touch the data model, "
          "AGT-DA-001 is authoritative. Where this volume and AGT-SD-001 both "
          "touch module internals, AGT-SD-001 is authoritative. Duplicating "
          "detail across volumes is how documentation sets start contradicting "
          "themselves.", "PRECEDENCE")

D.h2("1.3 Architecture governance")
D.table(["Aspect", "Approach"],
        [("Decision record", "Architecturally significant decisions are "
                             "recorded with the reasoning and the rejected "
                             "alternative, not only the outcome"),
         ("Change control", "Contract-affecting changes require ONC "
                            "concurrence; internal changes follow the "
                            "deployment gate"),
         ("Deployment gate", "Tests pass and /health returns 200 before any "
                             "environment is promoted"),
         ("Methodology change", "Handled as data — a new rule version with an "
                                "effective date; the prior version is retired, "
                                "never deleted"),
         ("API contract", "Frozen as version 1.0 with a documented breaking-"
                          "change policy (docs/API_VERSION_1.0_BASELINE.md)"),
         ("Security review", "Static analysis on every push; dynamic testing "
                             "scheduled weekly against development only"),
         ("Risk acceptance", "Recorded formally with rationale, compensating "
                             "controls and a review date")],
        widths=(1.5, 5.0))
D.page_break()

# ── 2 ────────────────────────────────────────────────────────────────────────
D.h1("2. Business Architecture")
D.h2("2.1 Business Capability Map")
D.table(["Domain", "Capabilities", "Module", "Contract Task"],
        [("Entity Verification",
          "Import, validate NPI, verify against authoritative sources, "
          "classify B1–B4", "TEFCA ARC", "Tasks 2, 3"),
         ("Statistical Analysis",
          "Cochran sampling, finite population correction, stratification, "
          "Wilson confidence intervals", "TEFCA ARC", "Tasks 2, 3"),
         ("Review Management",
          "Review cycles, review records, B3 human resolution, priority review",
          "TEFCA ARC", "Tasks 3, 4, 5"),
         ("Reporting",
          "Weekly, quarterly, priority reports; Excel export; immutable "
          "archival", "TEFCA ARC", "Tasks 3, 4, 5"),
         ("News Intelligence",
          "Collection, AI classification, briefing generation, delivery",
          "Bulletin", "Out of scope — separate contract"),
         ("Document Processing",
          "Upload, analysis, comparison, extraction", "Document",
          "Out of scope"),
         ("Authentication",
          "Login, JWT issuance, RBAC enforcement, account lifecycle", "Auth",
          "Cross-cutting"),
         ("Audit and Compliance",
          "Immutable audit log, evidence retention, assessment packages",
          "Cross-cutting", "Cross-cutting")],
        widths=(1.25, 2.5, 1.05, 1.2))

D.h2("2.2 Business Process Flows")
D.h3("2.2.1 Entity verification")
D.diagram([
    " ONC CSV extract",
    "      |",
    "      v",
    " [ Import ] --> per-row errors recorded on the batch",
    "      |",
    "      v",
    " [ NPI check digit ] --> invalid: FLAG + audit (never reject)",
    "      |",
    "      v",
    " [ Verify ] --+--> NPPES     --> verified | not_found | unavailable",
    "              +--> PECOS     --> verified | not_found | unavailable",
    "              +--> OIG LEIE  --> clear    | excluded  | unavailable",
    "              +--> SAM.gov   --> not_checked (no API key)",
    "      |",
    "      v",
    " [ Rules engine ]  priority order, first match wins",
    "      |            B4 evaluated FIRST (priority 5)",
    "      v",
    " B1 / B2 / B3 / B4  --> Review ID  REV-YYYY-NNNNNN",
    "      |",
    "      +--> B3 --> human resolution queue",
    "      |",
    "      v",
    " [ Audit log ]  actor, action, entity, IP, timestamp",
], "Figure 1. Entity verification. A source that does not answer yields "
   "'unavailable' and is excluded from scoring; it never becomes 'not_found'.")

D.h3("2.2.2 Statistical sampling")
D.diagram([
    " Define parameters",
    "   confidence 95% | margin of error | expected proportion",
    "      |",
    "      v",
    " [ Cochran n0 ]  n0 = z^2 * p * (1-p) / e^2",
    "      |",
    "      v",
    " [ Finite population correction ]  n = n0 / (1 + (n0-1)/N)",
    "      |",
    "      v",
    " [ Draw stratified random sample ]  seed recorded with the sample",
    "      |",
    "      v",
    " [ Assign to reviewers ]  sample_entities",
    "      |",
    "      v",
    " [ Track completion ]  review_cycles",
], "Figure 2. Sampling. The seed is returned with the sample; re-drawing with "
   "it reproduces the identical entity set, which is what makes a reported "
   "sample defensible.")

D.h3("2.2.3 Weekly reporting")
D.diagram([
    " [ Query completed reviews for the period ]",
    "      |",
    "      v",
    " [ B1–B4 distribution ]  counts must sum to the reviewed total",
    "      |",
    "      v",
    " [ Discrepancy rate ]  (B2+B3+B4) / reviewed",
    "      |",
    "      v",
    " [ Wilson score confidence interval ]  valid at 0 and at n",
    "      |",
    "      v",
    " [ Document data sources actually used ]",
    "      |",
    "      v",
    " [ LIMITATIONS ]  mandatory, cannot be empty",
    "      |",
    "      v",
    " [ Generate JSON + HTML ] --> [ Archive immutably ] --> ONC delivery",
    "                                     |",
    "                                     +--> Excel export reads the ARCHIVE",
], "Figure 3. Weekly reporting. Excel export derives from the archived payload "
   "and never recomputes, so two renderings of one report cannot disagree.")

D.h3("2.2.4 B3 resolution")
D.diagram([
    " Entity classified B3 (Inexplicable)",
    "      |",
    "      v",
    " [ Pending human review queue ]   never auto-resolved",
    "      |",
    "      v",
    " [ Reviewer examines the recorded evidence ]",
    "      |   source responses, response hashes, rule version",
    "      v",
    " [ Decision ] --+--> confirm B3",
    "                +--> reclassify to B1 / B2 / B4",
    "      |",
    "      v",
    " [ Record resolution ]  rationale + reviewer + timestamp",
    "      |                 APPEND, never an update over the original",
    "      v",
    " effective_bucket set; original classification still readable",
], "Figure 4. B3 resolution. The original classification remains legible after "
   "resolution — an auditor must see what was concluded at the time, not only "
   "the latest view.")

D.h3("2.2.5 Priority review (Task 5)")
D.diagram([
    " ONC flags an entity",
    "      |",
    "      v",
    " [ POST /priority-review ]  admin only",
    "      |",
    "      v",
    " [ Immediate verification across all operational connectors ]",
    "      |",
    "      v",
    " [ Classification ]",
    "      |",
    "      v",
    " [ Root cause analysis ] --> [ Severity assessment ]",
    "      |",
    "      v",
    " [ Corrective recommendations ]",
    "      |",
    "      v",
    " [ Priority review report ] --> included in the quarterly aggregate",
], "Figure 5. Priority review. The same verification and classification path is "
   "used as for scheduled review, so a priority finding is comparable with a "
   "routine one.")

D.h2("2.3 Organisational Roles")
D.p("Stakeholders are enumerated in AGT-EX-001 §8. This table maps each role to "
    "the system capability it exercises.")
D.table(["Role (AGT-EX-001 §8)", "Organisation", "System capability exercised"],
        [("Technical Lead", "ASTP/ONC",
          "Reviews methodology and findings; requests priority reviews"),
         ("Technical Monitor", "ASTP/ONC",
          "Reviews deliverables and reported figures"),
         ("Methodology Reviewer", "ASTP/ONC",
          "Reviews and concurs on classification rules and sampling design"),
         ("Contracting Officer's Representative", "ASTP/ONC",
          "Accepts deliverables; receives closeout artefacts"),
         ("Contracting Officer", "HHS OMAS",
          "Contract administration; no platform access required"),
         ("Chief Executive / Technical Lead", "AGT",
          "Platform administration, rule authoring, user management"),
         ("Programme Manager", "AGT",
          "Cycle management, deliverable submission, full audit log access"),
         ("Data Analyst", "AGT",
          "Import, verification execution, sample drawing"),
         ("Senior Healthcare IT Advisor", "AGT",
          "Methodology advice; read access to reports")],
        widths=(1.8, 1.1, 3.6))
D.page_break()

# ── 3 ────────────────────────────────────────────────────────────────────────
D.h1("3. Application Architecture")
D.h2("3.1 Application Portfolio")
D.table(["Application", "Purpose", "Technology", "Status"],
        [("DocuAction Backend", "API and business logic",
          "FastAPI 0.140 / Python 3.12 target, 3.13 development", "Production"),
         ("DocuAction Frontend", "User interface and dashboards",
          "Next.js 16.2.9 / React 18.3.1 (static export)", "Production"),
         ("Security Scanner", "Automated security assessment",
          "Python command-line tool", "Operational"),
         ("CI/CD Pipeline", "Build, test, deploy, security scanning",
          "GitHub Actions", "Operational"),
         ("DAST Pipelines", "Dynamic application security testing",
          "OWASP ZAP and StackHawk via GitHub Actions",
          "Built — not yet executed")],
        widths=(1.4, 1.9, 2.1, 1.1))
D.callout("Framework versions above were read from the deployed application and "
          "the frontend manifest, not from the proposal. The frontend is "
          "Next.js 16.2.9 with React 18.3.1. Two High-severity advisories "
          "affect this framework and are formally accepted (AGT-EX-001 §7, "
          "RA-001 and RA-002) because the only available remediation downgrades "
          "the framework by two major versions and breaks the build.",
          "VERIFIED")

D.h2("3.2 Module Dependency Map")
D.table(["Module", "Depends On", "Depended On By"],
        [("Auth", "Database", "Every other module"),
         ("Database access", "PostgreSQL", "All modules"),
         ("Connectors", "External federal APIs, Auth",
          "TEFCA ARC verification"),
         ("TEFCA ARC", "Auth, Database, Connectors", "Reporting, Analytics"),
         ("Rules engine", "Database (review_rules)", "TEFCA ARC"),
         ("Sampling engine", "Database", "TEFCA ARC, Reporting"),
         ("Reporting", "TEFCA ARC, Sampling engine, Database", "Analytics"),
         ("Bulletin", "Auth, Database, Anthropic API", "None"),
         ("Document Processing", "Auth, Database, Anthropic API", "None"),
         ("Audio Transcription", "Auth, OpenAI API", "None"),
         ("Audit", "Database", "All modules")],
        widths=(1.5, 2.5, 2.5))
D.p("The dependency graph is deliberately acyclic and shallow. Bulletin and "
    "Document Processing depend on shared infrastructure but nothing depends on "
    "them, which is what allows them to be out of contract scope without "
    "affecting the TEFCA ARC deliverables.")

D.h2("3.3 Application Layering")
D.diagram([
    " +--------------------------------------------------------------+",
    " |  PRESENTATION      Next.js static export, Azure Static Web App |",
    " +--------------------------------------------------------------+",
    "                            | HTTPS / JSON",
    " +--------------------------------------------------------------+",
    " |  API               FastAPI routers, request validation,        |",
    " |                    RBAC guard, rate limiting, error handler    |",
    " +--------------------------------------------------------------+",
    " +--------------------------------------------------------------+",
    " |  BUSINESS LOGIC    bucket_classifier, sampling_engine,         |",
    " |                    report_generator, lifecycle state machine   |",
    " +--------------------------------------------------------------+",
    " +--------------------------------------------------------------+",
    " |  DATA ACCESS       SQLAlchemy async ORM, queries module        |",
    " +--------------------------------------------------------------+",
    " +--------------------------------------------------------------+",
    " |  INTEGRATION       NPPES | PECOS | OIG LEIE | SAM.gov          |",
    " |                    common SourceResult contract               |",
    " +--------------------------------------------------------------+",
    " +--------------------------------------------------------------+",
    " |  INFRASTRUCTURE    Azure App Service (Linux), PostgreSQL 16    |",
    " +--------------------------------------------------------------+",
], "Figure 6. Application layering. Business logic holds no HTTP or database "
   "concerns, which is why the classification engine can be exercised "
   "deterministically in tests without a database.")
D.page_break()

# ── 4 ────────────────────────────────────────────────────────────────────────
D.h1("4. Information Architecture")
D.h2("4.1 Information Domains")
D.p("The data model is specified in AGT-DA-001. Summarised here for "
    "architectural context only.")
D.table(["Domain", "Content", "Tables", "Authoritative source"],
        [("Entity", "Registry population, identifiers, hierarchy, versions",
          "8", "ONC-supplied extract"),
         ("Verification", "Per-source results, payloads, response hashes",
          "5", "NPPES, PECOS, OIG LEIE"),
         ("Review", "Rules, classifications, samples, cycles, reports", "7",
          "DocuAction platform"),
         ("Platform", "Users, roles, immutable audit trail", "2",
          "DocuAction platform"),
         ("Bulletin", "Articles, briefings, sources, cost telemetry", "8",
          "Public RSS and news APIs")],
        widths=(1.1, 2.7, 0.6, 2.1))

D.h2("4.2 Data Classification Summary")
D.p("Full per-table classification is in AGT-DA-001 §8.")
D.table(["Classification", "Applies to", "Encryption", "Audit"],
        [("CUI / PII", "Entity tables, identifiers, entity versions", "Yes",
          "Yes"),
         ("CUI", "Verifications, review records, samples, reports", "Yes",
          "Yes"),
         ("PII", "users", "Yes", "Yes"),
         ("INTERNAL", "Import batches, job metadata, rules, cycles, telemetry",
          "Varies", "Yes"),
         ("PUBLIC", "Bulletin articles and briefings", "No", "No")],
        widths=(1.3, 3.1, 1.05, 1.05))

D.h2("4.3 Data Flow Overview")
D.diagram([
    "  EXTERNAL                PLATFORM              STORAGE        OUTPUT",
    "",
    "  NPPES API ------+",
    "  PECOS API ------+",
    "  OIG LEIE   -----+--> [ Connectors ] --> Verification --> PostgreSQL",
    "  SAM.gov (no key)+                        results",
    "  ONC CSV    -----+--> [ Import ]     --> Entities    --> PostgreSQL",
    "",
    "  PostgreSQL --> [ Rules engine ] --> Classification --> review_records",
    "",
    "  review_records --> [ Sampler ] --> Sample --> [ Report generator ]",
    "                                                       |",
    "                                                       v",
    "                                              review_reports (archived)",
    "                                                       |",
    "                                            +----------+----------+",
    "                                            v                     v",
    "                                          HTML                  Excel",
    "                                            |                     |",
    "                                            +------> ONC <--------+",
], "Figure 7. Data flow. Both output renderings read the archived report, never "
   "the live tables.")
D.page_break()

# ── 5 ────────────────────────────────────────────────────────────────────────
D.h1("5. Integration Architecture")
D.h2("5.1 Integration pattern")
D.p("Point-to-point REST. Each connector calls exactly one federal API and "
    "returns a common result object, so the classification engine consumes one "
    "shape regardless of which upstream produced it.")
D.p("Every source outcome is expressed in a five-state model. The states are "
    "not interchangeable and the distinction is the platform's core integrity "
    "property.")
D.table(["State", "Meaning", "Effect on scoring"],
        [("verified", "The source answered and confirmed the entity",
          "Counts toward confidence"),
         ("not_found", "The source answered and has no record — a FINDING",
          "Counts toward confidence; may drive B3"),
         ("not_checked", "No connector exists, or no identifier to query with",
          "Excluded — neither helps nor penalises"),
         ("unavailable", "A connector exists but the source did not answer",
          "Excluded — an outage is not a finding"),
         ("failed", "The source answered with something unusable",
          "Excluded; logged for investigation")],
        widths=(1.0, 3.4, 2.1))
D.callout("Collapsing 'unavailable' into 'not_found' would manufacture findings "
          "out of somebody else's downtime. Collapsing it the other way would "
          "hide real findings behind claimed outages. Both failure modes are "
          "silent to a downstream reader, which is why the distinction is "
          "enforced at the connector boundary rather than left to the caller.",
          "INTEGRITY")

D.h2("5.2 Connector Inventory")
D.p("Operational status below is measured, not asserted. Latency is the mean of "
    "five sequential calls taken during the assessment window; it is not an "
    "availability guarantee. Full detail in AGT-DA-001 §6 and "
    "docs/audit/CONNECTOR_OPERATIONAL_MONITORING.md.")
D.table(["Connector", "Endpoint", "Key required", "Status", "Measured",
         "In scoring"],
        [("NPPES", "CMS NPI Registry", "No", "Operational",
          "5/5, 391 ms mean", "Yes"),
         ("PECOS", "Same CMS NPI dataset", "No", "Operational",
          "5/5, 242 ms mean", "Yes"),
         ("OIG LEIE", "HHS exclusions CSV", "No", "Operational",
          "5/5, 428 ms mean", "Yes"),
         ("SAM.gov v3", "Entity Management", "Yes", "Built, not operational",
          "0/5 — HTTP 404, no key", "No"),
         ("SAM.gov v4", "Exclusions", "Yes", "Built, not operational",
          "0/5 — HTTP 404, no key", "No"),
         ("TEFCA entity data", "The ONC", "N/A", "Not accessible",
          "ONC-provided", "No"),
         ("State registries", "Various", "N/A", "Not implemented",
          "No standard interface", "No"),
         ("IRS", "EIN-keyed", "N/A", "Not implemented",
          "EIN not captured", "No")],
        widths=(1.0, 1.35, 0.7, 1.35, 1.25, 0.85), font_size=7.5)

D.h2("5.3 Integration Error Handling")
D.table(["Scenario", "Behaviour", "Recorded state"],
        [("Request timeout", "Abandon that source, continue the run",
          "unavailable"),
         ("HTTP 4xx", "Log the status, continue", "failed"),
         ("HTTP 5xx", "Retry once, then abandon", "unavailable"),
         ("Network unreachable", "Continue with remaining sources",
          "unavailable"),
         ("Unparseable response", "Log a payload summary, continue", "failed"),
         ("Rate limited (429)", "Stop that provider for the run",
          "unavailable"),
         ("No API key configured", "Do not call; report the dependency",
          "not_checked"),
         ("No identifier to query with", "Do not call", "not_checked")],
        widths=(1.6, 3.2, 1.7))
D.callout("One connector failure NEVER blocks a verification. The run always "
          "returns partial results with the gap disclosed. The alternative — "
          "failing the whole verification because one source was slow — would "
          "make the platform's availability the product of every upstream's "
          "availability.", "RULE")

D.h2("5.4 Future integrations")
D.table(["Integration", "Purpose", "Status"],
        [("Microsoft Entra ID", "Single sign-on",
          "Backend implemented; frontend hand-off not wired"),
         ("SendGrid", "Email notification and delivery",
          "Planned — currently sent over direct HTTP"),
         ("Azure Monitor", "Centralised observability and alerting",
          "Partially configured"),
         ("Azure Key Vault", "Secret storage and rotation",
          "Blocked — vaults firewalled to private link (AGT-EX-001 §6.4)")],
        widths=(1.5, 2.6, 2.4))
D.page_break()

# ── 6 ────────────────────────────────────────────────────────────────────────
D.h1("6. Security Architecture")
D.h2("6.1 Security Layers")
D.table(["Layer", "Control", "Implementation"],
        [("Network", "Transport encryption", "TLS enforced by Azure; SNI "
                                             "certificate bound per host"),
         ("Network", "Host allow-listing",
          "TrustedHostMiddleware — an unlisted host receives 400 on every "
          "route including /health"),
         ("Application", "Input validation",
          "Pydantic models; parameterised queries throughout; defusedxml for "
          "all external XML"),
         ("Application", "Error disclosure",
          "Structured JSON errors; no stack traces, database detail or path "
          "echo in production responses"),
         ("Authentication", "Bearer tokens",
          "JWT HS256 with configurable expiry and role claims"),
         ("Authentication", "Credential storage", "bcrypt password hashing"),
         ("Authentication", "Session termination",
          "tokens_revoked_at stamped on credential change"),
         ("Authorization", "Role-based access control",
          "Router-level dependency enforcing a minimum role"),
         ("Rate limiting", "Per-account lockout",
          "5 failed logins per account per 15 minutes"),
         ("Rate limiting", "Per-address throttle",
          "20 login attempts per IP per 15 minutes; 5 registrations per IP per "
          "hour"),
         ("Rate limiting", "General API",
          "Tiered sliding window, 60 requests per minute default"),
         ("Data", "Encryption at rest", "Azure-managed"),
         ("Audit", "Immutable logging",
          "Append-only audit table; an audit write never aborts its "
          "transaction"),
         ("Operations", "Static analysis", "Bandit and CodeQL on every push"),
         ("Operations", "Dependency scanning", "pip-audit against requirements"),
         ("Operations", "Dynamic testing",
          "OWASP ZAP (unauthenticated) and StackHawk (authenticated), weekly, "
          "development only")],
        widths=(1.1, 1.6, 3.8), font_size=8)

D.h2("6.2 NIST SP 800-53 Rev 5 Control Mapping")
D.p("Traces to the cross-cutting requirements in AGT-REQ-001 §A.7.")
D.table(["Control", "Title", "Implementation", "Status"],
        [("AC-2", "Account Management",
          "Nine-level role hierarchy; account creation, role change and "
          "deactivation are audited", "Implemented"),
         ("AC-3", "Access Enforcement",
          "Minimum-role dependency on every protected router", "Implemented"),
         ("AC-6", "Least Privilege",
          "Roles graded viewer through admin; destructive operations are "
          "admin-only", "Implemented"),
         ("AC-7", "Unsuccessful Logon Attempts",
          "Account lockout at 5 failures per 15 minutes", "Implemented"),
         ("AC-12", "Session Termination",
          "Token revocation timestamp invalidates prior sessions",
          "Implemented"),
         ("AU-2", "Event Logging",
          "Every state-changing action writes an audit entry", "Implemented"),
         ("AU-3", "Content of Audit Records",
          "Actor, action, entity, IP address and timestamp", "Implemented"),
         ("AU-9", "Protection of Audit Information",
          "Append-only; no update or delete path exposed", "Implemented"),
         ("AU-11", "Audit Record Retention",
          "Seven-year retention specified; automated enforcement not yet built",
          "Partial"),
         ("CA-5", "Plan of Action and Milestones",
          "Risk acceptance register with review dates", "Implemented"),
         ("CM-2", "Baseline Configuration",
          "API frozen as version 1.0 with an archived specification",
          "Implemented"),
         ("CM-3", "Configuration Change Control",
          "Deployment gate; rollback artefact retained per release",
          "Implemented"),
         ("CP-9", "System Backup",
          "Automated daily backup, 14-day point-in-time restore in production",
          "Implemented"),
         ("CP-10", "System Recovery",
          "Procedure documented; never rehearsed, so RTO is unmeasured",
          "Partial"),
         ("IA-2", "Identification and Authentication",
          "JWT bearer authentication with role claims", "Implemented"),
         ("IA-5", "Authenticator Management",
          "bcrypt hashing; rotation performed; sessions terminated on change",
          "Implemented"),
         ("RA-5", "Vulnerability Monitoring",
          "Static analysis and dependency scanning on every push; dynamic "
          "testing built but not yet executed", "Partial"),
         ("SC-8", "Transmission Confidentiality",
          "TLS enforced on every host", "Implemented"),
         ("SC-13", "Cryptographic Protection",
          "TLS in transit; Azure-managed encryption at rest", "Implemented"),
         ("SC-28", "Protection of Information at Rest",
          "Azure-managed database and storage encryption", "Implemented"),
         ("SI-10", "Information Input Validation",
          "Schema validation, parameterised queries, XXE-safe XML parsing",
          "Implemented"),
         ("SI-11", "Error Handling",
          "Structured errors; identical response for unknown account and wrong "
          "password to prevent enumeration", "Implemented")],
        widths=(0.65, 1.35, 3.4, 0.95), font_size=7.5)

D.h2("6.3 Data classification and handling")
D.p("Per-table classification, sensitivity, encryption and audit requirements "
    "are specified in AGT-DA-001 §8, and retention in AGT-DA-001 §9. "
    "Architecturally the consequence is that CUI-classified tables are never "
    "exported to a destination outside the authorisation boundary, and the "
    "bulletin domain — which carries no CUI — is the only data set permitted "
    "in a public response.")
D.page_break()

# ── 7 ────────────────────────────────────────────────────────────────────────
D.h1("7. Infrastructure Architecture")
D.h2("7.1 Azure Resource Inventory")
D.table(["Resource", "Development", "Production"],
        [("Backend App Service", "docuaction-dev (rg-docuaction-dev)",
          "Docuaction (rg-docuaction-prod)"),
         ("Backend host", "docuaction-dev.azurewebsites.net",
          "api-prod.docuaction.io"),
         ("Frontend Static Web App", "docuaction-frontend-dev",
          "docuaction-frontend"),
         ("Frontend host", "witty-dune-0dd70870f.7.azurestaticapps.net",
          "witty-tree-0a448a70f.7.azurestaticapps.net"),
         ("Database", "docuaction-db-dev, PostgreSQL 16, 7-day retention",
          "docuaction-db, PostgreSQL 16, 14-day retention"),
         ("Geo-redundant database", "Not provisioned",
          "docuaction-db-geo (14-day, geo-redundant) — cutover pending"),
         ("Key Vault", "docuaction-kv-dev — firewalled to private link",
          "docuaction-kv-prod — firewalled to private link")],
        widths=(1.5, 2.5, 2.5))
D.callout("A material fact for any reader assessing deployment currency: the "
          "customer-facing host api.docuaction.io is still served by the legacy "
          "Railway platform, not by the Azure App Service above. It resolves to "
          "thzu1ngo.up.railway.app and returns Server: railway-hikari. Code "
          "deployed to Azure production is NOT live on that host until the DNS "
          "cutover documented in docs/RAILWAY_DNS_CUTOVER_PLAN.md is executed. "
          "This is risk R-16 in AGT-EX-001 §7.", "MATERIAL")

D.h2("7.2 Environment Strategy")
D.table(["Environment", "Purpose", "Data", "Promotion gate"],
        [("Development", "Feature work, integration testing, DAST target",
          "Synthetic entities plus five real NPIs",
          "Tests pass and /health returns 200"),
         ("Production", "Live ONC-facing service",
          "ONC-supplied data only — never seeded with test identifiers",
          "Development verified first, then deploy and restart, then verify")],
        widths=(1.15, 1.85, 1.9, 1.6))
D.p("There is currently no QA, UAT or staging environment. The consequence is "
    "that development serves as both the integration environment and the "
    "security-testing target, and production changes are validated only after "
    "deployment. Adding a UAT environment ahead of production promotion is "
    "recommended and is recorded in §9.1.")

D.h2("7.3 CI/CD Pipeline")
D.diagram([
    "  PUSH to main",
    "     |",
    "     +--> [ CodeQL ]        static analysis",
    "     +--> [ Bandit ]        Python security linting",
    "     +--> [ pip-audit ]     dependency advisories",
    "     +--> [ pytest ]        274 tests",
    "              |",
    "              v",
    "     [ Build deployment zip ]   Python zipfile, NEVER PowerShell",
    "              |",
    "              v",
    "     [ Deploy to Azure ] --> [ RESTART ] --> [ Verify /health + /api/config ]",
    "",
    "  SCHEDULED (Mondays)",
    "     +--> [ OWASP ZAP ]     unauthenticated, development only",
    "     +--> [ StackHawk ]     authenticated, development only",
], "Figure 8. Pipeline. The explicit restart step is not redundant: a "
   "deployment reporting success is not proof the new code is serving.")
D.table(["Rule", "Reason"],
        [("Build archives with Python zipfile, never PowerShell "
          "Compress-Archive",
          "PowerShell writes backslash path separators, which Linux App "
          "Service does not read as directories. A clean deploy of such an "
          "archive replaces a working application with unusable flat files."),
         ("Always restart explicitly after deploying",
          "A deployment status of active is not proof the new code is serving. "
          "Every deployment in this programme has required an explicit restart "
          "before new behaviour appeared."),
         ("Never retry a deploy on a CLI error",
          "The command frequently reports a disconnect while the server "
          "continues building. Query deployment status instead; a blind retry "
          "during a live build is how partial deploys happen."),
         ("Verify with an endpoint unique to the new build",
          "A 200 from /health can come from the previous build, or from an "
          "entirely different platform serving the same hostname.")],
        widths=(2.3, 4.2))

D.h2("7.4 Backup and Recovery")
D.p("Detailed in docs/BACKUP_RESTORE_PROCEDURE.md. Architecturally: production "
    "carries 14-day point-in-time restore, development 7-day. Restore "
    "provisions a new server rather than restoring in place, so recovery "
    "requires repointing the application's database configuration and "
    "restarting.")
D.callout("Geo-redundant backup is disabled on the production database in use "
          "and cannot be enabled after server creation. A geo-redundant server "
          "exists and is the intended destination at cutover. Until then "
          "production backups are regionally redundant only. Recorded as RA-005.",
          "LIMITATION")
D.page_break()

# ── 8 ────────────────────────────────────────────────────────────────────────
D.h1("8. Operations Architecture")
D.h2("8.1 Monitoring")
D.table(["Signal", "Source", "Covers", "Gap"],
        [("/health", "Application", "Liveness and connector reachability",
          "Polled manually; no alerting on failure"),
         ("/api/config", "Application",
          "Which build and which environment is serving", "—"),
         ("App Service metrics", "Azure",
          "CPU, memory, request rate, HTTP status distribution",
          "Alert rules configured for a subset only"),
         ("Scheduler job status", "Application",
          "Bulletin collection runs with a self-healing watchdog",
          "Gated on a configuration flag that has been found disabled in "
          "production before"),
         ("Connector monitoring", "Assessment activity",
          "Per-source reachability and latency",
          "Point-in-time only; no continuous monitoring exists")],
        widths=(1.3, 1.15, 2.2, 1.85))
D.callout("No continuous availability monitoring is in place. The 99.5% "
          "availability target in AGT-REQ-001 (NFR-05) therefore cannot "
          "currently be evidenced, and is reported as Partial rather than met.",
          "GAP")

D.h2("8.2 Incident Response")
D.table(["Step", "Action"],
        [("1. Detect", "Health check failure, error rate, or user report"),
         ("2. Identify what is actually serving",
          "Query /api/config and inspect the Server response header — a 200 "
          "from /health does not establish which build or which platform "
          "answered"),
         ("3. Contain", "Roll back by deploying the retained previous artefact "
                        "with a clean deploy, then restart explicitly"),
         ("4. Verify", "/health returns 200 and /api/config reports the "
                       "expected environment"),
         ("5. Investigate", "Reproduce on development, never on production"),
         ("6. Record", "Root cause and corrective action")],
        widths=(1.5, 5.0))

D.h2("8.3 Operational Procedures")
D.table(["Procedure", "Reference"],
        [("Deployment", "docs/DEPLOYMENT_GUIDE.md"),
         ("Backup and restore", "docs/BACKUP_RESTORE_PROCEDURE.md"),
         ("DNS cutover", "docs/RAILWAY_DNS_CUTOVER_PLAN.md"),
         ("DAST execution and finding validation", "docs/DAST_CI_SETUP.md"),
         ("SAM.gov key provisioning", "docs/SAM_GOV_API_KEY_SETUP.md"),
         ("State registry strategy", "docs/STATE_REGISTRY_STRATEGY.md"),
         ("Risk acceptance", "docs/RISK_ACCEPTANCE_REGISTER.md"),
         ("API version baseline", "docs/API_VERSION_1.0_BASELINE.md")],
        widths=(2.5, 4.0))
D.page_break()

# ── 9 ────────────────────────────────────────────────────────────────────────
D.h1("9. Future State Architecture")
D.h2("9.1 Near term — Q4 2026")
D.table(["Initiative", "Driver", "Dependency"],
        [("Execute the DNS cutover to Azure", "Risk R-16 — deployed fixes are "
          "not live on the customer-facing host", "Registrar access"),
         ("Provision the SAM.gov API key", "Gap G-01 — federal registration "
          "and debarment unverified", "Interactive SAM.gov login"),
         ("Execute the first DAST runs", "Gap G-05 — no dynamic testing "
          "evidence", "StackHawk API key"),
         ("Rehearse a database restore", "Gap G-06 — recovery time objective "
          "unmeasured", "Scratch server"),
         ("Complete Key Vault migration", "Gap G-08 — no secret rotation or "
          "access audit", "Approved network path"),
         ("Add a UAT environment", "Production changes are currently validated "
          "only after deployment", "Azure subscription capacity"),
         ("Automated regression testing", "Frontend has no end-to-end coverage",
          "Test authoring effort"),
         ("Next.js upgrade", "RA-001 and RA-002 advisories",
          "A stable release outside the advisory range")],
        widths=(2.0, 2.6, 1.9))

D.h2("9.2 Medium term — 2027")
D.table(["Initiative", "Description"],
        [("Case management module",
          "Configurable multi-agency workflow. Tables exist; explicitly out of "
          "TEFCA ARC scope (AGT-EX-001 §4.2)"),
         ("Additional agency configurations",
          "Reuse of the verification and classification core for other federal "
          "registry owners"),
         ("Automated retention enforcement",
          "Archival and purge jobs implementing the schedule in AGT-DA-001 §9 "
          "— currently specified but not enforced"),
         ("FedRAMP authorisation",
          "Required if the platform is offered as a shared service"),
         ("SOC 2 Type II", "Commercial assurance for non-federal customers"),
         ("Independent accessibility audit",
          "Section 508 conformance is currently unverified (NFR-11)")],
        widths=(2.0, 4.5))

D.h2("9.3 Long term — 2028 and beyond")
D.table(["Initiative", "Description", "Precondition"],
        [("Event-driven architecture",
          "Streaming ingestion and reactive verification in place of "
          "batch cycles",
          "Sustained volume that batch processing cannot serve"),
         ("Identity resolution graph",
          "Provenance and cross-registry entity resolution",
          "Multiple registries under management"),
         ("Machine-assisted classification",
          "Model-assisted triage of B3 cases",
          "A labelled corpus of resolved B3 decisions large enough to "
          "evaluate honestly"),
         ("Multi-agency shared service",
          "The platform offered to several agencies from one authorisation "
          "boundary", "FedRAMP authorisation")],
        widths=(1.7, 3.0, 1.8))
D.callout("Section 9 describes candidate direction, not committed scope. "
          "Nothing in it is funded under Contract 7571MN26F80064.", "SCOPE")
D.page_break()

D.h1("Revision History")
D.table(["Version", "Date", "Author", "Description"],
        [("1.0", "August 2026", "Imran Siddiqui", "Initial release")],
        widths=(0.9, 1.3, 1.7, 2.6))

path = OUT / "AGT-EA-001_Enterprise_Architecture.docx"
D.save(path)
print(f"saved {path.name}: {path.stat().st_size:,} bytes")
