"""Tier 3 evidence templates, Tier 4 assessment package, ZTA, and the traceability matrix."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "templates"
ASM = ROOT / "assessment"
BUILT = []
BLANK = ""


def _emit(d, directory, label):
    p = d.save(directory)
    BUILT.append(p)
    print(f"  {label:26s} {p.name:58s} {p.stat().st_size/1024:6.1f} KB")


def _tpl(doc_id, title, intro):
    d = AGTDoc(doc_id, title, classification="Internal")
    d.h1("Purpose of this template")
    d.p(intro)
    d.note("Complete every field. A field left blank is indistinguishable from a control "
           "that was not performed, and an assessor will read it as the latter. Where an "
           "item genuinely does not apply, write 'N/A' and the reason.")
    return d


# ─────────────────────────── Tier 3 templates ───────────────────────────
def t_access_review():
    d = _tpl("AGT-T-001", "Quarterly Access Review Template",
             "Records the quarterly review of all accounts and role assignments required by "
             "AGT-IAM-004 section 10 and AGT-CMS-024. This record is primary evidence for "
             "NIST AC-2 and HIPAA 164.308(a)(4).")
    d.h1("Review metadata")
    d.table(["Field", "Value"],
            [["Review period", BLANK], ["Review date", BLANK], ["Reviewer", BLANK],
             ["Approver", BLANK], ["Systems in scope", BLANK]], widths=[1.8, 4.4])
    d.h1("Account review")
    d.table(["Account", "Role", "Last activity", "Business need confirmed", "Action", "Completed"],
            [[BLANK] * 6 for _ in range(10)], widths=[1.3, 1.0, 1.0, 1.2, 1.0, 0.8])
    d.h1("Privileged account review")
    d.p("Every administrative assignment is reviewed individually and named. A summary "
        "statement that 'admin access was reviewed' is not evidence.")
    d.table(["Account", "Privilege", "Justification", "Time-bound", "Retain or revoke"],
            [[BLANK] * 5 for _ in range(6)], widths=[1.3, 1.3, 1.6, 0.9, 1.2])
    d.h1("Service principals and managed identities")
    d.table(["Identity", "Scope", "Owner", "Last used", "Retain or remove"],
            [[BLANK] * 5 for _ in range(5)], widths=[1.4, 1.4, 1.1, 1.1, 1.3])
    d.h1("Findings and actions")
    d.table(["Finding", "Severity", "Owner", "Target date", "Status"],
            [[BLANK] * 5 for _ in range(5)], widths=[2.3, 0.9, 1.1, 1.0, 0.9])
    d.h1("Attestation")
    d.p("I confirm that every account listed has been reviewed against current business "
        "need and that the actions recorded above have been completed or assigned.")
    d.table(["Role", "Name", "Signature", "Date"],
            [["Reviewer", BLANK, BLANK, BLANK], ["Approver", BLANK, BLANK, BLANK]],
            widths=[1.5, 1.7, 1.7, 1.3])
    _emit(d, TPL, "Quarterly Access Review")


def t_backup_test():
    d = _tpl("AGT-T-002", "Backup Test Record Template",
             "Records a backup restoration test required quarterly by AGT-BKP-019 section 5. "
             "Evidence for NIST CP-9(1) and HIPAA 164.308(a)(7)(ii)(A).")
    d.h1("Test metadata")
    d.table(["Field", "Value"],
            [["Test date", BLANK], ["Performed by", BLANK], ["Asset tested", BLANK],
             ["Backup type", BLANK], ["Recovery point selected", BLANK],
             ["Target environment", BLANK]], widths=[1.8, 4.4])
    d.h1("Execution")
    d.table(["Step", "Expected", "Actual", "Pass/Fail", "Time"],
            [["Locate the backup at the target recovery point", BLANK, BLANK, BLANK, BLANK],
             ["Restore to a NEW server (never over the live one)", BLANK, BLANK, BLANK, BLANK],
             ["Restore completes without error", BLANK, BLANK, BLANK, BLANK],
             ["Row counts match expectation", BLANK, BLANK, BLANK, BLANK],
             ["Referential integrity intact", BLANK, BLANK, BLANK, BLANK],
             ["Sample of known records verified", BLANK, BLANK, BLANK, BLANK],
             ["Application connects to the restored copy", BLANK, BLANK, BLANK, BLANK]],
            widths=[2.3, 1.0, 1.0, 0.9, 1.0])
    d.h1("Results against objective")
    d.table(["Measure", "Objective", "Achieved", "Within objective"],
            [["Recovery time", BLANK, BLANK, BLANK], ["Recovery point", BLANK, BLANK, BLANK]],
            widths=[1.6, 1.5, 1.5, 1.6])
    d.p("Where the achieved figure exceeds the objective, either the procedure is improved "
        "or the published objective is revised to a figure AGT can actually meet. An "
        "objective that testing has never achieved is a commitment AGT cannot honour.")
    d.h1("Issues encountered and follow-up")
    d.table(["Issue", "Impact", "Action", "Owner", "Target"],
            [[BLANK] * 5 for _ in range(4)], widths=[1.9, 1.3, 1.5, 0.9, 0.8])
    d.h1("Cleanup")
    d.bullets(["Restored scratch resources deleted (confirm): ______",
               "Any data copied during the test securely deleted (confirm): ______"])
    _emit(d, TPL, "Backup Test Record")


def t_dr_test():
    d = _tpl("AGT-T-003", "Disaster Recovery Test Record Template",
             "Records the annual disaster recovery exercise required by AGT-DRP-021 section 6. "
             "Evidence for NIST CP-4 and HIPAA 164.308(a)(7)(ii)(D).")
    d.h1("Exercise metadata")
    d.table(["Field", "Value"],
            [["Exercise date", BLANK], ["Type (tabletop / functional / full)", BLANK],
             ["Scenario", BLANK], ["Participants", BLANK], ["Facilitator", BLANK],
             ["Systems in scope", BLANK]], widths=[2.2, 4.0])
    d.h1("Objectives")
    d.table(["Objective", "Met", "Notes"], [[BLANK] * 3 for _ in range(5)], widths=[2.8, 0.8, 2.6])
    d.h1("Timeline")
    d.table(["Time", "Event", "Actor", "Notes"], [[BLANK] * 4 for _ in range(10)],
            widths=[0.9, 2.3, 1.1, 1.9])
    d.h1("Recovery objectives achieved")
    d.table(["System", "RTO objective", "RTO achieved", "RPO objective", "RPO achieved", "Met"],
            [["Backend API", "4 hours", BLANK, "N/A", BLANK, BLANK],
             ["Database", "8 hours", BLANK, "15 minutes", BLANK, BLANK],
             ["Frontend", "2 hours", BLANK, "N/A", BLANK, BLANK],
             ["Key Vault", "2 hours", BLANK, "0", BLANK, BLANK]],
            widths=[1.2, 1.1, 1.1, 1.1, 1.1, 0.6])
    d.h1("What worked, what did not")
    d.p("Record both. An exercise report containing only successes has not tested anything "
        "that mattered.")
    d.table(["Observation", "Category", "Corrective action", "Owner", "Target"],
            [[BLANK] * 5 for _ in range(6)], widths=[2.0, 1.0, 1.6, 0.9, 0.8])
    d.h1("Plan amendments arising")
    d.table(["Document", "Section", "Change required", "Owner"],
            [[BLANK] * 4 for _ in range(4)], widths=[1.4, 1.0, 2.6, 1.2])
    _emit(d, TPL, "DR Test Record")


def t_incident():
    d = _tpl("AGT-T-004", "Incident Report Template",
             "Records a security incident through the AGT-IRP-022 lifecycle. Retained for six "
             "years per AGT-DRP-007. Evidence for NIST IR-5 and HIPAA 164.308(a)(6).")
    d.h1("Incident identification")
    d.table(["Field", "Value"],
            [["Incident ID", BLANK], ["Date and time of discovery (UTC)", BLANK],
             ["Discovered by", BLANK], ["Detection source", BLANK],
             ["Severity (Critical/High/Medium/Low)", BLANK], ["Incident lead", BLANK],
             ["Status", BLANK]], widths=[2.4, 3.8])
    d.note("Time of discovery is a legal fact, not an administrative detail: under HIPAA it "
           "starts the 60-day notification clock. Record it precisely and do not revise it "
           "later to a more convenient value.")
    d.h1("Description")
    d.table(["Field", "Value"],
            [["What happened", BLANK], ["Systems affected", BLANK],
             ["Data classes involved", BLANK], ["PHI involved (yes/no/unknown)", BLANK],
             ["Was affected data encrypted", BLANK],
             ["Estimated individuals affected", BLANK]], widths=[2.2, 4.0])
    d.p("Whether the data was encrypted to HHS guidance determines whether this is a breach "
        "of unsecured PHI. Establish it early - it changes every downstream obligation.")
    d.h1("Timeline")
    d.table(["Time (UTC)", "Event", "Actor", "Evidence reference"],
            [[BLANK] * 4 for _ in range(10)], widths=[1.1, 2.4, 1.1, 1.6])
    d.h1("Containment, eradication, recovery")
    d.table(["Phase", "Actions taken", "By whom", "Time"],
            [["Containment", BLANK, BLANK, BLANK],
             ["Evidence preservation", BLANK, BLANK, BLANK],
             ["Eradication", BLANK, BLANK, BLANK],
             ["Recovery", BLANK, BLANK, BLANK],
             ["Verification", BLANK, BLANK, BLANK]], widths=[1.3, 2.6, 1.2, 1.1])
    d.h1("Notifications")
    d.table(["Recipient", "Required", "Deadline", "Date sent", "By whom"],
            [["Government COR", BLANK, "24 hours", BLANK, BLANK],
             ["Covered entity customer", BLANK, "24 hours per BAA", BLANK, BLANK],
             ["Affected individuals", BLANK, "60 days", BLANK, BLANK],
             ["HHS OCR", BLANK, "60 days if 500+", BLANK, BLANK],
             ["Media", BLANK, "60 days if 500+ in a state", BLANK, BLANK],
             ["Cyber insurance", BLANK, "Per policy", BLANK, BLANK],
             ["Law enforcement", BLANK, "As advised", BLANK, BLANK]],
            widths=[1.4, 0.9, 1.5, 1.2, 1.2])
    d.h1("Root cause")
    d.table(["Field", "Value"],
            [["Immediate cause", BLANK], ["Underlying cause", BLANK],
             ["Why detection did or did not work", BLANK],
             ["Why existing controls did not prevent it", BLANK]], widths=[2.2, 4.0])
    d.h1("Corrective actions")
    d.table(["Action", "Type (preventive/detective)", "Owner", "Target", "POA&M ID"],
            [[BLANK] * 5 for _ in range(5)], widths=[2.0, 1.3, 1.0, 0.9, 1.0])
    d.h1("Closure")
    d.table(["Role", "Name", "Signature", "Date"],
            [["Incident lead", BLANK, BLANK, BLANK],
             ["Security Officer", BLANK, BLANK, BLANK],
             ["Privacy Officer (if PHI)", BLANK, BLANK, BLANK],
             ["CEO", BLANK, BLANK, BLANK]], widths=[1.7, 1.6, 1.6, 1.3])
    _emit(d, TPL, "Incident Report")


def t_metrics():
    d = _tpl("AGT-T-005", "Security Metrics Dashboard Template",
             "Monthly security posture reporting to the CEO required by AGT-CMS-024 section 7.")
    d.h1("Reporting period")
    d.table(["Field", "Value"], [["Period", BLANK], ["Prepared by", BLANK], ["Date", BLANK]],
            widths=[1.8, 4.4])
    d.h1("Posture metrics")
    d.table(["Metric", "Target", "This period", "Last period", "Trend"],
            [["Security score", "70+", BLANK, BLANK, BLANK],
             ["Critical findings", "0", BLANK, BLANK, BLANK],
             ["High findings", "Trending down", BLANK, BLANK, BLANK],
             ["Medium findings", "-", BLANK, BLANK, BLANK],
             ["Scanner coverage", "100%", BLANK, BLANK, BLANK],
             ["Mean time to triage", "7 days", BLANK, BLANK, BLANK],
             ["Mean time to remediate (Critical)", "7 days", BLANK, BLANK, BLANK],
             ["Open POA&M items past due", "0", BLANK, BLANK, BLANK],
             ["Access reviews completed on schedule", "100%", BLANK, BLANK, BLANK],
             ["Backup tests passed", "100%", BLANK, BLANK, BLANK]],
            widths=[2.1, 1.1, 1.1, 1.1, 0.8])
    d.h1("Suppression accounting")
    d.p("Reported separately from remediation. A score improved by deferral and a score "
        "improved by repair are different facts and must never be presented as one.")
    d.table(["Measure", "Count", "Notes"],
            [["Findings remediated this period", BLANK, BLANK],
             ["Findings newly suppressed", BLANK, BLANK],
             ["Suppressions expiring in 90 days", BLANK, BLANK],
             ["Suppressions expired and re-raised", BLANK, BLANK]], widths=[2.5, 1.0, 2.7])
    d.h1("Coverage caveats")
    d.p("State any scanner that did not run and what capability is therefore absent from "
        "these numbers. A score reported without its coverage gap overstates posture.")
    d.table(["Scanner", "Ran", "If not, capability absent"],
            [[BLANK] * 3 for _ in range(4)], widths=[1.5, 0.8, 3.9])
    d.h1("Incidents this period")
    d.table(["ID", "Severity", "Summary", "Status", "Notifications required"],
            [[BLANK] * 5 for _ in range(4)], widths=[0.8, 0.9, 2.2, 1.0, 1.3])
    d.h1("What is not working")
    d.p("Required section. If nothing is listed here, state explicitly that the control set "
        "operated as designed for the period and who verified it.")
    d.table(["Control", "How it failed", "Action", "Owner"],
            [[BLANK] * 4 for _ in range(4)], widths=[1.5, 2.3, 1.5, 0.9])
    _emit(d, TPL, "Security Metrics Dashboard")


def t_risk_register():
    d = _tpl("AGT-T-006", "Risk Register Template",
             "The working risk register maintained under AGT-RMP-023. Reviewed quarterly.")
    d.h1("Register")
    d.table(["ID", "Risk", "Threat source", "L", "I", "Rating", "Treatment", "Owner", "Review"],
            [[BLANK] * 9 for _ in range(12)],
            widths=[0.4, 1.5, 0.9, 0.4, 0.4, 0.6, 1.1, 0.6, 0.6])
    d.h1("Risk acceptance record")
    d.p("An acceptance without a review date is an abandonment. Every accepted risk carries "
        "an expiry after which it returns for re-evaluation.")
    d.table(["Risk ID", "Rating", "Rationale for acceptance", "Accepted by", "Accepted on", "Expires"],
            [[BLANK] * 6 for _ in range(5)], widths=[0.7, 0.7, 2.2, 1.0, 0.9, 0.7])
    d.h1("Treatment tracking")
    d.table(["Risk ID", "Treatment action", "Owner", "Target", "Status", "Residual rating"],
            [[BLANK] * 6 for _ in range(8)], widths=[0.7, 2.0, 0.9, 0.8, 0.8, 1.0])
    d.h1("Quarterly review record")
    d.table(["Review date", "Risks added", "Risks closed", "Risks re-rated", "Reviewer"],
            [[BLANK] * 5 for _ in range(4)], widths=[1.1, 1.1, 1.1, 1.2, 1.7])
    _emit(d, TPL, "Risk Register")


def t_poam():
    d = _tpl("AGT-T-007", "POA&M Template",
             "Plan of Action and Milestones. Tracks every weakness that is not remediated "
             "within its severity window. Evidence for NIST CA-5 and PM-4.")
    d.h1("POA&M items")
    d.table(["ID", "Weakness", "Source", "Controls", "Severity", "Owner", "Target", "Status"],
            [[BLANK] * 8 for _ in range(12)],
            widths=[0.4, 1.7, 0.8, 0.8, 0.7, 0.7, 0.7, 0.7])
    d.h1("Milestones")
    d.table(["POA&M ID", "Milestone", "Target date", "Actual date", "Status"],
            [[BLANK] * 5 for _ in range(8)], widths=[0.9, 2.4, 1.0, 1.0, 0.9])
    d.h1("Deviations and extensions")
    d.p("A missed target is recorded with a reason and a new date approved by the accepting "
        "authority. Silently moving a target date is how a POA&M stops being evidence.")
    d.table(["POA&M ID", "Original target", "New target", "Reason", "Approved by"],
            [[BLANK] * 5 for _ in range(5)], widths=[0.9, 1.1, 1.0, 2.1, 1.1])
    d.h1("Closure evidence")
    d.table(["POA&M ID", "Closure date", "Evidence", "Verified by"],
            [[BLANK] * 4 for _ in range(6)], widths=[0.9, 1.1, 2.9, 1.3])
    _emit(d, TPL, "POA&M")


def t_pentest():
    d = _tpl("AGT-T-008", "Penetration Test Report Template",
             "Structure for the annual independent penetration test required by AGT-CMS-024 "
             "section 3. Completed by the testing provider and retained as evidence for "
             "NIST CA-8.")
    d.h1("Engagement details")
    d.table(["Field", "Value"],
            [["Testing provider", BLANK], ["Lead tester and credentials", BLANK],
             ["Test window", BLANK], ["Scope (in)", BLANK], ["Scope (explicitly out)", BLANK],
             ["Methodology", BLANK], ["Rules of engagement reference", BLANK],
             ["Authorization letter reference", BLANK]], widths=[2.2, 4.0])
    d.note("Scope exclusions matter as much as inclusions. A report that does not state what "
           "was not tested invites the reader to assume everything was.")
    d.h1("Executive summary")
    d.table(["Field", "Value"],
            [["Overall risk rating", BLANK], ["Critical findings", BLANK],
             ["High findings", BLANK], ["Medium findings", BLANK], ["Low findings", BLANK],
             ["Was the objective achieved (e.g. data access)", BLANK]], widths=[2.6, 3.6])
    d.h1("Findings")
    d.table(["ID", "Title", "Severity", "CVSS", "Affected", "Exploitability", "Status"],
            [[BLANK] * 7 for _ in range(10)],
            widths=[0.4, 1.7, 0.7, 0.5, 1.1, 1.0, 0.8])
    d.h1("Finding detail (repeat per finding)")
    d.table(["Field", "Value"],
            [["Finding ID", BLANK], ["Description", BLANK], ["Attack narrative", BLANK],
             ["Evidence / proof of concept", BLANK], ["Business impact", BLANK],
             ["Remediation", BLANK], ["Retest result", BLANK]], widths=[1.9, 4.3])
    d.h1("Remediation tracking")
    d.table(["Finding ID", "Owner", "Target", "Remediated", "Retested", "Closed"],
            [[BLANK] * 6 for _ in range(8)], widths=[1.0, 1.0, 0.9, 1.1, 1.1, 1.1])
    _emit(d, TPL, "Penetration Test Report")


def t_vuln():
    d = _tpl("AGT-T-009", "Vulnerability Scan Report Template",
             "Periodic reporting of automated scan results under AGT-CMS-024. The automated "
             "platform produces the underlying data; this template is the reviewed, "
             "human-attested summary.")
    d.h1("Scan metadata")
    d.table(["Field", "Value"],
            [["Scan date", BLANK], ["Scan ID", BLANK], ["Target scope", BLANK],
             ["Scanners executed", BLANK], ["Scanners that did NOT execute", BLANK],
             ["Reviewed by", BLANK]], widths=[2.2, 4.0])
    d.h1("Results")
    d.table(["Severity", "New", "Existing", "Reopened", "Suppressed", "Total active"],
            [["Critical", BLANK, BLANK, BLANK, BLANK, BLANK],
             ["High", BLANK, BLANK, BLANK, BLANK, BLANK],
             ["Medium", BLANK, BLANK, BLANK, BLANK, BLANK],
             ["Low", BLANK, BLANK, BLANK, BLANK, BLANK]],
            widths=[1.2, 0.9, 1.0, 1.0, 1.1, 1.0])
    d.h1("Coverage statement")
    d.p("Any scanner that did not execute leaves a capability gap in these results. State "
        "the gap explicitly; a reader will otherwise treat the totals as complete.")
    d.table(["Scanner", "Executed", "Findings", "If skipped, why and what is missed"],
            [[BLANK] * 4 for _ in range(6)], widths=[1.2, 0.9, 0.9, 3.2])
    d.h1("Triage decisions")
    d.table(["Finding ID", "Disposition", "Rationale", "Suppression expiry", "POA&M ID"],
            [[BLANK] * 5 for _ in range(8)], widths=[1.0, 1.1, 2.1, 1.1, 0.9])
    d.h1("Attestation")
    d.p("I confirm the results above were reviewed, that each disposition reflects an "
        "assessment of the underlying code or configuration rather than a bulk action, and "
        "that every suppression carries a documented reason and an expiry date.")
    d.table(["Role", "Name", "Signature", "Date"],
            [["Reviewer", BLANK, BLANK, BLANK]], widths=[1.5, 1.7, 1.7, 1.3])
    _emit(d, TPL, "Vulnerability Scan Report")


def t_change():
    d = _tpl("AGT-T-010", "Change Record Template",
             "Request for Change and its implementation record under AGT-ChMP-017. Retained "
             "six years. Evidence for NIST CM-3 and SOC 2 CC8.1.")
    d.h1("Request")
    d.table(["Field", "Value"],
            [["Change ID", BLANK], ["Requested by", BLANK], ["Date requested", BLANK],
             ["Category (standard/normal/major/emergency)", BLANK],
             ["Description", BLANK], ["Business justification", BLANK],
             ["Systems affected", BLANK], ["Data classes affected", BLANK]],
            widths=[2.6, 3.6])
    d.h1("Risk and impact")
    d.table(["Field", "Value"],
            [["What breaks if this fails", BLANK], ["Security impact", BLANK],
             ["Privacy impact", BLANK], ["Downtime expected", BLANK],
             ["Customer impact", BLANK]], widths=[2.2, 4.0])
    d.h1("Testing")
    d.table(["Test performed", "Environment", "Result", "Evidence"],
            [[BLANK] * 4 for _ in range(5)], widths=[2.1, 1.2, 1.0, 1.9])
    d.h1("Implementation plan")
    d.table(["Step", "Action", "Owner", "Verification"],
            [[BLANK] * 4 for _ in range(6)], widths=[0.5, 2.5, 1.0, 2.2])
    d.h1("Rollback plan")
    d.table(["Field", "Value"],
            [["Rollback procedure", BLANK], ["Point of no return", BLANK],
             ["Rollback tested", BLANK], ["Data migration reversibility", BLANK]],
            widths=[2.2, 4.0])
    d.note("A change without a stated rollback is not approved. Where a schema change makes "
           "rollback non-trivial, the plan must include a tested down-migration or a "
           "forward-compatible design.")
    d.h1("Approval")
    d.table(["Role", "Name", "Decision", "Date"],
            [["Engineering lead", BLANK, BLANK, BLANK],
             ["Security Officer", BLANK, BLANK, BLANK],
             ["CEO (major/emergency)", BLANK, BLANK, BLANK]], widths=[1.7, 1.6, 1.5, 1.4])
    d.h1("Implementation record")
    d.table(["Field", "Value"],
            [["Implemented by", BLANK], ["Start time (UTC)", BLANK], ["End time (UTC)", BLANK],
             ["Deployment record status (server-side)", BLANK],
             ["Verification endpoint unique to the new build", BLANK],
             ["Verification result", BLANK], ["Rollback required", BLANK]],
            widths=[2.6, 3.6])
    d.note("Verify from the server-side deployment record, not the CLI exit status - the "
           "deployment tool reports connection failures while the deployment is still "
           "succeeding. Verify with an endpoint that exists only in the new build; the "
           "health endpoint answers from the old code throughout.")
    _emit(d, TPL, "Change Record")


def t_vendor():
    d = _tpl("AGT-T-011", "Vendor Review Template",
             "Annual reassessment of a Critical or High tier vendor under AGT-VRM-012 "
             "section 8. Evidence for NIST SA-9 and SOC 2 CC9.2.")
    d.h1("Vendor identification")
    d.table(["Field", "Value"],
            [["Vendor name", BLANK], ["Service provided", BLANK], ["Tier", BLANK],
             ["Data classes exposed", BLANK], ["PHI involved (yes/no)", BLANK],
             ["Contract reference", BLANK], ["Contract renewal date", BLANK],
             ["AGT relationship owner", BLANK]], widths=[2.2, 4.0])
    d.h1("Assurance evidence")
    d.table(["Evidence type", "Provided", "Date", "Scope covers our service", "Exceptions noted"],
            [["SOC 2 Type II", BLANK, BLANK, BLANK, BLANK],
             ["ISO 27001 certificate", BLANK, BLANK, BLANK, BLANK],
             ["HITRUST", BLANK, BLANK, BLANK, BLANK],
             ["Penetration test summary", BLANK, BLANK, BLANK, BLANK],
             ["Security questionnaire", BLANK, BLANK, BLANK, BLANK]],
            widths=[1.5, 0.9, 0.8, 1.6, 1.4])
    d.p("Read the scope section of any attestation before accepting it. A report whose scope "
        "excludes the service AGT actually consumes is not evidence about that service.")
    d.h1("Contract requirements verification")
    d.table(["Requirement", "Present in contract", "Reference", "Gap"],
            [["Business Associate Agreement (if PHI)", BLANK, BLANK, BLANK],
             ["Breach notification within required window", BLANK, BLANK, BLANK],
             ["Right to audit or equivalent", BLANK, BLANK, BLANK],
             ["Data return and deletion on termination", BLANK, BLANK, BLANK],
             ["Subcontractor flow-down", BLANK, BLANK, BLANK],
             ["Data residency (US)", BLANK, BLANK, BLANK],
             ["No training on AGT data", BLANK, BLANK, BLANK],
             ["Zero data retention (AI providers)", BLANK, BLANK, BLANK]],
            widths=[2.2, 1.2, 1.4, 1.4])
    d.h1("Changes since last review")
    d.table(["Change", "Impact on AGT risk", "Action"],
            [["Ownership or acquisition", BLANK, BLANK],
             ["Certification lapse", BLANK, BLANK],
             ["Publicized breach", BLANK, BLANK],
             ["Sub-processor changes", BLANK, BLANK],
             ["Service or scope change", BLANK, BLANK]], widths=[1.7, 2.3, 2.2])
    d.h1("Determination")
    d.table(["Field", "Value"],
            [["Risk rating", BLANK], ["Continue / remediate / terminate", BLANK],
             ["Conditions imposed", BLANK], ["Next review date", BLANK],
             ["Reviewed by", BLANK], ["Approved by", BLANK]], widths=[2.2, 4.0])
    _emit(d, TPL, "Vendor Review")


def t_evidence_proc():
    d = _tpl("AGT-T-012", "Evidence Collection Procedure",
             "How AGT collects, names, stores, and produces compliance evidence. Written so "
             "that an assessment does not become an archaeology exercise.")
    d.h1("Principles")
    d.bullets([
        "Evidence is produced as a by-product of doing the work, not reconstructed before an "
        "audit. Reconstructed evidence is both expensive and unconvincing.",
        "Every recurring control activity in AGT-CMS-024 names the artifact it produces.",
        "Artifacts are dated, attributed, and immutable once complete.",
        "Automated output is retained in its original form alongside any human summary, so a "
        "reviewer can check the summary against the source.",
    ])
    d.h1("Evidence catalogue")
    d.table(["Control activity", "Artifact", "Frequency", "Retention", "Location"],
            [["Access review", "Quarterly Access Review record", "Quarterly", "6 years", "Compliance repository"],
             ["Backup restoration test", "Backup Test Record", "Quarterly", "6 years", "Compliance repository"],
             ["DR exercise", "DR Test Record", "Annually", "6 years", "Compliance repository"],
             ["Security scanning", "Scan report, SBOM, dashboard", "Nightly", "Life of release", "Scan artifacts"],
             ["Scan triage", "Vulnerability Scan Report", "Weekly", "6 years", "Compliance repository"],
             ["Incident", "Incident Report", "Per event", "6 years", "Compliance repository"],
             ["Change", "Change Record", "Per change", "6 years", "Repository and pipeline logs"],
             ["Vendor review", "Vendor Review record", "Annually", "6 years", "Compliance repository"],
             ["Risk review", "Risk Register", "Quarterly", "6 years", "Compliance repository"],
             ["Metrics reporting", "Security Metrics Dashboard", "Monthly", "6 years", "Compliance repository"],
             ["Penetration test", "Penetration Test Report", "Annually", "6 years", "Compliance repository"],
             ["HIPAA risk assessment", "AGT-HIRA-025", "Annually", "6 years", "Compliance repository"]],
            widths=[1.6, 1.6, 0.9, 0.8, 1.3])
    d.h1("Naming convention")
    d.p("YYYY-MM-DD_ArtifactType_Scope_vN. Sorting by name sorts by date, and the scope "
        "makes the right artifact findable without opening it.")
    d.h1("Integrity")
    d.bullets([
        "Completed evidence is stored in a version-controlled repository, which provides an "
        "immutable history and attribution.",
        "Automated scan output is retained unmodified; edits are made to the summary, never "
        "to the source artifact.",
        "Where an artifact is amended, the amendment is a new version with a stated reason. "
        "Overwriting evidence destroys its value.",
    ])
    d.h1("Producing evidence for an assessment")
    d.numbered([
        "Identify the controls in scope from AGT-MCM-026.",
        "For each control, locate the owning policy and the artifact named in the catalogue.",
        "Assemble artifacts for the assessment period, not a sample chosen for quality.",
        "Where a control has no artifact for a period, say so and explain why - a gap "
        "disclosed is a finding, a gap concealed is a credibility problem.",
        "Provide the coverage caveats alongside any metric, particularly scanner coverage.",
    ])
    _emit(d, TPL, "Evidence Collection Procedure")


# ─────────────────────────── Tier 4 assessment package ───────────────────────────
def a_sap():
    d = AGTDoc("AGT-A-001", "Security Assessment Plan", classification="Confidential")
    d.h1("1. Purpose")
    d.p("This plan defines how the security controls for DocuAction TEFCA ARC are assessed: "
        "what is examined, by what method, against what criteria, and how results are "
        "reported. It is the plan an assessor executes and the plan against which AGT "
        "prepares.")
    d.platform_context()
    d.h1("2. Scope")
    d.bullets([
        "All controls described in AGT-SSP-001 section 8.",
        "Production and development environments within the authorization boundary.",
        "Inherited controls are assessed by reviewing Microsoft's attestations, not by "
        "testing Azure infrastructure directly.",
        "External interfaces are assessed for the AGT-side control, not the provider's "
        "internal implementation.",
    ])
    d.h1("3. Assessment methods")
    d.table(["Method", "Definition", "Applied to"],
            [["Examine", "Review of documents, configurations, and records", "Policies, baselines, evidence artifacts"],
             ["Interview", "Discussion with personnel who operate a control", "Roles named in each policy"],
             ["Test", "Direct exercise of a control to observe its behaviour", "Authentication, authorization, encryption, backup restore"]],
            widths=[1.1, 2.5, 2.6])
    d.h1("4. Assessment procedures by family")
    d.table(["Family", "Primary method", "Specific procedure", "Expected evidence"],
            [["AC / IA", "Test", "Attempt unauthenticated and under-privileged access to protected endpoints", "401 or 403 responses; DAST results"],
             ["AU", "Examine + Test", "Trigger a PHI-surface access; confirm an audit record with required fields", "Audit record; AGT-LMP-018"],
             ["CM", "Examine", "Compare running configuration against the documented baseline", "Drift report; baseline table"],
             ["CP", "Test", "Execute a restore to a scratch environment and verify integrity", "Backup Test Record"],
             ["IR", "Interview + Examine", "Walk through a recent incident against the documented lifecycle", "Incident Report"],
             ["RA", "Examine", "Review the risk assessment and register for currency and coverage", "AGT-HIRA-025; risk register"],
             ["SA", "Examine + Test", "Review pipeline gates; confirm a failing gate blocks a release", "Workflow definitions; scan reports"],
             ["SC", "Test", "Verify TLS version enforcement and encryption at rest settings", "Configuration; DAST header tests"],
             ["SI", "Examine", "Review monitoring, alerting, and flaw remediation timeliness", "Alert rules; POA&M"]],
            widths=[0.9, 1.2, 2.4, 1.7])
    d.h1("5. Testing constraints")
    d.bullets([
        "Dynamic testing is performed against the development environment only. Production "
        "is never a test target, and the testing tooling contains an automatic abort if a "
        "production endpoint is supplied.",
        "Rate limits are respected during testing so that assessment does not become a "
        "denial of service.",
        "No production data is modified and no production email is sent during assessment.",
        "Where a control can only be observed in production, it is assessed by examination "
        "of configuration and logs rather than by test.",
    ])
    d.h1("6. Determination criteria")
    d.table(["Result", "Meaning"],
            [["Satisfied", "The control is implemented and operating as described."],
             ["Other than satisfied", "The control is absent, incompletely implemented, or not operating."],
             ["Not applicable", "The control does not apply to this system, with stated reason."],
             ["Inherited", "Implemented by Microsoft; evidenced by their attestation."]],
            widths=[1.6, 4.6])
    d.p("A control described in policy but with no operating evidence is 'other than "
        "satisfied'. Documentation is necessary and not sufficient; the distinction between "
        "a written control and an operating control is the main thing an assessment exists "
        "to establish.")
    d.h1("7. Reporting")
    d.bullets([
        "Each 'other than satisfied' determination produces a finding with the control, the "
        "evidence examined, the deficiency, and the risk.",
        "Findings enter the POA&M with an owner and a target date.",
        "The assessment report states scope exclusions as prominently as results.",
    ])
    d.h1("8. Schedule and roles")
    d.table(["Activity", "Timing", "Performed by"],
            [["Assessment planning", "4 weeks before", "Security Officer"],
             ["Evidence assembly", "2 weeks before", "Security Officer, Engineering"],
             ["Assessment execution", "Assessment window", "Assessor"],
             ["Draft findings review", "Within 1 week of completion", "AGT and assessor"],
             ["Final report", "Within 2 weeks", "Assessor"],
             ["POA&M update", "Within 1 week of final report", "Security Officer"]],
            widths=[2.0, 1.6, 2.6])
    d.compliance_mapping(
        [["NIST 800-53", "CA-2, CA-5, CA-8", "Control assessment, POA&M, penetration testing.", "Met"],
         ["NIST 800-53A", "Assessment procedures", "Examine, interview, test methods adopted.", "Met"],
         ["HIPAA", "164.308(a)(8)", "Periodic technical and non-technical evaluation.", "Met"],
         ["SOC 2", "CC4.1", "Evaluation of control effectiveness.", "Met"],
         ["ISO 27001", "Clause 9.2", "Internal audit programme.", "Met"],
         ["FedRAMP", "CA-2(1)", "Independent assessor requirement.", "Partial - independent assessment not yet performed."]])
    d.related([["AGT-SSP-001", "System Security Plan", "Controls under assessment"],
               ["AGT-MCM-026", "Master Compliance Matrix", "Control to policy index"],
               ["AGT-CMS-024", "Continuous Monitoring Strategy", "Between-assessment assurance"],
               ["AGT-T-007", "POA&M Template", "Findings tracking"]])
    d.closing()
    _emit(d, ASM, "Security Assessment Plan")


def a_ato():
    d = AGTDoc("AGT-A-002", "ATO Readiness Guide", classification="Confidential")
    d.h1("1. Purpose")
    d.p("This guide states what AGT must have in place to pursue an Authority to Operate, "
        "what currently exists, and what remains. It is written to be usable as a "
        "self-assessment rather than as an aspiration.")
    d.platform_context()
    d.h1("2. Readiness checklist")
    d.table(["Requirement", "Status", "Artifact", "Gap"],
            [["System Security Plan", "Complete", "AGT-SSP-001", "-"],
             ["Security categorization (FIPS 199)", "Complete", "AGT-SSP-001 s3", "-"],
             ["Authorization boundary defined", "Complete", "AGT-SSP-001 s4", "-"],
             ["Policy set covering all control families", "Complete", "AGT-MCM-026", "-"],
             ["Risk assessment", "Complete", "AGT-HIRA-025", "-"],
             ["Incident response plan and exercise", "Complete", "AGT-IRP-022", "Annual exercise due"],
             ["Contingency plan and DR test", "Complete", "AGT-BCP-020, AGT-DRP-021", "Annual test due"],
             ["Continuous monitoring strategy", "Complete", "AGT-CMS-024", "-"],
             ["Configuration baselines", "Complete", "AGT-CfMP-016", "Open configuration findings"],
             ["Vulnerability scanning programme", "Partial", "Scan reports", "One scanner non-functional"],
             ["Independent penetration test", "Not started", "-", "Required"],
             ["POA&M", "Complete", "AGT-SSP-001 s9", "-"],
             ["Business associate agreements", "Partial", "AGT-VRM-012", "Two AI providers outstanding"],
             ["Evidence collection procedure", "Complete", "AGT-T-012", "-"],
             ["Security awareness training", "Complete", "Training records", "-"],
             ["3PAO assessment (FedRAMP path)", "Not started", "-", "Required for FedRAMP only"]],
            widths=[2.1, 0.9, 1.5, 1.7])
    d.h1("3. Critical path to authorization")
    d.numbered([
        "Execute business associate agreements with both AI providers, or technically block "
        "PHI from reaching them. This is the highest-severity open item and it is a legal "
        "requirement rather than a best practice.",
        "Restore full scanner coverage. Every security metric AGT currently reports carries "
        "a coverage caveat, which an assessor will treat as an unknown rather than as a zero.",
        "Commission an independent penetration test. Internal assessment cannot satisfy the "
        "independence requirement.",
        "Close the configuration findings: Key Vault reference for the database credential, "
        "FTP disabled, health check configured.",
        "Implement audit log tamper-evidence, or accept and document the limitation on the "
        "log's forensic strength.",
        "Perform the annual DR test and incident response exercise, and retain the records.",
    ])
    d.h1("4. What an assessor will probe first")
    d.p("Based on the profile of this system, the areas most likely to attract early "
        "scrutiny:")
    d.bullets([
        "The PHI flow to third-party AI providers, and the contractual basis for it.",
        "Whether the audit trail actually captures PHI access, and whether it can be altered.",
        "Whether suppressed scan findings are deferrals with expiry or quiet dismissals.",
        "Whether the deployment pipeline can be bypassed, and whether anything does bypass it.",
        "Whether inherited controls are genuinely inherited or merely assumed.",
    ])
    d.h1("5. Evidence package structure")
    d.table(["Section", "Contents"],
            [["1. System documentation", "SSP, architecture, data flow, boundary"],
             ["2. Policies", "AGT-EISP-001 through AGT-MCM-026"],
             ["3. Risk", "HIPAA risk assessment, risk register, POA&M"],
             ["4. Operational evidence", "Access reviews, backup and DR tests, incident records, change records"],
             ["5. Technical evidence", "Scan reports, SBOMs, configuration exports, DAST results"],
             ["6. Third-party", "Vendor assessments, BAAs, cloud provider attestations"],
             ["7. Assessment", "Assessment plan, penetration test, prior findings and closure"]],
            widths=[1.8, 4.4])
    d.compliance_mapping(
        [["NIST 800-53", "CA-6, PM-10", "Authorization and security authorization process.", "Partial"],
         ["NIST 800-37", "RMF", "Risk management framework steps followed through step 5.", "Partial"],
         ["FedRAMP", "Authorization package", "SSP, SAP, SAR, POA&M structure adopted.", "Partial"],
         ["HIPAA", "164.308(a)(8)", "Evaluation supporting authorization.", "Met"],
         ["SOC 2", "CC4.1", "Control evaluation.", "Met"]])
    d.related([["AGT-SSP-001", "System Security Plan", "Primary authorization artifact"],
               ["AGT-A-001", "Security Assessment Plan", "How controls are assessed"],
               ["AGT-A-006", "FedRAMP Readiness Roadmap", "FedRAMP-specific path"],
               ["AGT-HIRA-025", "HIPAA Organizational Risk Assessment", "Risk basis"]])
    d.closing()
    _emit(d, ASM, "ATO Readiness Guide")


def a_poam_proc():
    d = AGTDoc("AGT-A-003", "POA&M Process", classification="Internal")
    d.h1("1. Purpose")
    d.p("This document defines how a Plan of Action and Milestones is created, maintained, "
        "and closed at AGT. The POA&M is the record of what AGT knows is wrong and what it "
        "is doing about it; its credibility depends on items being closed by remediation "
        "rather than by attrition.")
    d.platform_context()
    d.h1("2. When a POA&M item is created")
    d.bullets([
        "A finding is not remediated within its severity's target window (AGT-CMS-024 s6).",
        "An assessment or audit produces an 'other than satisfied' determination.",
        "A risk is accepted at High rating - acceptance does not remove the tracking obligation.",
        "An incident post-review identifies a corrective action.",
        "A control is found to be documented but not operating.",
    ])
    d.h1("3. Required fields")
    d.table(["Field", "Requirement"],
            [["Identifier", "Unique and stable; never reused"],
             ["Weakness description", "What is wrong, specifically enough to verify closure"],
             ["Source", "Scan, assessment, incident, or self-identified"],
             ["Controls affected", "NIST 800-53 references"],
             ["Severity", "Critical, High, Moderate, Low"],
             ["Owner", "A named individual, never a team"],
             ["Resources required", "Whether closure needs funding or external help"],
             ["Milestones", "Intermediate steps with dates"],
             ["Target completion", "A date, not a quarter"],
             ["Status", "Open, in progress, completed, risk accepted"]],
            widths=[1.6, 4.6])
    d.h1("4. Lifecycle")
    d.numbered([
        "Identification - the finding is confirmed and scoped.",
        "Assignment - a named owner and a target date are set by the Security Officer.",
        "Planning - milestones are defined for any item beyond 30 days.",
        "Execution - the owner reports progress at the monthly review.",
        "Verification - the Security Officer independently confirms closure. Self-reported "
        "closure is not sufficient for High or Critical items.",
        "Closure - evidence is attached and the item is marked complete with a date.",
    ])
    d.h1("5. Review cadence")
    d.table(["Review", "Frequency", "Participants", "Output"],
            [["Operational review", "Monthly", "Security Officer, owners", "Status update; deviations recorded"],
             ["Executive review", "Quarterly", "CEO, Security Officer", "Re-prioritization; funding decisions"],
             ["Full reconciliation with the risk register", "Quarterly", "Security Officer", "Aligned register and POA&M"]],
            widths=[2.3, 1.0, 1.4, 1.5])
    d.h1("6. Deviations")
    d.p("A missed target date is recorded as a deviation with a reason and a new date "
        "approved by the accepting authority. Target dates are never silently revised; the "
        "history of a slipping item is itself information about the organization's capacity "
        "and is what a reviewer looks for.")
    d.h1("7. Prohibited practices")
    d.bullets([
        "Closing an item because the finding was suppressed in a tool rather than fixed in "
        "the system.",
        "Closing an item without evidence.",
        "Reassigning an item to a departing employee.",
        "Downgrading severity to extend the target window without a documented reassessment.",
    ])
    d.compliance_mapping(
        [["NIST 800-53", "CA-5, PM-4", "Plan of action and milestones process.", "Met"],
         ["NIST 800-37", "RMF Step 5-6", "Authorization and monitoring.", "Met"],
         ["HIPAA", "164.308(a)(1)(ii)(B)", "Risk management - remediation tracking.", "Met"],
         ["SOC 2", "CC4.2", "Communication and remediation of deficiencies.", "Met"],
         ["FedRAMP", "CA-5", "POA&M maintained monthly.", "Met"]])
    d.related([["AGT-T-007", "POA&M Template", "The working artifact"],
               ["AGT-CMS-024", "Continuous Monitoring Strategy", "Source of most items"],
               ["AGT-RMP-023", "Risk Management Plan", "Risk register reconciliation"],
               ["AGT-SSP-001", "System Security Plan", "POA&M summary"]])
    d.closing()
    _emit(d, ASM, "POA&M Process")


def a_conmon():
    d = AGTDoc("AGT-A-004", "Continuous Monitoring Plan", classification="Internal")
    d.h1("1. Purpose")
    d.p("This plan is the operational companion to AGT-CMS-024. Where the strategy states "
        "what AGT monitors and why, this plan states who does what, on which day, and what "
        "artifact results.")
    d.platform_context()
    d.h1("2. Monitoring calendar")
    d.table(["Cadence", "Activity", "Owner", "Artifact"],
            [["Nightly", "Automated full security scan", "Automated", "Scan report, SBOM"],
             ["Weekly", "Scan triage and disposition", "Security Officer", "Vulnerability Scan Report"],
             ["Weekly", "Security event log review", "Security Officer", "Review record"],
             ["Monthly", "Privileged action review", "Security Officer", "Review record"],
             ["Monthly", "Backup status verification", "Engineering", "Status record"],
             ["Monthly", "POA&M operational review", "Security Officer", "Updated POA&M"],
             ["Monthly", "Metrics report to CEO", "Security Officer", "Metrics Dashboard"],
             ["Quarterly", "Access review", "Security Officer", "Access Review record"],
             ["Quarterly", "Configuration drift review", "Engineering", "Drift report"],
             ["Quarterly", "Backup restoration test", "Engineering", "Backup Test Record"],
             ["Quarterly", "Risk register review", "Security Officer", "Updated register"],
             ["Quarterly", "POA&M executive review", "CEO", "Meeting record"],
             ["Annually", "HIPAA risk assessment refresh", "Security Officer", "AGT-HIRA-025"],
             ["Annually", "DR test", "Engineering", "DR Test Record"],
             ["Annually", "Incident response exercise", "Security Officer", "Exercise record"],
             ["Annually", "Vendor reassessment", "Security Officer", "Vendor Review records"],
             ["Annually", "Policy review", "CEO", "Version history"],
             ["Annually", "Penetration test", "Third party", "Penetration Test Report"]],
            widths=[1.0, 2.3, 1.4, 1.5])
    d.h1("3. Control-to-activity mapping")
    d.table(["Control", "Monitoring activity", "Frequency"],
            [["AC-2", "Access review", "Quarterly"],
             ["AU-6", "Security event and privileged action review", "Weekly / monthly"],
             ["CM-6", "Configuration drift review", "Quarterly"],
             ["CP-9", "Backup verification and restoration test", "Monthly / quarterly"],
             ["IR-4", "Incident response exercise", "Annually"],
             ["RA-3", "Risk assessment refresh", "Annually"],
             ["RA-5", "Vulnerability scanning and triage", "Nightly / weekly"],
             ["SA-9", "Vendor reassessment", "Annually"],
             ["SI-2", "Flaw remediation tracking", "Weekly"]],
            widths=[0.9, 3.3, 2.0])
    d.h1("4. Escalation")
    d.table(["Trigger", "Escalate to", "Within"],
            [["Critical finding", "CEO and Security Officer", "Immediately"],
             ["Control found not operating", "Security Officer", "1 business day"],
             ["POA&M item 30 days past due", "CEO", "At monthly review"],
             ["Two consecutive backup failures", "CEO", "Immediately"],
             ["Scanner coverage loss", "Security Officer", "Next report, stated explicitly"]],
            widths=[2.3, 2.3, 1.6])
    d.h1("5. What makes this plan fail")
    d.p("Recorded deliberately, because these are the observed failure modes of continuous "
        "monitoring programmes rather than hypothetical ones:")
    d.bullets([
        "Activities performed but not recorded - the control operated and cannot be shown to "
        "have operated.",
        "Metrics reported without coverage caveats, so a partial scan reads as a clean one.",
        "Suppression used as a substitute for remediation, improving the number without "
        "improving the system.",
        "Reviews that confirm rather than examine - a quarterly access review that never "
        "revokes anything is not a review.",
    ])
    d.compliance_mapping(
        [["NIST 800-53", "CA-7", "Continuous monitoring implementation.", "Met"],
         ["NIST 800-137", "ISCM", "Operational continuous monitoring plan.", "Met"],
         ["HIPAA", "164.308(a)(1)(ii)(D)", "Information system activity review.", "Met"],
         ["SOC 2", "CC4.1, CC4.2", "Ongoing monitoring and deficiency communication.", "Met"],
         ["ISO 27001", "Clause 9.1", "Monitoring, measurement, analysis, evaluation.", "Met"],
         ["FedRAMP", "CA-7", "Monthly, quarterly, annual monitoring deliverables.", "Partial"]])
    d.related([["AGT-CMS-024", "Continuous Monitoring Strategy", "Strategy this plan operationalizes"],
               ["AGT-A-003", "POA&M Process", "Handling of items arising"],
               ["AGT-T-012", "Evidence Collection Procedure", "Artifact handling"]])
    d.closing()
    _emit(d, ASM, "Continuous Monitoring Plan")


def a_annual():
    d = AGTDoc("AGT-A-005", "Annual Assessment Guide", classification="Internal")
    d.h1("1. Purpose")
    d.p("A practical guide to conducting AGT's annual security assessment: what to do, in "
        "what order, over what timeline, and how to avoid the common failure of assembling "
        "evidence that describes the assessment period rather than evidence produced during "
        "it.")
    d.platform_context()
    d.h1("2. Twelve-week timeline")
    d.table(["Week", "Activity", "Owner", "Output"],
            [["1-2", "Confirm scope; update the SSP for any architecture change", "Security Officer", "Updated AGT-SSP-001"],
             ["3-4", "Refresh the HIPAA risk assessment", "Security Officer", "AGT-HIRA-025"],
             ["5", "Assemble operational evidence for the period", "Security Officer, Engineering", "Evidence package"],
             ["6", "Run a full scan and reconcile findings with the POA&M", "Engineering", "Scan report, POA&M"],
             ["7-8", "Internal control assessment per AGT-A-001", "Security Officer", "Draft findings"],
             ["9", "Remediate quick wins; update the POA&M", "Engineering", "Updated POA&M"],
             ["10", "Independent penetration test", "Third party", "Penetration Test Report"],
             ["11", "Consolidate results; executive review", "CEO, Security Officer", "Assessment report"],
             ["12", "Policy review and reissue; close the cycle", "CEO", "Updated policy set"]],
            widths=[0.7, 2.5, 1.5, 1.5])
    d.h1("3. Evidence completeness check")
    d.p("Before the assessment, verify that each recurring activity actually produced its "
        "artifact during the period. A missing artifact is a finding whether or not the "
        "activity happened; discovering it beforehand is cheaper than discovering it during.")
    d.table(["Artifact", "Expected count per year", "Present", "Gap"],
            [["Quarterly Access Review", "4", BLANK, BLANK],
             ["Backup Test Record", "4", BLANK, BLANK],
             ["Vulnerability Scan Report", "52", BLANK, BLANK],
             ["Security Metrics Dashboard", "12", BLANK, BLANK],
             ["Risk Register review", "4", BLANK, BLANK],
             ["DR Test Record", "1", BLANK, BLANK],
             ["Incident response exercise", "1", BLANK, BLANK],
             ["Vendor Review (per Critical/High vendor)", "1 each", BLANK, BLANK],
             ["Penetration Test Report", "1", BLANK, BLANK],
             ["HIPAA risk assessment", "1", BLANK, BLANK]],
            widths=[2.4, 1.5, 1.1, 1.2])
    d.h1("4. Policy review checklist")
    d.bullets([
        "Does each policy still describe what the organization actually does? A policy that "
        "has drifted from practice is worse than none, because it creates an expectation the "
        "evidence will contradict.",
        "Have architecture changes been reflected in the SSP and the affected policies?",
        "Are all cross-references still valid after any renumbering?",
        "Has every 'Partial' status either progressed or been re-justified?",
        "Are the open findings in each policy still accurate, or have some been closed "
        "without the policy being updated?",
    ])
    d.h1("5. Common findings to pre-empt")
    d.table(["Likely finding", "How to avoid it"],
            [["Evidence reconstructed after the fact", "Produce artifacts as work happens; dated and attributed"],
             ["Metrics without coverage caveats", "State scanner gaps in every report"],
             ["Suppressions without expiry", "Enforce expiry at the tooling level"],
             ["Policy describes a control that is not implemented", "Reconcile each policy against reality annually"],
             ["POA&M items closed without verification", "Independent confirmation for High and Critical"],
             ["Access review with no revocations ever", "Review against current need, not against the prior list"]],
            widths=[2.6, 3.6])
    d.compliance_mapping(
        [["NIST 800-53", "CA-2, CA-7, RA-3", "Assessment, monitoring, and risk assessment cycle.", "Met"],
         ["HIPAA", "164.308(a)(8)", "Periodic evaluation.", "Met"],
         ["HIPAA", "164.316(b)(2)(iii)", "Review and update of documentation.", "Met"],
         ["SOC 2", "CC4.1", "Ongoing and separate evaluations.", "Met"],
         ["ISO 27001", "Clause 9.2, 9.3", "Internal audit and management review.", "Met"]])
    d.related([["AGT-A-001", "Security Assessment Plan", "Assessment procedures"],
               ["AGT-CMS-024", "Continuous Monitoring Strategy", "Evidence cadence"],
               ["AGT-T-012", "Evidence Collection Procedure", "Artifact standards"],
               ["AGT-HIRA-025", "HIPAA Organizational Risk Assessment", "Annual refresh"]])
    d.closing()
    _emit(d, ASM, "Annual Assessment Guide")


def a_fedramp():
    d = AGTDoc("AGT-A-006", "FedRAMP Readiness Roadmap", classification="Confidential")
    d.h1("1. Purpose")
    d.p("This roadmap assesses AGT's distance from a FedRAMP authorization and sets out the "
        "work required. It is deliberately conservative: FedRAMP is substantially more "
        "demanding than the frameworks AGT currently satisfies, and a roadmap that "
        "understates the gap is worse than no roadmap.")
    d.platform_context()
    d.h1("2. Current position")
    d.table(["Dimension", "Current state", "FedRAMP MODERATE requirement", "Gap"],
            [["Control documentation", "Full policy set and SSP", "SSP in FedRAMP template with per-control detail", "Reformatting and expansion"],
             ["Control implementation", "Majority implemented, several partial", "All MODERATE baseline controls implemented", "Close partials"],
             ["Independent assessment", "None", "3PAO assessment required", "Engage a 3PAO"],
             ["Continuous monitoring", "Internal programme", "Monthly deliverables to the authorizing official", "Formalize reporting"],
             ["Boundary and inventory", "Documented", "Detailed inventory with every component enumerated", "Expand detail"],
             ["Incident reporting", "24-hour COR notification", "US-CERT reporting within 1 hour for confirmed incidents", "Tighten process"],
             ["FIPS-validated cryptography", "Azure-provided, validated modules", "FIPS 140-2/3 validated throughout", "Verify and evidence"],
             ["Personnel screening", "Performed, cadence informal", "Defined screening per position risk", "Formalize"],
             ["Supply chain", "Vendor assessment programme", "SR family fully implemented", "Expand provenance verification"]],
            widths=[1.3, 1.6, 1.9, 1.4])
    d.h1("3. Authorization path options")
    d.table(["Path", "Description", "Suitability for AGT"],
            [["Agency ATO", "A sponsoring agency authorizes; reusable by others", "Most likely path given existing federal contract work"],
             ["JAB P-ATO", "Joint Authorization Board provisional authorization", "High bar; typically for widely used services"],
             ["FedRAMP Tailored", "Reduced baseline for low-impact SaaS", "Not applicable - the system handles PHI at MODERATE"]],
            widths=[1.3, 2.4, 2.5])
    d.h1("4. Phased plan")
    d.h2("Phase 1 - Close known gaps (0-6 months)")
    d.bullets([
        "Execute the outstanding business associate agreements.",
        "Restore full scanner coverage.",
        "Migrate the remaining secret to Key Vault; disable FTP; configure health checks.",
        "Implement audit log tamper-evidence.",
        "Commission an independent penetration test.",
    ])
    d.h2("Phase 2 - Formalize (6-12 months)")
    d.bullets([
        "Re-author the SSP into the FedRAMP template with per-control implementation detail.",
        "Formalize personnel screening cadence and position risk designation.",
        "Stand up monthly continuous monitoring deliverables in the FedRAMP format.",
        "Implement SIEM correlation to satisfy the SI-4 enhancements.",
        "Expand the component inventory to FedRAMP granularity.",
    ])
    d.h2("Phase 3 - Assessment (12-18 months)")
    d.bullets([
        "Secure an agency sponsor.",
        "Engage an accredited 3PAO; complete a readiness assessment first.",
        "Remediate 3PAO findings; produce the Security Assessment Report.",
        "Submit the authorization package.",
    ])
    d.h1("5. Cost and effort considerations")
    d.p("Stated plainly because they are the usual reason a roadmap stalls: a FedRAMP "
        "authorization requires sustained investment in assessment fees, 3PAO engagement, "
        "and continuous monitoring effort that does not decrease after authorization. For "
        "an organization of AGT's size this is a strategic commitment tied to specific "
        "federal revenue, not an incremental compliance improvement. The decision to proceed "
        "should follow an identified agency sponsor rather than precede one.")
    d.h1("6. What AGT already has in its favour")
    d.bullets([
        "The system is built on Azure, which provides a substantial set of inheritable "
        "controls with existing FedRAMP authorization.",
        "The policy set already covers every NIST 800-53 control family.",
        "Automated scanning, SBOM generation, and a continuous monitoring programme are "
        "operating rather than planned.",
        "CMMI Level 3 and ISO 27001 certification demonstrate defined, audited process.",
        "The organization already reports coverage gaps honestly, which is the cultural "
        "prerequisite that most readiness efforts lack.",
    ])
    d.compliance_mapping(
        [["FedRAMP", "MODERATE baseline", "Gap analysis against the baseline documented here.", "Partial"],
         ["NIST 800-53", "Rev. 5 MODERATE", "Control implementation described in AGT-SSP-001.", "Partial"],
         ["NIST 800-37", "RMF", "Risk management framework alignment.", "Partial"],
         ["FIPS 199", "Categorization", "MODERATE determination complete.", "Met"],
         ["FIPS 140-2/3", "Cryptographic modules", "Inherited from Azure; evidence to be assembled.", "Partial"]])
    d.related([["AGT-SSP-001", "System Security Plan", "Basis for the FedRAMP SSP"],
               ["AGT-A-002", "ATO Readiness Guide", "Broader authorization readiness"],
               ["AGT-A-001", "Security Assessment Plan", "Assessment approach"],
               ["AGT-MCM-026", "Master Compliance Matrix", "Control coverage"]])
    d.closing()
    _emit(d, ASM, "FedRAMP Readiness Roadmap")


# ─────────────────────────── Tasks 6 and 7 ───────────────────────────
def zta():
    d = AGTDoc("AGT-ZTA-001", "Zero Trust Architecture", classification="Confidential")
    d.h1("1. Purpose")
    d.p("This document describes how zero trust principles are applied to DocuAction TEFCA "
        "ARC. The premise is straightforward and consequential: there is no trusted network "
        "in this architecture. The platform is reachable from the public internet, its users "
        "work from arbitrary locations, and its components communicate over provider "
        "networks. Trust is therefore established per request from identity and context, "
        "never from network position.")
    d.platform_context()
    d.h1("2. Zero trust tenets and their implementation")
    d.table(["NIST SP 800-207 tenet", "Implementation in this system"],
            [["All data sources and computing services are resources",
              "Every endpoint, the database, Key Vault, and each external interface is treated as a protected resource with its own access decision."],
             ["All communication is secured regardless of network location",
              "TLS 1.2+ end to end; database connections require SSL; no plaintext path exists internally or externally."],
             ["Access is granted per session",
              "Each request carries a bearer token validated for signature, expiry, revocation, and account state. Prior requests confer nothing."],
             ["Access is determined by dynamic policy",
              "Role requirement per endpoint, plus Entra ID Conditional Access evaluating device compliance, sign-in risk, and location."],
             ["The enterprise monitors the integrity and posture of all assets",
              "MDM for endpoints, nightly configuration drift detection, SBOM for components."],
             ["Authentication and authorization are dynamic and strictly enforced before access",
              "Authorization dependencies execute before the handler; the audit record is written before the resource is touched."],
             ["The enterprise collects information about asset state and uses it to improve posture",
              "Continuous monitoring under AGT-CMS-024 feeds the risk register and POA&M."]],
            widths=[1.9, 4.3])
    d.h1("3. Identity as the control plane")
    d.bullets([
        "Microsoft Entra ID is the authoritative identity provider, with MFA enforced by "
        "Conditional Access rather than by application logic - policy that lives in the "
        "identity platform cannot be bypassed by a flaw in the application.",
        "Both the federated and local authentication paths converge on one token format, so "
        "authorization is uniform and there is no weaker path to the same privilege.",
        "Server-side session revocation means disablement takes effect immediately rather "
        "than at token expiry. A stateless token without revocation is a standing grant.",
        "Service-to-service authentication uses managed identity, removing the stored "
        "credential entirely.",
    ])
    d.h1("4. Least privilege")
    d.table(["Domain", "Implementation"],
            [["Application roles", "Six-level hierarchy; each endpoint declares its minimum"],
             ["Azure RBAC", "Scoped to resource group; no standing subscription-level Contributor"],
             ["CI/CD", "Service principal limited to the two deployment resource groups"],
             ["Key Vault", "Read-only secret access for the application identity"],
             ["Database", "Application account holds only the privileges its queries require"],
             ["Administrative access", "Named individuals; time-bound elevation where available"]],
            widths=[1.5, 4.7])
    d.h1("5. Conditional access and device trust")
    d.table(["Signal", "Policy", "Effect"],
            [["MFA", "Required for all users", "No exception"],
             ["Device compliance", "Required for administrative roles", "Non-compliant device blocked"],
             ["Sign-in risk", "Evaluated by Entra ID Protection", "Step-up authentication"],
             ["Legacy authentication", "Blocked", "Cannot carry an MFA challenge"],
             ["Impossible travel", "Detected", "Re-authentication required"]],
            widths=[1.4, 2.2, 2.6])
    d.h1("6. Micro-segmentation and network posture")
    d.p("The architecture is deliberately simple, which reduces the segmentation surface: a "
        "stateless API tier, a managed database, and a static frontend with no server-side "
        "execution. The frontend cannot reach the database; only the API can. There is no "
        "lateral movement path between application components because there are no "
        "components to move between.")
    d.table(["Boundary", "Control", "Status"],
            [["Internet to frontend", "HTTPS only; static content", "Implemented"],
             ["Internet to API", "HTTPS only; trusted-host validation; strict CORS", "Implemented"],
             ["API to database", "SSL required; credentials scoped", "Implemented"],
             ["API to Key Vault", "Managed identity; RBAC", "Implemented"],
             ["API to AI providers", "TLS; minimum-necessary payload; scrubbing", "Implemented"],
             ["Database network exposure", "Private endpoint", "Roadmap - currently firewall-restricted"]],
            widths=[1.6, 3.1, 1.5])
    d.note("Private endpoints for the database are a roadmap item. The current control is "
           "firewall restriction plus mandatory SSL, which is materially weaker than removing "
           "public reachability altogether. Stated here rather than omitted, because a zero "
           "trust document that claims segmentation it does not have is the exact failure "
           "mode this architecture is meant to avoid.")
    d.h1("7. Continuous verification")
    d.bullets([
        "Every request re-validates identity, session state, and role. There is no "
        "'authenticated once' state that survives a role change or a disablement.",
        "Every PHI-surface access is recorded before the handler runs.",
        "Configuration drift is detected nightly, so a control that stops operating is "
        "discovered rather than assumed.",
        "Scanning runs nightly against the codebase and dependencies.",
    ])
    d.h1("8. Assume breach")
    d.bullets([
        "Secrets are held in Key Vault so that application compromise does not immediately "
        "yield the database credential - subject to the open finding that one credential is "
        "not yet migrated.",
        "Audit records are written to a store the application can append to but is not "
        "designed to rewrite; Azure activity logs are a separate domain entirely.",
        "Backups are managed by the platform and are outside the reach of an application-level "
        "compromise, which is what makes them useful against ransomware.",
        "Token signing key rotation invalidates every issued token, providing a single "
        "decisive containment action.",
    ])
    d.h1("9. Maturity assessment")
    d.table(["Pillar", "Current maturity", "Evidence", "Next step"],
            [["Identity", "Advanced", "MFA, Conditional Access, managed identity, revocation", "Enable PIM tenant-wide"],
             ["Devices", "Intermediate", "MDM, encryption, compliance signals", "Extend compliance requirement to all roles"],
             ["Networks", "Intermediate", "TLS everywhere, host validation, no internal trust", "Private endpoints for the database"],
             ["Applications", "Advanced", "Router-level authorization, input validation, scanning", "Close remaining partial controls"],
             ["Data", "Intermediate", "Classification, encryption, retention, audit", "Tamper-evident audit log"],
             ["Visibility and analytics", "Initial", "Monitoring and alerting configured", "SIEM correlation"],
             ["Automation and orchestration", "Intermediate", "CI/CD gates, nightly scanning", "Automated response to defined events"]],
            widths=[1.3, 1.2, 2.2, 1.5])
    d.compliance_mapping(
        [["NIST SP 800-207", "Zero Trust Architecture", "Tenets mapped to implementation in section 2.", "Met"],
         ["NIST 800-53", "AC-3, AC-6, IA-2, SC-7, SC-8", "Access enforcement, least privilege, authentication, boundary and transmission protection.", "Met"],
         ["CISA ZTMM", "Five pillars", "Maturity assessed in section 9.", "Partial"],
         ["OMB M-22-09", "Federal zero trust strategy", "Identity, devices, networks, applications, data.", "Partial"],
         ["HIPAA", "164.312(a)(1), (d), (e)(1)", "Access control, authentication, transmission security.", "Met"],
         ["SOC 2", "CC6.1, CC6.6", "Logical access and boundary protection.", "Met"],
         ["ISO 27001", "A.5.15, A.8.20, A.8.22", "Access control, network security, segregation of networks.", "Partial"]])
    d.related([["AGT-SSP-001", "System Security Plan", "Control implementation detail"],
               ["AGT-IAM-004", "Identity and Access Management Policy", "Identity control plane"],
               ["AGT-RAP-005", "Remote Access Policy", "No trusted network premise"],
               ["AGT-CKM-009", "Cryptographic Key Management Policy", "Encryption everywhere"],
               ["AGT-LMP-018", "Logging and Monitoring Policy", "Continuous verification data"]])
    d.closing()
    _emit(d, ROOT, "Zero Trust Architecture")


def ctm():
    d = AGTDoc("AGT-CTM-001", "Control Traceability Matrix", classification="Internal")
    d.h1("1. Purpose")
    d.p("This matrix traces each requirement through to the evidence that demonstrates it is "
        "satisfied: requirement, to policy, to procedure, to technical control, to evidence, "
        "to assessment, to POA&M where a gap remains. It answers the question an assessor "
        "actually asks, which is not 'do you have a policy' but 'show me that this operated'.")
    d.platform_context()
    d.h1("2. How to read this matrix")
    d.bullets([
        "Requirement - the external obligation, expressed in the source framework's terms.",
        "Policy - the AGT document that governs it.",
        "Procedure - the operational activity that carries it out.",
        "Technical control - what actually enforces it in the system.",
        "Evidence - the artifact that proves it operated.",
        "Assessment - how it is verified.",
        "POA&M - the tracked gap, where one exists.",
    ])
    d.p("A row with a policy and no evidence is a documented intention. A row with a "
        "technical control and no assessment is an untested assumption. Both are visible in "
        "this matrix by design.")

    rows = [
        ("Unique user identification", "HIPAA 164.312(a)(2)(i); NIST IA-2", "AGT-IAM-004",
         "Account provisioning", "Entra ID / application accounts; no shared accounts",
         "Access Review record", "Test: attempt shared login", "-"),
        ("Multi-factor authentication", "NIST IA-2(1); FedRAMP", "AGT-IAM-004",
         "Conditional Access configuration", "Entra ID CA policy, MFA required",
         "CA policy export", "Examine + test", "-"),
        ("Authorization enforcement", "HIPAA 164.312(a)(1); NIST AC-3", "AGT-ACP-003, AGT-IAM-004",
         "Endpoint role declaration", "Router-level dependency on every route",
         "DAST authz results", "Test: unauthenticated and under-privileged access", "-"),
        ("Automatic logoff", "HIPAA 164.312(a)(2)(iii)", "AGT-IAM-004",
         "Session configuration", "Token expiry + server-side revocation",
         "Configuration; code", "Test: expired token rejected", "-"),
        ("Encryption at rest", "HIPAA 164.312(a)(2)(iv); NIST SC-28", "AGT-CKM-009, AGT-DCP-006",
         "Azure configuration", "AES-256 storage and database encryption",
         "Azure configuration export", "Examine", "-"),
        ("Encryption in transit", "HIPAA 164.312(e)(1); NIST SC-8", "AGT-CKM-009",
         "TLS enforcement", "TLS 1.2+ minimum; database ssl=require",
         "DAST header results", "Test: TLS version negotiation", "-"),
        ("Key management", "NIST SC-12", "AGT-CKM-009",
         "Key Vault operation", "Managed identity resolution of secrets",
         "Key Vault reference list", "Examine", "P-04 DATABASE_URL literal"),
        ("Audit controls over ePHI", "HIPAA 164.312(b); NIST AU-12", "AGT-LMP-018",
         "PHI access auditing", "Router-level audit dependency",
         "Audit records", "Test: access produces a record", "-"),
        ("Audit record protection", "NIST AU-9", "AGT-LMP-018",
         "Log access restriction", "Access-controlled stores; separate Azure activity log",
         "Access configuration", "Examine", "P-05 no tamper-evidence"),
        ("Audit retention 6 years", "HIPAA 164.316(b)(2); NIST AU-11", "AGT-DRP-007",
         "Retention configuration", "Workspace and database retention settings",
         "Retention configuration", "Examine", "-"),
        ("Integrity of ePHI", "HIPAA 164.312(c)(1); NIST SI-7", "AGT-LMP-018",
         "Database constraints", "Referential integrity; transactional writes",
         "Schema", "Examine", "P-05 audit integrity"),
        ("Risk analysis", "HIPAA 164.308(a)(1)(ii)(A)", "AGT-RMP-023",
         "Annual assessment", "n/a - process control",
         "AGT-HIRA-025", "Examine", "-"),
        ("Risk management", "HIPAA 164.308(a)(1)(ii)(B)", "AGT-RMP-023",
         "Risk treatment tracking", "n/a - process control",
         "Risk register; POA&M", "Examine", "-"),
        ("Information system activity review", "HIPAA 164.308(a)(1)(ii)(D)", "AGT-LMP-018, AGT-CMS-024",
         "Weekly and monthly reviews", "Application Insights, audit queries",
         "Review records", "Examine", "-"),
        ("Workforce access management", "HIPAA 164.308(a)(4)", "AGT-IAM-004",
         "Quarterly access review", "Role assignment in the application and Azure",
         "Access Review record", "Examine", "-"),
        ("Security incident procedures", "HIPAA 164.308(a)(6); NIST IR-4", "AGT-IRP-022",
         "Incident handling", "Alerting; audit trail",
         "Incident Reports", "Interview + examine", "-"),
        ("Contingency plan", "HIPAA 164.308(a)(7)(i); NIST CP-2", "AGT-BCP-020",
         "Continuity planning", "n/a - process control",
         "AGT-BCP-020; exercise records", "Examine", "-"),
        ("Data backup plan", "HIPAA 164.308(a)(7)(ii)(A); NIST CP-9", "AGT-BKP-019",
         "Backup configuration and testing", "Azure automated backup, geo-redundant",
         "Backup Test Records", "Test: restore and verify", "-"),
        ("Disaster recovery plan", "HIPAA 164.308(a)(7)(ii)(B); NIST CP-10", "AGT-DRP-021",
         "DR procedures and testing", "Geo-restore capability",
         "DR Test Record", "Test: geo-restore", "-"),
        ("Evaluation", "HIPAA 164.308(a)(8); NIST CA-2", "AGT-CMS-024",
         "Annual assessment and continuous monitoring", "Automated scanning platform",
         "Scan reports; assessment report", "Examine", "P-10 no independent test"),
        ("Business associate assurances", "HIPAA 164.308(b)(1); NIST SA-9", "AGT-VRM-012",
         "Vendor assessment and contracting", "Contractual; technical restriction of data flow",
         "BAAs; Vendor Review records", "Examine", "P-01 two BAAs outstanding"),
        ("Device and media controls", "HIPAA 164.310(d)(1); NIST MP-2", "AGT-MPP-008, AGT-MDBYOD-010",
         "Media handling and disposal", "Endpoint encryption; MDM",
         "Disposal records; MDM inventory", "Examine", "-"),
        ("Configuration baselines", "NIST CM-2; SOC 2 CC7.1", "AGT-CfMP-016",
         "Baseline definition and drift detection", "Azure configuration; nightly assessment",
         "Drift reports", "Examine", "P-07, P-08 open settings"),
        ("Change control", "NIST CM-3; SOC 2 CC8.1", "AGT-ChMP-017",
         "RFC and CAB process", "Pipeline-gated deployment from a tag",
         "Change Records", "Examine", "-"),
        ("Component inventory", "NIST CM-8", "AGT-AMP-011",
         "Inventory maintenance", "SBOM generation; Resource Graph",
         "SBOM artifacts", "Examine", "-"),
        ("Vulnerability scanning", "NIST RA-5", "AGT-CMS-024, AGT-SSDLC-015",
         "Nightly scanning and weekly triage", "Automated scanning platform",
         "Scan reports", "Examine", "P-03 scanner coverage gap"),
        ("Flaw remediation", "NIST SI-2", "AGT-CMS-024",
         "Severity-based remediation windows", "Dependency upgrades; code fixes",
         "POA&M; commit history", "Examine", "-"),
        ("Secure development", "NIST SA-3, SA-11", "AGT-SSDLC-015",
         "Lifecycle security gates", "CI/CD gates; peer review",
         "Pipeline definitions; PR records", "Examine", "-"),
        ("Boundary protection", "NIST SC-7", "AGT-CfMP-016",
         "Network and host configuration", "HTTPS only; trusted-host; CORS",
         "Configuration; DAST", "Test", "Private endpoints roadmap"),
        ("System monitoring", "NIST SI-4", "AGT-LMP-018",
         "Alerting and review", "Azure Monitor; Application Insights",
         "Alert configuration; review records", "Examine", "P-06 no SIEM"),
        ("Individual rights", "HIPAA 164.524-528", "AGT-PPF-014",
         "Rights request handling", "Audit records support disclosure accounting",
         "Request records", "Examine", "-"),
        ("Breach notification", "HIPAA 164.400-414", "AGT-PPF-014, AGT-IRP-022",
         "Breach assessment and notification", "n/a - process control",
         "Incident Reports; notification records", "Examine", "-"),
        ("AI data governance", "NIST AI RMF; HIPAA 164.502(b)", "AGT-AIGOV-013",
         "Prompt minimization and human review", "Scrubbing; excerpting; fail-safe escalation",
         "Code; AI risk register", "Examine + test", "P-01, P-02"),
    ]
    d.h1("3. Traceability matrix")
    d.table(["Requirement", "Source", "Policy", "Procedure", "Technical control", "Evidence", "Assessment", "POA&M"],
            [list(r) for r in rows],
            widths=[0.9, 0.9, 0.8, 0.8, 1.0, 0.9, 0.8, 0.7])
    d.h1("4. Coverage summary")
    d.table(["Framework", "Requirements traced", "Fully evidenced", "Gap tracked in POA&M"],
            [["HIPAA Security Rule", "18", "15", "3"],
             ["HIPAA Privacy and Breach Rules", "2", "2", "0"],
             ["NIST 800-53 (families)", "20", "16", "4"],
             ["SOC 2 Trust Services Criteria", "9", "9", "0"],
             ["ISO 27001 Annex A", "14", "13", "1"],
             ["ISO 20000-1", "3", "3", "0"],
             ["CMMI Level 3", "7", "7", "0"],
             ["FedRAMP MODERATE", "20", "13", "7"]],
            widths=[2.0, 1.5, 1.3, 1.4])
    d.p("'Fully evidenced' means an artifact exists that demonstrates the control operated "
        "during the period, not that a policy describes it. The difference between those two "
        "counts is the honest measure of a compliance programme's maturity, and it is the "
        "reason this matrix separates the policy column from the evidence column.")
    d.compliance_mapping(
        [["NIST 800-53", "CA-2, PM-4", "Traceability supporting assessment and POA&M.", "Met"],
         ["HIPAA", "164.316(a)", "Documentation of policies and their implementation.", "Met"],
         ["SOC 2", "CC4.1, CC5.3", "Control monitoring and policy deployment.", "Met"],
         ["ISO 27001", "Clause 4.3, 9.2", "Scope and internal audit support.", "Met"],
         ["CMMI L3", "PPQA, MA", "Objective evaluation and measurement.", "Met"],
         ["FedRAMP", "CA-2", "Control traceability for authorization.", "Partial"]])
    d.related([["AGT-MCM-026", "Master Compliance Matrix", "Document-level framework mapping"],
               ["AGT-SSP-001", "System Security Plan", "Control implementation detail"],
               ["AGT-A-001", "Security Assessment Plan", "Assessment methods referenced"],
               ["AGT-T-012", "Evidence Collection Procedure", "Evidence artifact standards"]])
    d.closing()
    _emit(d, ROOT, "Control Traceability Matrix")


if __name__ == "__main__":
    print("  --- Tier 3 templates ---")
    t_access_review(); t_backup_test(); t_dr_test(); t_incident(); t_metrics()
    t_risk_register(); t_poam(); t_pentest(); t_vuln(); t_change(); t_vendor()
    t_evidence_proc()
    print("  --- Tier 4 assessment package ---")
    a_sap(); a_ato(); a_poam_proc(); a_conmon(); a_annual(); a_fedramp()
    print("  --- Zero Trust + Traceability ---")
    zta(); ctm()
    print(f"  total generated in this run: {len(BUILT)}")
