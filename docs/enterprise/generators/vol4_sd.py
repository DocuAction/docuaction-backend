"""AGT-SD-001 — Volume IV, System and Module Design."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402

S = pathlib.Path(__file__).parent
OUT = pathlib.Path(r"C:\Imran_Coding projects\DocuAction\backend\docs\enterprise")
OUT.mkdir(parents=True, exist_ok=True)
SPEC = json.loads((S / "openapi.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((S / "schema.json").read_text(encoding="utf-8"))

D = AGTDoc(doc_id="AGT-SD-001",
           title="DocuAction TEFCA ARC Platform",
           subtitle="Prepared for the Assistant Secretary for Technology Policy / "
                    "Office of the National Coordinator for Health IT (ASTP/ONC)",
           version="1.0", date="August 2026")
D.cover("Volume IV — System and Module Design")
D.doc_control([
    ("Document ID", "AGT-SD-001"),
    ("Document Title", "Volume IV — System and Module Design"),
    ("Version", "1.0"), ("Status", "Released"), ("Date", "August 2026"),
    ("Contract Number", "7571MN26F80064"),
    ("Contractor", "Alliance Global Tech, Inc. (AGT)"),
    ("CAGE / UEI", "8ERE8 / MP2FLV1MAW93"),
    ("Author", "Imran Siddiqui, Chief Executive Officer"),
    ("Classification", "CONFIDENTIAL — Controlled Unclassified Information (CUI)"),
    ("Related Documents", "AGT-EA-001 (Enterprise Architecture), AGT-EX-001, "
                          "AGT-REQ-001, AGT-DA-001"),
])
D.page_break()
D.toc()

# ── 1 ────────────────────────────────────────────────────────────────────────
D.h1("1. System Design Overview")
D.p("AGT-EA-001 establishes the architectural context: the layers, the module "
    "boundaries and the integration pattern. This volume specifies the internal "
    "design of each module — its purpose, inputs, outputs, interfaces, tables, "
    "business rules and error handling.")
D.p("Where a design decision has a non-obvious reason, that reason is recorded "
    "with it. Several of the rules below exist because their absence produced a "
    "defect that reached testing; those are marked, because a constraint with a "
    "known failure behind it is more likely to survive a future refactor than "
    "one asserted abstractly.")
D.table(["Module", "Contract relevance", "Section"],
        [("TEFCA ARC Verification Engine", "Core deliverable — Tasks 2 to 5",
          "§2"),
         ("Bulletin Intelligence", "Out of TEFCA ARC scope — separate contract",
          "§3"),
         ("Document Processing", "Out of scope", "§4"),
         ("Audio Transcription", "Out of scope", "§5"),
         ("Authentication and Authorization", "Cross-cutting", "§6"),
         ("Case Management", "Future product — not in scope", "§7"),
         ("Analytics and Reporting", "Supports Tasks 3 to 5", "§8")],
        widths=(2.3, 3.2, 1.0))
D.page_break()

# ── 2 ────────────────────────────────────────────────────────────────────────
D.h1("2. TEFCA ARC Verification Engine")
D.h2("2.1 Purpose")
D.p("The core contract deliverable. Implements the review methodology (Task 2), "
    "the retrospective review (Task 3), the ongoing review (Task 4) and "
    "priority reviews (Task 5).")
D.h2("2.2 Module location")
D.p("app/tefca_registry/ — registry, verification, rules, sampling, reporting "
    "and review workflow. Authoritative-source connectors live in "
    "app/Tefca/connectors.py.")

D.h2("2.3 Component overview")
D.diagram([
    "  [ Entity Registry ] --> [ Connector Manager ] --> [ Rules Engine ]",
    "         |                        |                        |",
    "         |                        v                        v",
    "         |                 [ NPI Validator ]      [ Bucket Classifier ]",
    "         |                                                 |",
    "         v                                                 v",
    "  [ State Machine ]                              [ Review Records ]",
    "                                                           |",
    "  [ Sampling Engine ] ----------------------------> [ Review Cycles ]",
    "                                                           |",
    "                                                           v",
    "                          [ Report Generator ] <--- [ Priority Review ]",
], "Figure 1. TEFCA ARC component relationships.")

# 2.3.1
D.h2("2.3.1 Entity Registry")
D.table(["Aspect", "Design"],
        [("Purpose", "Import, store, version and search TEFCA entities"),
         ("Inputs", "CSV extract supplied by ONC"),
         ("Outputs", "Entity records with normalised identifiers and hierarchy"),
         ("Interfaces", "GET /api/tefca/registry/entities, GET /entities/{id}, "
                        "GET /search, POST /import/csv, GET /import/history, "
                        "DELETE /entities/{id}"),
         ("Tables", "tefca_reg_entities, tefca_entity_identifiers, "
                    "tefca_entity_relationships, tefca_entity_endpoints, "
                    "tefca_entity_versions, tefca_import_batches"),
         ("Business rules",
          "NPI check digit validated on import; duplicates skipped by "
          "TEFCAID/HCID; parent references resolved in a second pass; an "
          "initial version snapshot written per entity"),
         ("Error handling",
          "Per-entity savepoints — one bad row never fails the batch. Row "
          "errors are recorded on the batch record with their detail"),
         ("Deletion", "Soft only. Review records, verifications and sample "
                      "membership reference entities, so a hard delete would "
                      "orphan reported evidence")],
        widths=(1.2, 5.3))
D.callout("Invalid NPIs FLAG, never reject. Existing registry data predates the "
          "check-digit rule, and refusing those rows would break a working "
          "system to enforce it. The entity lands, the problem is visible, and "
          "an audit entry records it.", "RULE")
D.callout("A defect found during performance benchmarking and fixed: a batch "
          "reported an error count with an empty error list, because the JSONB "
          "column was handed the same list object it was first flushed with and "
          "the change was therefore invisible to the ORM. An auditor would have "
          "seen 'two errors' with no record of what they were. The first "
          "attempted fix was deployed, re-tested and still failed, which is "
          "what exposed the real cause.", "DEFECT HISTORY")

# 2.3.2
D.h2("2.3.2 Connector Manager")
D.table(["Aspect", "Design"],
        [("Purpose", "Orchestrate verification across authoritative sources"),
         ("Inputs", "An entity carrying an NPI, and a UEI or legal name for SAM"),
         ("Outputs", "One five-state result per source, with payload and hash"),
         ("Components", "NPPES, PECOS, OIG LEIE, SAM.gov (v3 registration and "
                        "v4 exclusions)"),
         ("Interface per connector",
          "lookup_by_npi(), lookup_by_uei(), lookup_by_name(), "
          "check_exclusions(), verify(), probe()"),
         ("Tables", "tefca_verifications, tefca_verification_jobs, "
                    "tefca_source_cache, tefca_connector_logs"),
         ("Error handling",
          "Every failure mode fails closed to unavailable or failed. One "
          "connector failing never blocks the others")],
        widths=(1.5, 5.0))
D.p("The common result object carries a success flag, the payload, the query "
    "parameters, a timestamp, an API version and a SHA-256 hash of the "
    "response. The hash is what allows a classification to be reconstructed and "
    "shown to be based on unaltered source data.")
D.callout("The success flag means THE QUERY COMPLETED — not that the entity is "
          "clean. The verdict lives in the payload. Reading success as a verdict "
          "classified every entity as excluded during early testing, because a "
          "successful OIG LEIE query was interpreted as an exclusion hit.",
          "DEFECT HISTORY")
D.p("SAM.gov queries two endpoints independently rather than inferring "
    "exclusion from the registration record. The v3 record carries an exclusion "
    "summary flag, but an entity with no SAM registration at all can still "
    "appear on the exclusions list — in that case v3 returns nothing while v4 "
    "returns a hit. Trusting v3 alone would report 'not found, therefore fine' "
    "about a debarred party.")

# 2.3.3
D.h2("2.3.3 NPI Validator")
D.table(["Aspect", "Design"],
        [("Purpose", "CMS check digit validation"),
         ("Algorithm", "Prefix the nine-digit base with 80840, apply the Luhn "
                       "mod-10 algorithm, compare with the tenth digit"),
         ("Input", "A ten-digit NPI string"),
         ("Output", "Validity flag plus the reason when invalid"),
         ("Behaviour on failure", "Flag and audit; the entity still imports")],
        widths=(1.5, 5.0))
D.p("Applied during the assessment, this caught three of five supplied hospital "
    "NPIs failing the check digit outright — they could not have been real NPIs, "
    "and the national registry held no record of them.")

# 2.3.4
D.h2("2.3.4 State Machine")
D.table(["From state", "Permitted transitions"],
        [("draft", "pending_verification"),
         ("pending_verification", "active, draft"),
         ("active", "suspended, inactive"),
         ("suspended", "active, inactive"),
         ("inactive", "(terminal — no transition permitted)")],
        widths=(1.8, 4.7))
D.p("Anything not listed is refused with HTTP 400 and an explanation the caller "
    "can act on. Two refusals matter in particular: draft directly to active "
    "would skip verification, and inactive back to active would resurrect a "
    "deregistered entity. Both refusals are written to the audit log — an "
    "attempt to skip verification is precisely what a reviewer wants to see.")

# 2.3.5
D.h2("2.3.5 Rules Engine")
D.table(["Aspect", "Design"],
        [("Purpose", "Configurable B1–B4 classification"),
         ("Input", "The five-state verification result set plus derived fields"),
         ("Output", "Bucket, rule code, rule version, rationale, matched "
                    "conditions"),
         ("Storage", "review_rules — versioned rows with effective and retired "
                     "dates"),
         ("Evaluation", "Sorted by priority then rule code; first match wins"),
         ("Default", "B3 (Inexplicable) when no rule matches"),
         ("Condition grammar", "all_of, any_of, none_of, any_unavailable over "
                               "source states and derived fields")],
        widths=(1.5, 5.0))
D.callout("B4 rules carry priority 5 and are evaluated FIRST. At priority 50 "
          "they were evaluated last, and an excluded entity with otherwise "
          "clean sources matched the B1 rule and was reported 'No Discrepancy'. "
          "A debarred provider reported as having no discrepancy is the most "
          "consequential error this engine could make, so the disqualifying "
          "rule leads.", "DEFECT HISTORY")
D.h3("Rule versions in force")
D.table(["Version", "Status", "Change"],
        [("1", "Retired", "Original seeded rule set, RULE-001 to RULE-005"),
         ("2", "Active", "SAM.gov wired in as a disqualifier on B1/B2/B3 and as "
                         "a trigger on B4")],
        widths=(0.9, 1.0, 4.6))
D.p("SAM is added as a disqualifier and never as a requirement. Requiring SAM "
    "verification for B1 would drop every entity out of B1 while SAM has no API "
    "key, reclassifying the entire registry on deployment. Every version 2 SAM "
    "condition fires only on a positive finding, so with no key the "
    "classification output is identical to version 1 — a property held by a "
    "dedicated regression test rather than by assertion.")
D.p("Version 2 also corrects a latent defect in version 1: the B4 rule matched "
    "only the status 'debarred' while the connector emits 'excluded', so a "
    "SAM-excluded entity with clean NPPES and PECOS results was classified B1.")

# 2.3.6
D.h2("2.3.6 Bucket Classifier")
D.table(["Aspect", "Design"],
        [("Purpose", "Execute the active rule set against a verification result"),
         ("Determinism", "The same inputs always produce the same output; no "
                         "clock, randomness or external call participates"),
         ("Caching", "Rules cached per classifier instance for 3,600 seconds"),
         ("Cache scope", "Per instance rather than global, so a test "
                         "constructing a classifier with explicit rules is not "
                         "affected by whatever another test loaded"),
         ("Recorded output", "Rule code AND rule version on every "
                             "classification")],
        widths=(1.5, 5.0))

# 2.3.7
D.h2("2.3.7 Sampling Engine")
D.table(["Aspect", "Design"],
        [("Purpose", "Statistically valid sample selection"),
         ("Formula", "Cochran, with a finite population correction applied "
                     "whenever the sample is a material fraction of the "
                     "population"),
         ("Parameters", "Confidence level, margin of error, expected "
                        "proportion — all configurable, none hard-coded"),
         ("Stratification", "By entity type and by parent QHIN"),
         ("Reproducibility", "The random seed is stored with the sample and "
                             "returned to the caller"),
         ("Interval estimation", "Wilson score interval, which stays valid at "
                                 "small counts and at proportions of 0 or 1 "
                                 "where the normal approximation does not"),
         ("Tables", "review_samples, sample_entities")],
        widths=(1.5, 5.0))

# 2.3.8
D.h2("2.3.8 Report Generator")
D.p("Ten mandatory sections. The generator fails rather than emit a report "
    "missing any of them.")
D.table(["#", "Section", "Note"],
        [("1", "Executive summary", "Population, reviewed count, headline rate"),
         ("2", "Sampling statistics", "Population, sample size, seed, method"),
         ("3", "B1–B4 distribution", "Counts must sum to the reviewed total"),
         ("4", "Discrepancy rate with confidence interval", "Wilson score"),
         ("5", "Verification coverage", "Measured against implemented "
                                        "connectors only"),
         ("6", "Outstanding items", "Unresolved B3 queue"),
         ("7", "Data sources used", "Which connectors answered this cycle"),
         ("8", "Methodology", "Rule version in force"),
         ("9", "Known gaps", "Unimplemented connectors, named"),
         ("10", "LIMITATIONS", "MANDATORY — generation fails if empty")],
        widths=(0.4, 2.6, 3.5))
D.p("Output is JSON plus rendered HTML, archived immutably. Excel export reads "
    "the archived payload and never recomputes: a report rendering one set of "
    "figures as HTML and another as Excel would be worse than having no export "
    "at all. The Excel workbook carries the limitations on their own sheet, so "
    "a reader who opens the spreadsheet and not the HTML still sees the "
    "caveats.")
D.callout("An empty limitations section reads to a government reviewer as 'no "
          "limitations', which is never true of this platform. Coverage is "
          "three of seven possible authoritative sources.", "RULE")

# 2.3.9
D.h2("2.3.9 Priority Review")
D.table(["Aspect", "Design"],
        [("Purpose", "On-demand urgent review — Task 5"),
         ("Trigger", "POST /api/tefca/arc/priority-review, administrator only"),
         ("Actions", "Immediate verification across all operational "
                     "connectors, classification, root cause analysis, severity "
                     "assessment"),
         ("Output", "A discrete priority review report carrying root cause, "
                    "severity and corrective recommendations"),
         ("Aggregation", "Included in the quarterly aggregated report"),
         ("Tables", "tefca_priority_cases, review_reports")],
        widths=(1.3, 5.2))

# 2.3.10
D.h2("2.3.10 Review Records")
D.table(["Aspect", "Design"],
        [("Purpose", "Immutable classification history"),
         ("Stable identifier", "REV-YYYY-NNNNNN"),
         ("Contents", "Verification snapshot, bucket, rule code, rule version, "
                      "rationale, matched conditions"),
         ("B3 resolution", "Reviewer confirms B3 or reclassifies, always with a "
                           "rationale"),
         ("Mutability", "Append-only. No update or delete path is exposed"),
         ("Tables", "review_records")],
        widths=(1.4, 5.1))
D.p("A resolution is recorded as new state alongside the original "
    "classification rather than overwriting it. An auditor asking what was "
    "concluded at the time, and on what basis, must be able to see the original "
    "answer and the human decision that followed it.")

# 2.3.11
D.h2("2.3.11 Review Cycles")
D.table(["Aspect", "Design"],
        [("Purpose", "Tie a sample to its report so traceability is one row, "
                     "not a reconstructed join"),
         ("Types", "Retrospective (Task 3), ongoing (Task 4), priority (Task 5)"),
         ("Tables", "review_cycles")],
        widths=(1.3, 5.2))
D.p("An auditor asking which sample backs a given quarterly report gets a "
    "single row rather than having to reconstruct the relationship from "
    "timestamps.")
D.page_break()

# ── 3 ────────────────────────────────────────────────────────────────────────
D.h1("3. Bulletin Intelligence Module")
D.callout("Out of TEFCA ARC scope. Documented because it shares the platform "
          "and the database. It carries no CUI. See AGT-EX-001 §4.2.", "SCOPE")
D.table(["Aspect", "Design"],
        [("Purpose", "FCC Daily Intelligence Bulletin"),
         ("Location", "app/bulletin_intelligence/"),
         ("Pipeline", "collect → classify → QA → generate → deliver"),
         ("Collection", "RSS feeds, NewsData.io, and other providers"),
         ("Classification", "Model-assisted topic scoring"),
         ("Generation", "Model-assisted summarisation into a briefing"),
         ("Export", "Branded Excel workbook with a summary sheet"),
         ("Scheduling", "Weekday early-morning run, gated on a configuration "
                        "flag"),
         ("Tables", "bulletin_articles, bulletin_briefings, "
                    "bulletin_source_registry, bulletin_source_outcome, "
                    "bulletin_cost_logs, bulletin_search_profiles, "
                    "bulletin_run_log, bulletin_audit_log")],
        widths=(1.3, 5.2))
D.h2("3.1 Source health — measured")
D.p("All registered feed URLs were probed twice during the assessment. The "
    "two-pass method is the design point, not an incidental detail.")
D.table(["Category", "Count", "Share", "Action"],
        [("Active", "161", "37.4%", "Retained"),
         ("Recovered on gentle re-probe", "78", "18.1%",
          "Retained — the first sweep was wrong about these"),
         ("Dead (404/410 on both passes)", "78", "18.1%", "Deactivated"),
         ("Access blocked (401/403)", "58", "13.5%",
          "Retained — client refused, feed exists"),
         ("Stale", "38", "8.8%", "Retained"),
         ("Unreachable", "15", "3.5%", "Retained"),
         ("Server error / rate limited", "3", "0.7%", "Retained"),
         ("Total probed", "431", "100%", "")],
        widths=(2.2, 0.8, 0.8, 2.7))
D.callout("The fast sweep reported 232 failures; a gentler re-probe found 78 of "
          "them — 34% — working perfectly. Deactivating on a single sweep would "
          "have removed 78 healthy feeds while producing a report that looked "
          "like diligent cleanup. Only twice-confirmed dead URLs were "
          "deactivated, and the 58 refusing our client were deliberately kept: "
          "that is a request-headers problem to fix, not a source to delete.",
          "METHOD")
D.page_break()

# ── 4, 5 ─────────────────────────────────────────────────────────────────────
D.h1("4. Document Processing Module")
D.callout("Out of TEFCA ARC scope.", "SCOPE")
D.table(["Aspect", "Design"],
        [("Purpose", "Model-assisted document analysis"),
         ("Capabilities", "Upload, summarise, compare, extract"),
         ("Security", "File type and size validation on upload; stored outside "
                      "the web root"),
         ("Error handling", "Rejects unsupported types without echoing the "
                            "supplied filename back into the response")],
        widths=(1.3, 5.2))

D.h1("5. Audio Transcription Module")
D.callout("Out of TEFCA ARC scope.", "SCOPE")
D.table(["Aspect", "Design"],
        [("Purpose", "Speech-to-text for meeting recordings"),
         ("Integration", "External transcription API"),
         ("Status", "Basic functionality")],
        widths=(1.3, 5.2))
D.page_break()

# ── 6 ────────────────────────────────────────────────────────────────────────
D.h1("6. Authentication and Authorization Module")
D.h2("6.1 Purpose")
D.p("Platform-wide identity and access control. Cross-cutting: every other "
    "module depends on it.")

D.h2("6.2 Token design")
D.table(["Aspect", "Design"],
        [("Format", "JSON Web Token, HS256"),
         ("Claims", "Subject, role, expiry"),
         ("Expiry", "Configurable; shorter for administrative sessions"),
         ("Revocation", "A revocation timestamp on the account invalidates "
                        "every token issued before it"),
         ("Failure response", "401 for a missing or malformed token, 403 for a "
                              "valid token with insufficient role")],
        widths=(1.4, 5.1))
D.callout("The 401/403 boundary is part of the frozen API contract "
          "(docs/API_VERSION_1.0_BASELINE.md). An earlier framework version "
          "returned 403 in both cases; the upgrade changed observable behaviour "
          "without any application code change, which is exactly the kind of "
          "change the version baseline exists to catch.", "CONTRACT")

D.h2("6.3 Role hierarchy")
D.p("Nine graded levels. A guard requiring a minimum role admits every level at "
    "or above it.")
D.table(["Level", "Role", "Capabilities"],
        [("1", "viewer", "Read-only access to entities and reports"),
         ("2", "contributor", "Import, run verification, draw samples"),
         ("3", "manager", "Contributor plus team oversight"),
         ("4", "reviewer", "Front-line review; resolve B3 classifications"),
         ("5", "senior_analyst", "Bucket overrides, B3 escalation queue, "
                                 "calibration"),
         ("6", "qalead", "Methodology approval, deliverable sign-off, view all "
                         "queues"),
         ("7", "program_manager", "Deliverable submission, full audit log, "
                                  "cycle management"),
         ("8", "admin", "Full access including rule authoring and user "
                        "management")],
        widths=(0.6, 1.5, 4.4))
D.p("The contract-facing roles at levels 4 to 7 exist because the review "
    "workflow separates the person who classifies from the person who approves "
    "the methodology and from the person who submits the deliverable. Collapsing "
    "them into a single administrator role would remove that separation of "
    "duties.")

D.h2("6.4 Account lifecycle and protective limits")
D.table(["Control", "Threshold", "Window", "Effect"],
        [("Account lockout", "5 failed logins", "15 minutes",
          "Further attempts on that account are refused"),
         ("Per-address login throttle", "20 attempts", "15 minutes",
          "Further attempts from that address are refused"),
         ("Registration throttle", "5 registrations", "1 hour",
          "Guards against mail-bombing through the signup path"),
         ("General API rate limit", "60 requests per minute (default tier)",
          "Sliding", "429 with a retry indication")],
        widths=(1.8, 1.7, 1.1, 1.9))
D.p("Login responses are time-equalised: a bcrypt operation runs even when the "
    "supplied address matches no account, so response timing cannot be used to "
    "enumerate valid accounts.")
D.callout("The per-address throttle has a testing consequence worth recording. "
          "A lockout test consumes the address budget and will starve every "
          "subsequent authenticated test of a token. Test suites must run "
          "lockout cases last and abort explicitly if they cannot obtain a "
          "token, rather than reporting a cascade of misleading failures.",
          "OPERATIONAL")
D.page_break()

# ── 7, 8 ─────────────────────────────────────────────────────────────────────
D.h1("7. Case Management Module (Future Product)")
D.callout("Tables exist in the schema. NOT in TEFCA ARC scope and not funded "
          "under this contract. Documented so its presence in the database is "
          "not mistaken for contract work.", "SCOPE")
D.table(["Aspect", "Design intent"],
        [("Purpose", "Configurable multi-agency case workflow"),
         ("Design", "Configurable states, service-level timers, recorded "
                    "decisions"),
         ("Candidate agencies", "SBA, CMS, VA, IRS"),
         ("Tables", "tefca_cases, tefca_decisions"),
         ("Status", "Schema only — no exposed interface")],
        widths=(1.5, 5.0))

D.h1("8. Analytics and Reporting Module")
D.table(["Dashboard", "Content", "Audience"],
        [("TEFCA dashboard", "Entity counts, verification status distribution, "
                             "B1–B4 distribution, review queue depth",
          "ONC and AGT programme staff"),
         ("Bulletin dashboard", "Collection runs, article counts, per-provider "
                                "cost", "AGT programme staff"),
         ("Security dashboard", "Finding counts by severity, scan currency",
          "AGT technical staff")],
        widths=(1.4, 3.2, 1.9))
D.p("Common pattern: each dashboard endpoint returns aggregated JSON computed "
    "server-side. Aggregation is never performed in the browser, so a dashboard "
    "figure and a report figure covering the same period are computed by the "
    "same code path and cannot disagree.")
D.page_break()

# ── 9 ────────────────────────────────────────────────────────────────────────
D.h1("9. Error Handling Design")
D.h2("9.1 Response shape")
D.diagram([
    '  {',
    '    "detail":     "Human-readable message",',
    '    "error_code": "MACHINE_READABLE_CODE",',
    '    "status":     4xx | 5xx,',
    '    "request_id": "uuid"',
    '  }',
], "Figure 2. Structured error response. The request identifier correlates a "
   "user report with a server log entry without exposing internals.")

D.h2("9.2 Disclosure rules")
D.table(["Rule", "Reason"],
        [("No stack traces in production responses",
          "A trace reveals file paths, framework versions and internal "
          "structure"),
         ("No database detail in error messages",
          "Driver errors disclose schema, table and column names"),
         ("No path echo in 404 responses",
          "Echoing the requested path enables reflected content injection and "
          "confirms path structure"),
         ("Identical response for unknown account and wrong password",
          "Distinguishable responses turn the login endpoint into an account "
          "enumeration oracle"),
         ("Time-equalised authentication failure",
          "Response timing is an enumeration side channel even when the "
          "response body is identical"),
         ("Configuration names may appear; values never do",
          "Naming a required setting is operationally useful; the value is a "
          "credential")],
        widths=(2.4, 4.1))
D.callout("The last rule produced a false positive during Security Validation. "
          "A test flagged a credential disclosure on a response containing the "
          "text 'requires SAM_GOV_API_KEY'. That is a configuration NAME in an "
          "explanatory note, not a credential VALUE, and nothing was disclosed. "
          "The test assertion was corrected; no application change was "
          "warranted. Auto-fixing it would have degraded a useful message to "
          "satisfy a faulty test.", "DEFECT HISTORY")

D.h2("9.3 Failure isolation")
D.table(["Failure", "Isolation behaviour"],
        [("One authoritative source unavailable",
          "Verification completes with that source marked unavailable"),
         ("One entity fails during import",
          "Per-entity savepoint; the batch continues and records the error"),
         ("Audit write fails",
          "Logged; the originating transaction still commits. Losing the action "
          "because its audit entry failed is worse than the missing entry"),
         ("Optional module import fails",
          "Guarded independently so an unrelated failure cannot leave a "
          "required helper undefined deep inside a later loop"),
         ("Report section missing", "Generation fails loudly rather than "
                                    "emitting an incomplete report")],
        widths=(2.0, 4.5))
D.page_break()

# ── 10 ───────────────────────────────────────────────────────────────────────
D.h1("10. Notification Design (Future)")
D.table(["Channel", "Purpose", "Status"],
        [("Email", "Deliverable and alert distribution",
          "Currently sent over direct HTTP; a managed provider is planned"),
         ("In-application", "Review queue notifications", "Not built"),
         ("Alerting", "B4 findings and service-level breaches", "Not built")],
        widths=(1.3, 3.2, 2.0))
D.callout("No notification capability is claimed as delivered. B4 findings are "
          "currently surfaced through the review queue and the report, not "
          "pushed.", "STATUS")
D.page_break()

# ── 11 Appendices ────────────────────────────────────────────────────────────
D.h1("11. Appendices")

D.h2("Appendix A — Module file structure")
D.diagram([
    "  app/",
    "   |- main.py                    application assembly, health, /api/config",
    "   |- core/                      security, database, rate limiting, errors",
    "   |- api/                       auth, admin users, password reset, plans",
    "   |- tefca_registry/            TEFCA ARC engine (core deliverable)",
    "   |   |- routes.py              registry CRUD, import, verify, delete",
    "   |   |- review_routes.py       rules, samples, reviews, reports",
    "   |   |- review_service.py      verify -> classify -> persist",
    "   |   |- bucket_classifier.py   B1-B4 rules engine",
    "   |   |- sampling_engine.py     Cochran + Wilson",
    "   |   |- report_generator.py    ten-section report assembly",
    "   |   |- report_excel.py        archived-report Excel export",
    "   |   |- lifecycle.py           entity state machine",
    "   |   |- csv_import.py          ONC CSV ingestion",
    "   |   |- queries.py             read models",
    "   |   |- models.py              ORM definitions",
    "   |   +- audit.py               append-only audit writer",
    "   |- Tefca/",
    "   |   +- connectors.py          NPPES, PECOS, OIG LEIE, SAM.gov",
    "   |- bulletin_intelligence/     out of TEFCA ARC scope",
    "   +- models/                    shared ORM models",
], "Figure 3. Module layout. The contract deliverable is app/tefca_registry/ "
   "plus the connectors.")

D.h2("Appendix B — API surface")
paths = SPEC.get("paths", {})
METHODS = ("get", "post", "put", "patch", "delete")
groups = {}
for p, ops in paths.items():
    seg = p.split("/")
    key = "/".join(seg[:4]) if len(seg) > 3 else p
    groups.setdefault(key, []).append(
        (p, sorted(m.upper() for m in ops if m in METHODS)))
total_ops = sum(len(v) for _, lst in groups.items() for _, v in lst)
D.table(["Metric", "Value"],
        [("OpenAPI version", SPEC.get("openapi")),
         ("Documented paths", str(len(paths))),
         ("Documented operations", str(total_ops)),
         ("Path groups", str(len(groups))),
         ("Baseline", "Frozen as API version 1.0 — "
                      "docs/api/openapi_v1.0.json"),
         ("Schema validation", "PASS — no errors reported")],
        widths=(2.0, 4.5))
D.p("Grouped by path prefix. The complete operation-level specification is the "
    "archived document referenced above; reproducing 300-plus operations here "
    "would duplicate a machine-readable artefact in a form that cannot be "
    "validated.")
rows = []
for key in sorted(groups):
    entries = groups[key]
    ops_count = sum(len(m) for _, m in entries)
    verbs = sorted({v for _, ms in entries for v in ms})
    rows.append((key, str(len(entries)), str(ops_count), ", ".join(verbs)))
D.table(["Path group", "Paths", "Ops", "Methods"], rows,
        widths=(3.1, 0.6, 0.5, 2.3), font_size=7)

D.h2("Appendix C — Database table index")
PURPOSE = {
    "tefca_reg_entities": "TEFCA registry entity master (current model)",
    "tefca_entities": "Legacy entity store",
    "tefca_entity_identifiers": "NPI, TEFCAID, HCID and other identifiers",
    "tefca_entity_relationships": "QHIN / Participant / Sub-Participant hierarchy",
    "tefca_entity_endpoints": "Exchange endpoints per entity",
    "tefca_entity_versions": "Point-in-time entity snapshots",
    "tefca_entity_findings": "Findings raised against an entity",
    "tefca_import_batches": "Import provenance, counts and per-row errors",
    "tefca_verifications": "Per-source verification results and payloads",
    "tefca_verification_jobs": "Verification run orchestration",
    "tefca_verification_checks": "Individual source check outcomes",
    "tefca_source_cache": "Cached authoritative-source responses",
    "tefca_connector_logs": "Connector operational logging",
    "review_rules": "Versioned B1–B4 classification rules",
    "review_records": "Immutable classification history",
    "review_samples": "Cochran sampling parameters and drawn seed",
    "sample_entities": "Sample membership",
    "review_cycles": "Links sample to report per cycle",
    "review_reports": "Archived report snapshots",
    "tefca_priority_cases": "Task 5 priority review subjects",
    "tefca_reg_audit_log": "Append-only audit trail",
    "users": "Platform accounts, roles and credential hashes",
    "tefca_cases": "Future case management — not in scope",
    "tefca_decisions": "Future case management — not in scope",
}
tbl_rows = []
for name in sorted(SCHEMA):
    meta = SCHEMA[name]
    purpose = PURPOSE.get(name, "")
    if not purpose:
        if name.startswith("bulletin_"):
            purpose = "Bulletin Intelligence — out of TEFCA ARC scope"
        elif name.startswith("tefca_"):
            purpose = "TEFCA supporting table"
        else:
            purpose = "Platform / other product area — out of TEFCA ARC scope"
    tbl_rows.append((name, str(len(meta["columns"])), purpose))
D.p(f"All {len(SCHEMA)} tables present in the application schema, extracted "
    f"programmatically rather than transcribed. Tables outside the TEFCA ARC "
    f"deliverable are marked as such. Full column-level documentation for the "
    f"30 in-scope tables is in AGT-DA-001 §5.")
D.table(["Table", "Cols", "Purpose"], tbl_rows,
        widths=(2.3, 0.5, 3.7), font_size=7)
D.page_break()

D.h1("Revision History")
D.table(["Version", "Date", "Author", "Description"],
        [("1.0", "August 2026", "Imran Siddiqui", "Initial release")],
        widths=(0.9, 1.3, 1.7, 2.6))

path = OUT / "AGT-SD-001_System_Module_Design.docx"
D.save(path)
print(f"saved {path.name}: {path.stat().st_size:,} bytes")
print(f"  API path groups: {len(groups)}  operations: {total_ops}")
print(f"  tables indexed:  {len(tbl_rows)}")
