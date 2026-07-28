"""Tier 1 policies, batch 4: AGT-BKP-019 .. AGT-MCM-026."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402
from tier1_batch1 import _std_open, _finish, BUILT  # noqa: E402

ALL_DOCS = [
    ("AGT-EISP-001", "Enterprise Information Security Policy"),
    ("AGT-AUP-002", "Acceptable Use Policy"),
    ("AGT-ACP-003", "Access Control Policy"),
    ("AGT-IAM-004", "Identity and Access Management Policy"),
    ("AGT-RAP-005", "Remote Access Policy"),
    ("AGT-DCP-006", "Data Classification Policy"),
    ("AGT-DRP-007", "Data Retention Policy"),
    ("AGT-MPP-008", "Media Protection Policy"),
    ("AGT-CKM-009", "Cryptographic Key Management Policy"),
    ("AGT-MDBYOD-010", "Mobile Device and BYOD Policy"),
    ("AGT-AMP-011", "Asset Management Policy"),
    ("AGT-VRM-012", "Vendor Risk Management Policy"),
    ("AGT-AIGOV-013", "AI Governance Policy"),
    ("AGT-PPF-014", "Privacy Policy Framework"),
    ("AGT-SSDLC-015", "Secure Software Development Lifecycle"),
    ("AGT-CfMP-016", "Configuration Management Policy"),
    ("AGT-ChMP-017", "Change Management Policy"),
    ("AGT-LMP-018", "Logging and Monitoring Policy"),
    ("AGT-BKP-019", "Backup Policy"),
    ("AGT-BCP-020", "Business Continuity Policy"),
    ("AGT-DRP-021", "Disaster Recovery Policy"),
    ("AGT-IRP-022", "Incident Response Plan"),
    ("AGT-RMP-023", "Risk Management Plan"),
    ("AGT-CMS-024", "Continuous Monitoring Strategy"),
    ("AGT-HIRA-025", "HIPAA Organizational Risk Assessment"),
    ("AGT-MCM-026", "Master Compliance Matrix"),
]


def bkp():
    d = AGTDoc("AGT-BKP-019", "Backup Policy")
    _std_open(d, "This policy defines what AGT backs up, how often, where it is stored, how "
                 "long it is kept, and - most importantly - how restoration is proven to "
                 "work. A backup that has never been restored is a hypothesis, not a "
                 "control.")
    d.h1("3. Backup scope and schedule")
    d.table(["Asset", "Method", "Frequency", "Retention", "Location"],
            [["Azure PostgreSQL (prod)", "Automated platform backup with point-in-time restore",
              "Continuous", "35 days", "Geo-redundant, paired region"],
             ["Azure PostgreSQL (dev)", "Automated platform backup", "Continuous", "7 days", "Locally redundant"],
             ["Key Vault secrets", "Soft delete with purge protection", "Continuous", "90 days", "Vault region"],
             ["Source code", "Distributed version control with remote", "Every push", "Indefinite", "GitHub"],
             ["Infrastructure definitions", "Version-controlled templates", "Every change", "Indefinite", "GitHub"],
             ["Application configuration", "Exported and version-controlled", "On change", "Indefinite", "GitHub"],
             ["Audit logs", "Database backup plus log workspace retention", "Continuous", "6 years", "Azure"],
             ["Compliance documentation", "Version-controlled repository", "On change", "6 years", "GitHub"]],
            widths=[1.4, 1.7, 1.0, 0.9, 1.2])

    d.h1("4. Backup characteristics")
    d.bullets([
        "Backups are encrypted at rest by the platform using AES-256.",
        "Backups inherit the classification of their source; a backup of Restricted data is "
        "Restricted.",
        "Geo-redundant storage places a copy in the Azure paired region, which is what makes "
        "regional failure survivable.",
        "Backup access is restricted to the Security Officer and Engineering; restoration is "
        "a logged administrative action.",
    ])
    d.note("Geo-redundant backup on Azure Database for PostgreSQL Flexible Server can only "
           "be selected at server creation. It cannot be enabled afterwards. Any server "
           "created without it must be rebuilt or migrated to gain it, which makes this a "
           "provisioning-time decision with permanent consequences.")

    d.h1("5. Restoration testing")
    d.p("Testing is the substance of this policy. The schedule below is mandatory and the "
        "record is retained as compliance evidence.")
    d.table(["Test", "Frequency", "Success criteria", "Record"],
            [["Point-in-time restore to a scratch server", "Quarterly",
              "Restore completes; row counts and a sample of records match expectation",
              "Backup Test Record"],
             ["Geo-restore to the paired region", "Annually",
              "Restore completes within the RTO; application connects", "DR Test Record"],
             ["Key Vault secret recovery", "Annually", "Soft-deleted secret recovered intact", "Backup Test Record"],
             ["Full application rebuild from source", "Annually",
              "Application deploys from tag and serves traffic", "DR Test Record"]],
            widths=[1.8, 1.0, 2.2, 1.2])

    d.h1("6. Restoration procedure")
    d.numbered([
        "Declare the restoration need and obtain approval from the CEO or Security Officer.",
        "Identify the target recovery point; confirm it precedes the corrupting event.",
        "Restore to a NEW server rather than over the live one. Restoring in place destroys "
        "the forensic record and removes the option of comparing states.",
        "Verify integrity: row counts, referential integrity, and a sample of known records.",
        "Repoint the application only after verification.",
        "Record the restoration, including the recovery point achieved versus the objective.",
    ])

    d.h1("7. Backup failure handling")
    d.p("A failed backup is a Medium-severity incident. Two consecutive failures for the "
        "same asset escalate to High. Backup status is included in the monthly review under "
        "AGT-CMS-024, because a silent backup failure is only discovered at the worst "
        "possible moment.")

    d.roles([
        ["Engineering", "Configures backup; performs restoration tests; records results."],
        ["Security Officer", "Verifies schedule compliance; reviews test records."],
        ["CEO", "Approves production restoration."],
    ])
    _finish(d,
            [["NIST 800-53", "CP-9, CP-10, CP-9(1)", "System backup, recovery and reconstitution, testing for reliability.", "Met"],
             ["HIPAA", "164.308(a)(7)(ii)(A)", "Data backup plan.", "Met"],
             ["HIPAA", "164.310(d)(2)(iv)", "Data backup and storage before equipment movement.", "Met"],
             ["SOC 2", "A1.2, A1.3", "Environmental protections, backup, and recovery testing.", "Met"],
             ["ISO 27001", "A.8.13", "Information backup.", "Met"],
             ["ISO 20000-1", "Clause 8.7.2", "Service continuity and availability management.", "Met"],
             ["FedRAMP", "CP-9(1)", "Testing backup information to verify reliability.", "Met"]],
            [["AGT-DRP-021", "Disaster Recovery Policy", "Consumes these backups for recovery"],
             ["AGT-BCP-020", "Business Continuity Policy", "Business context for recovery objectives"],
             ["AGT-DRP-007", "Data Retention Policy", "Retention interaction with backup expiry"],
             ["AGT-CfMP-016", "Configuration Management Policy", "Backup settings are baseline values"]])


def bcp():
    d = AGTDoc("AGT-BCP-020", "Business Continuity Policy")
    _std_open(d, "This policy establishes how AGT continues to deliver its critical "
                 "services during a disruption. It addresses the business dimension - "
                 "people, communication, and process - while AGT-DRP-021 addresses the "
                 "technical recovery of systems.")
    d.h1("3. Critical business functions")
    d.table(["Function", "Impact of loss", "Max tolerable outage", "Dependency"],
            [["TEFCA validation and review delivery", "Contractual breach; customer operations halted", "8 hours", "Azure App Service, PostgreSQL"],
             ["Case management documentation", "Clinical documentation delayed", "8 hours", "App Service, AI providers"],
             ["FCC bulletin delivery", "Missed daily client commitment", "24 hours", "App Service, data feeds, email"],
             ["Customer authentication", "Total loss of access", "4 hours", "Entra ID, App Service"],
             ["Audit and compliance evidence", "Regulatory exposure", "72 hours", "PostgreSQL, log workspace"]],
            widths=[1.7, 1.9, 1.2, 1.4])

    d.h1("4. Business impact analysis summary")
    d.p("AGT is a small, distributed organization whose production capability is almost "
        "entirely cloud-hosted. This shapes the continuity profile in two ways worth stating "
        "explicitly. Facility loss is close to irrelevant - there is no data centre and no "
        "office that production depends on. Key-person loss is the dominant risk, because "
        "operational knowledge is concentrated in a small number of people.")
    d.table(["Threat", "Likelihood", "Impact", "Primary mitigation"],
            [["Azure regional outage", "Low", "High", "Geo-redundant backup; documented recovery to the paired region"],
             ["Key person unavailable", "Medium", "High", "Documented runbooks; no single-person-only procedure"],
             ["Third-party AI provider outage", "Medium", "Medium", "Graceful degradation; features fail visibly, not silently"],
             ["Email provider outage", "Low", "Medium", "Bulletin delivery deferred; content preserved"],
             ["Ransomware or destructive compromise", "Low", "High", "Immutable platform backups; AGT-IRP-022"],
             ["Loss of internet connectivity for staff", "Medium", "Low", "Cellular fallback; work is location-independent"]],
            widths=[1.9, 1.0, 0.9, 2.4])

    d.h1("5. Continuity strategy")
    d.bullets([
        "Work is location-independent by default, so a facility event does not interrupt "
        "operations.",
        "Every operational procedure that matters is written down in a runbook, so that "
        "continuity does not depend on a specific individual being reachable.",
        "Degradation is preferred to failure: where an external dependency is unavailable, "
        "the platform reports the degradation rather than presenting stale data as current.",
        "Customer commitments with fixed daily deadlines are identified so that a "
        "disruption's contractual consequence is known before it is negotiated.",
    ])

    d.h1("6. Emergency contacts and escalation")
    d.table(["Role", "Name", "Responsibility during disruption"],
            [["Chief Executive Officer", "Imran Siddiqui", "Declares an event; customer and regulator communication"],
             ["Security Officer", "Assigned", "Assesses security implications; leads if the cause is a compromise"],
             ["Engineering lead", "Assigned", "Executes technical recovery per AGT-DRP-021"],
             ["Privacy Officer", "Assigned", "Determines notification obligations if data is affected"]],
            widths=[1.6, 1.4, 3.2])
    d.note("Contact details are maintained separately from this document and are available "
           "offline, because a continuity plan reachable only through the system that is "
           "down is not a continuity plan.")

    d.h1("7. Communication procedures")
    d.numbered([
        "The CEO or delegate declares the event and opens a communication channel "
        "independent of AGT systems.",
        "Internal notification within 1 hour of declaration.",
        "Customer notification per contract; for federal customers within 24 hours.",
        "Status updates at a stated cadence, even when the update is that there is no "
        "change - silence is interpreted as loss of control.",
        "A single spokesperson handles external communication to prevent inconsistency.",
        "Closure notice with a summary of impact and next steps.",
    ])

    d.h1("8. Recovery checklist")
    d.numbered([
        "Confirm the nature of the disruption: availability, integrity, or confidentiality.",
        "If a compromise is suspected, invoke AGT-IRP-022 before restoring, so evidence is "
        "preserved.",
        "Determine the affected functions from section 3 and their tolerable outage.",
        "Invoke AGT-DRP-021 for technical recovery.",
        "Verify data integrity before declaring service restored.",
        "Notify stakeholders of restoration.",
        "Conduct a post-event review within 10 business days.",
    ])

    d.h1("9. Testing and maintenance")
    d.table(["Activity", "Frequency", "Participants"],
            [["Tabletop exercise of a regional outage", "Annually", "CEO, Security Officer, Engineering"],
             ["Contact list verification", "Semi-annually", "Security Officer"],
             ["Runbook accuracy review", "Annually", "Engineering"],
             ["Plan review and update", "Annually or after any real event", "CEO"]],
            widths=[2.6, 1.4, 2.2])

    d.roles([
        ["CEO", "Declares events; owns external communication; approves plan."],
        ["Security Officer", "Assesses security dimension; maintains contact list."],
        ["Engineering", "Executes recovery; maintains runbooks."],
        ["All personnel", "Know how to reach leadership through the out-of-band channel."],
    ])
    _finish(d,
            [["NIST 800-53", "CP-1, CP-2, CP-3, CP-4, CP-8", "Contingency planning, training, testing, telecommunications services.", "Met"],
             ["HIPAA", "164.308(a)(7)(i)", "Contingency plan.", "Met"],
             ["HIPAA", "164.308(a)(7)(ii)(C)", "Emergency mode operation plan.", "Met"],
             ["SOC 2", "A1.2, CC7.5", "Recovery of operations and incident recovery.", "Met"],
             ["ISO 27001", "A.5.29, A.5.30", "Information security during disruption; ICT readiness for continuity.", "Met"],
             ["ISO 20000-1", "Clause 8.7.2", "Service continuity management.", "Met"],
             ["ISO 22301", "Clause 8", "Business continuity operation and exercising.", "Partial"]],
            [["AGT-DRP-021", "Disaster Recovery Policy", "Technical recovery procedures"],
             ["AGT-BKP-019", "Backup Policy", "Recovery sources"],
             ["AGT-IRP-022", "Incident Response Plan", "Invoked when the cause is a compromise"],
             ["AGT-VRM-012", "Vendor Risk Management Policy", "Third-party dependency risk"]])


def drp21():
    d = AGTDoc("AGT-DRP-021", "Disaster Recovery Policy")
    _std_open(d, "This policy defines the technical procedures, objectives, and testing "
                 "regime for recovering the DocuAction TEFCA ARC platform after a "
                 "destructive event. It is the technical counterpart to AGT-BCP-020.")
    d.h1("3. Recovery objectives")
    d.table(["System", "RTO", "RPO", "Basis"],
            [["Backend API (App Service)", "4 hours", "Not applicable (stateless)", "Redeploy from tagged artifact"],
             ["Database (PostgreSQL)", "8 hours", "15 minutes", "Point-in-time restore granularity"],
             ["Frontend (Static Web Apps)", "2 hours", "Not applicable (rebuilt from source)", "Rebuild and redeploy"],
             ["Key Vault", "2 hours", "0", "Soft delete recovery"],
             ["Identity (Entra ID)", "Inherited from Microsoft", "Inherited", "Microsoft SLA"],
             ["Audit and compliance data", "24 hours", "15 minutes", "Included in database restore"]],
            widths=[1.9, 1.0, 1.5, 1.8])
    d.p("RTO and RPO are objectives derived from tolerable outage in AGT-BCP-020, not "
        "measurements. The annual geo-restore test is what converts them from a target into "
        "a verified capability, and the test record states the achieved figures alongside "
        "the objectives.")

    d.h1("4. Disaster scenarios and procedures")
    d.h2("4.1 Database corruption or accidental destructive change")
    d.numbered([
        "Stop writes to the affected database if the corruption is ongoing.",
        "Identify a recovery point preceding the corrupting event.",
        "Restore to a NEW server. Never restore over the live server - it eliminates the "
        "ability to compare states and destroys evidence.",
        "Verify integrity on the restored copy before repointing.",
        "Update the application connection to the restored server.",
        "Confirm the application serves correctly, then decommission the corrupted server "
        "after any forensic need is satisfied.",
    ])
    d.h2("4.2 Regional outage")
    d.numbered([
        "Confirm the scope of the outage through the Azure status page and the portal.",
        "Geo-restore the database to the paired region.",
        "Deploy the backend from the current release tag to an App Service in the recovery "
        "region.",
        "Deploy the frontend build to a Static Web App in the recovery region.",
        "Update DNS to the recovery endpoints; ensure the allowed-hosts configuration "
        "includes the new hostnames, or every request including health checks will be "
        "rejected with a 400.",
        "Verify end to end, then notify stakeholders.",
    ])
    d.h2("4.3 Application compromise")
    d.numbered([
        "Invoke AGT-IRP-022. Containment and evidence preservation precede recovery.",
        "Rotate all credentials and the token signing key. Rotating the signing key "
        "invalidates every issued token, which is the intent.",
        "Rebuild from a known-good tagged commit rather than repairing the running system.",
        "Restore data from a recovery point preceding the compromise.",
        "Verify integrity before returning to service.",
    ])
    d.h2("4.4 Accidental deletion of a cloud resource")
    d.numbered([
        "Check for soft-delete recovery, which exists for Key Vault and some resources.",
        "Recreate from the infrastructure definition in source control.",
        "Reapply configuration from the documented baseline in AGT-CfMP-016.",
        "Note that geo-redundant backup cannot be enabled after server creation; a "
        "recreated database server must have it selected at creation or the capability is "
        "permanently lost for that server.",
    ])

    d.h1("5. Dependencies for recovery")
    d.table(["Dependency", "Why it is needed", "Availability during a disaster"],
            [["Source repository (GitHub)", "Application and infrastructure definitions", "External to Azure; independent failure domain"],
             ["Release tags", "Identify the exact deployable version", "In the repository"],
             ["Azure credentials", "Perform recovery operations", "Held by named individuals; MFA required"],
             ["Runbooks", "Step-by-step procedures", "In the repository and available offline"],
             ["Contact list", "Coordination", "Maintained offline"]],
            widths=[1.6, 2.2, 2.4])

    d.h1("6. DR testing")
    d.table(["Test", "Frequency", "Scope", "Record"],
            [["Database point-in-time restore", "Quarterly", "Restore to scratch; verify integrity", "Backup Test Record"],
             ["Geo-restore to paired region", "Annually", "Full database recovery in the paired region", "DR Test Record"],
             ["Application rebuild from tag", "Annually", "Deploy from source to a clean App Service", "DR Test Record"],
             ["Full tabletop exercise", "Annually", "Regional outage walkthrough", "Exercise minutes"]],
            widths=[1.9, 1.0, 2.2, 1.1])
    d.p("Each test records the achieved recovery time against the objective. Where the "
        "achieved time exceeds the objective, either the procedure is improved or the "
        "objective is revised to a figure AGT can actually meet. Publishing an RTO that "
        "testing has never achieved is worse than publishing a longer, honest one.")

    d.roles([
        ["Engineering", "Executes recovery; owns and tests runbooks."],
        ["Security Officer", "Confirms no compromise before restoration; verifies integrity."],
        ["CEO", "Declares a disaster; authorizes recovery actions and communications."],
    ])
    _finish(d,
            [["NIST 800-53", "CP-2, CP-4, CP-6, CP-7, CP-10", "Contingency plan, testing, alternate storage and processing sites, reconstitution.", "Met"],
             ["HIPAA", "164.308(a)(7)(ii)(B)", "Disaster recovery plan.", "Met"],
             ["HIPAA", "164.308(a)(7)(ii)(D)", "Testing and revision procedures.", "Met"],
             ["SOC 2", "A1.2, A1.3", "Recovery infrastructure and testing.", "Met"],
             ["ISO 27001", "A.5.29, A.5.30", "ICT readiness for business continuity.", "Met"],
             ["FedRAMP", "CP-4(1), CP-7", "Coordinated testing and alternate processing capability.", "Partial - alternate site is procedural, not pre-provisioned."]],
            [["AGT-BCP-020", "Business Continuity Policy", "Business objectives driving RTO and RPO"],
             ["AGT-BKP-019", "Backup Policy", "Recovery sources and test schedule"],
             ["AGT-IRP-022", "Incident Response Plan", "Precedes recovery when compromise is suspected"],
             ["AGT-CfMP-016", "Configuration Management Policy", "Baselines to restore to"]])


def irp():
    d = AGTDoc("AGT-IRP-022", "Incident Response Plan")
    _std_open(d, "This plan defines how AGT detects, contains, eradicates, recovers from, "
                 "and learns from security incidents. It follows the NIST SP 800-61 "
                 "lifecycle and carries the notification obligations that apply to AGT as a "
                 "HIPAA business associate and federal contractor.")
    d.h1("3. Severity matrix")
    d.table(["Severity", "Definition", "Examples", "Response time", "Escalation"],
            [["Critical", "Confirmed breach of PHI, or total loss of service",
              "PHI exfiltration; ransomware; production compromise", "Immediate", "CEO + Privacy Officer immediately"],
             ["High", "Probable compromise or significant exposure",
              "Credential theft; privilege escalation; exposed secret", "1 hour", "CEO + Security Officer"],
             ["Medium", "Security event with limited or contained impact",
              "Failed intrusion attempt; malware on one endpoint", "4 hours", "Security Officer"],
             ["Low", "Policy violation or anomaly with no evident impact",
              "Misconfiguration found; single lost device, encrypted", "1 business day", "Security Officer"]],
            widths=[0.9, 1.7, 1.8, 0.9, 1.4])

    d.h1("4. Phase 1 - Preparation")
    d.bullets([
        "This plan and the runbooks it references are maintained and available offline.",
        "Contact list is verified semi-annually.",
        "Logging and alerting per AGT-LMP-018 provide the detection capability this plan "
        "depends on.",
        "An annual tabletop exercise is conducted, and its findings amend this plan.",
    ])

    d.h1("5. Phase 2 - Identification")
    d.numbered([
        "Receive the signal: an alert, a report from personnel, a vendor notification, or a "
        "customer report.",
        "Record the time of discovery. For HIPAA, discovery starts the notification clock, "
        "so this timestamp is a legal fact, not an administrative detail.",
        "Assign a severity from section 3.",
        "Open an incident record and assign an incident lead.",
        "Preserve evidence before changing anything: capture logs, take a snapshot, and "
        "avoid restarting the affected system if a restart would clear volatile state.",
    ])

    d.h1("6. Phase 3 - Containment")
    d.table(["Scenario", "Immediate containment"],
            [["Credential theft", "Disable the account, revoke all sessions, rotate the token signing key if scope is unclear"],
             ["Exposed secret in code or logs", "Rotate the secret first, then remove it from the source; removal alone does not un-expose it"],
             ["Ransomware", "Isolate affected systems; do not restore until the entry vector is identified"],
             ["Application compromise", "Take the instance out of rotation; preserve it for analysis; serve from a clean rebuild"],
             ["Data exfiltration", "Block the egress path; preserve network and application logs"],
             ["Malicious insider", "Disable access without notice to the individual; involve the CEO and counsel immediately"]],
            widths=[1.7, 4.5])
    d.p("Containment for a leaked credential is rotation, not deletion of the commit. A "
        "secret that has been pushed to a remote must be assumed captured; removing it from "
        "history changes what is visible, not what was taken.")

    d.h1("7. Phase 4 - Eradication and recovery")
    d.numbered([
        "Identify and close the root cause, not only the symptom.",
        "Rebuild rather than repair where the integrity of a system is in doubt.",
        "Restore data from a recovery point preceding the compromise (AGT-DRP-021).",
        "Rotate every credential the attacker could plausibly have reached.",
        "Verify integrity and confirm the attacker no longer has access before restoring "
        "service.",
        "Increase monitoring on the affected systems for at least 30 days.",
    ])

    d.h1("8. Notification obligations")
    d.table(["Recipient", "Trigger", "Deadline", "Owner"],
            [["Government contracting officer (COR)", "Any incident affecting contract performance or data", "24 hours", "CEO"],
             ["Covered entity customer", "Breach or suspected breach of their PHI", "24 hours per BAA", "CEO"],
             ["Affected individuals", "Confirmed breach of unsecured PHI", "60 days from discovery", "Covered entity, supported by AGT"],
             ["HHS OCR", "Breach affecting 500 or more individuals", "60 days from discovery", "Covered entity"],
             ["HHS OCR (small breaches)", "Fewer than 500 individuals", "Annually, within 60 days of year end", "Covered entity"],
             ["Prominent media", "Breach affecting 500+ residents of one state", "60 days", "Covered entity"],
             ["Cyber insurance carrier", "Per policy terms", "Per policy", "CEO"],
             ["Law enforcement", "Criminal activity suspected", "As advised by counsel", "CEO"]],
            widths=[1.7, 1.7, 1.2, 1.6])
    d.p("Encryption meeting HHS guidance is a safe harbour: loss of properly encrypted PHI "
        "is not a breach of unsecured PHI and does not trigger these deadlines. Establishing "
        "whether the data was encrypted is therefore one of the first determinations in any "
        "incident involving data loss.")

    d.h1("9. Scenario playbooks")
    d.h2("9.1 Ransomware")
    d.numbered([
        "Isolate affected systems from the network immediately.",
        "Do not pay, and do not communicate with the actor, without CEO and counsel "
        "involvement.",
        "Identify the entry vector before restoring; restoring into an unremediated "
        "environment reinfects.",
        "Assess whether PHI was accessed or exfiltrated - HHS guidance presumes a breach in "
        "a ransomware event unless a low probability of compromise is demonstrated.",
        "Restore from platform backups, which are outside the compromised system's control.",
        "Notify per section 8.",
    ])
    d.h2("9.2 Credential theft")
    d.numbered([
        "Disable the account and revoke all active sessions.",
        "Determine what the credential could reach and over what period.",
        "Review audit logs for actions taken with the credential.",
        "Rotate the token signing key if the blast radius is not precisely bounded.",
        "Require re-enrolment of MFA for the affected identity.",
        "Assess for PHI access; if PHI was reachable, treat as a probable breach until "
        "evidence shows otherwise.",
    ])
    d.h2("9.3 Exposed secret")
    d.numbered([
        "Rotate the secret immediately - this is containment.",
        "Determine the exposure window and who could have accessed it.",
        "Review logs for use of the secret from unexpected sources.",
        "Remove it from source and add detection to prevent recurrence.",
        "Record as an incident even if no misuse is found; the exposure itself is the event.",
    ])

    d.h1("10. Phase 5 - Lessons learned")
    d.p("A post-incident review occurs within 10 business days of closure for Critical and "
        "High incidents. It produces: a timeline, the root cause, what detection worked and "
        "what did not, and corrective actions with owners and dates. Corrective actions "
        "enter the POA&M. The review is blameless in tone and specific in substance - the "
        "purpose is to change the system, not to assign fault.")

    d.h1("11. Incident record retention")
    d.p("Incident records are retained for six years per AGT-DRP-007.")

    d.roles([
        ["CEO", "Declares Critical incidents; owns all external notification."],
        ["Security Officer", "Incident lead for technical response; preserves evidence."],
        ["Privacy Officer", "Determines breach status and notification obligations."],
        ["Engineering", "Executes containment, eradication, and recovery."],
        ["All personnel", "Report suspected incidents immediately; do not investigate alone."],
    ])
    _finish(d,
            [["NIST 800-53", "IR-1 through IR-8", "Incident response policy, training, testing, handling, monitoring, reporting, assistance, plan.", "Met"],
             ["NIST 800-61", "Rev. 2", "Incident handling lifecycle adopted.", "Met"],
             ["HIPAA", "164.308(a)(6)", "Security incident procedures.", "Met"],
             ["HIPAA", "164.410", "Business associate breach notification to the covered entity.", "Met"],
             ["HIPAA", "164.404-408", "Notification to individuals, media, and HHS.", "Met"],
             ["SOC 2", "CC7.3, CC7.4, CC7.5", "Evaluation, response, and recovery from incidents.", "Met"],
             ["ISO 27001", "A.5.24 through A.5.28", "Incident management planning, assessment, response, learning, evidence.", "Met"],
             ["FedRAMP", "IR-4(1), IR-6(1)", "Automated incident handling and reporting.", "Partial"]],
            [["AGT-BCP-020", "Business Continuity Policy", "Business coordination during incidents"],
             ["AGT-DRP-021", "Disaster Recovery Policy", "Technical recovery after eradication"],
             ["AGT-PPF-014", "Privacy Policy Framework", "Breach determination and privacy obligations"],
             ["AGT-LMP-018", "Logging and Monitoring Policy", "Detection and evidence"],
             ["AGT-VRM-012", "Vendor Risk Management Policy", "Vendor-originated incidents"]])


def rmp():
    d = AGTDoc("AGT-RMP-023", "Risk Management Plan")
    _std_open(d, "This plan establishes how AGT identifies, analyses, treats, and monitors "
                 "information security risk. It follows NIST SP 800-30 and feeds the "
                 "continuous monitoring programme in AGT-CMS-024.")
    d.h1("3. Methodology")
    d.p("Risk is assessed as the combination of the likelihood that a threat exploits a "
        "vulnerability and the impact if it does. AGT uses a qualitative five-by-five scale "
        "because the organization does not have the loss history that would make "
        "quantitative estimation honest.")
    d.table(["Likelihood", "Definition"],
            [["Very High", "Expected to occur; already observed in this environment"],
             ["High", "Likely within a year"],
             ["Moderate", "Possible within a year"],
             ["Low", "Unlikely but credible"],
             ["Very Low", "Requires an improbable combination of conditions"]],
            widths=[1.3, 4.9])
    d.table(["Impact", "Definition"],
            [["Very High", "PHI breach, contract termination, or regulatory enforcement"],
             ["High", "Significant service loss, material financial or reputational damage"],
             ["Moderate", "Limited service degradation; recoverable financial loss"],
             ["Low", "Minor operational inconvenience"],
             ["Very Low", "Negligible"]],
            widths=[1.3, 4.9])

    d.h1("4. Risk register - current")
    d.table(["ID", "Risk", "L", "I", "Rating", "Treatment", "Owner"],
            [["R-01", "PHI reaches an AI provider with no BAA in place", "Moderate", "Very High", "High",
              "Mitigate - execute BAAs; block PHI paths meanwhile", "CEO"],
             ["R-02", "Database password readable by any Reader on the resource group", "Low", "High", "Moderate",
              "Mitigate - migrate DATABASE_URL to Key Vault", "Security Officer"],
             ["R-03", "Audit log has no tamper-evidence", "Low", "High", "Moderate",
              "Mitigate - implement hash chaining", "Engineering"],
             ["R-04", "One SAST scanner has never run; coverage gap in every score", "High", "Moderate", "High",
              "Mitigate - run semgrep on a Linux runner", "Engineering"],
             ["R-05", "Key-person dependency for operational knowledge", "Moderate", "High", "High",
              "Mitigate - runbooks; cross-training", "CEO"],
             ["R-06", "Single cloud provider concentration", "Low", "Very High", "Moderate",
              "Accept - documented; geo-redundancy within the provider", "CEO"],
             ["R-07", "Unauthenticated endpoints disclose operational data", "Low", "Moderate", "Low",
              "Mitigated - authorization added to sensitive endpoints", "Engineering"],
             ["R-08", "Dead code with no authorization could be mounted by accident", "Low", "High", "Moderate",
              "Mitigate - suppressions expire so the finding returns", "Engineering"],
             ["R-09", "FTP deployment path bypasses pipeline verification", "Low", "Moderate", "Low",
              "Mitigate - disable FTP", "Engineering"],
             ["R-10", "No SIEM correlation; detection depends on manual review", "Moderate", "Moderate", "Moderate",
              "Mitigate - centralize logs, then evaluate Sentinel", "Security Officer"]],
            widths=[0.5, 2.0, 0.7, 0.7, 0.7, 1.6, 0.8])

    d.h1("5. Risk treatment options")
    d.bullets([
        "Mitigate - implement a control that reduces likelihood or impact.",
        "Transfer - shift consequence by contract or insurance. Note that regulatory "
        "liability under HIPAA cannot be transferred; only financial consequence can.",
        "Avoid - stop the activity that creates the risk.",
        "Accept - document the rationale, the accepting authority, and a review date. An "
        "acceptance without a review date is an abandonment.",
    ])

    d.h1("6. Risk acceptance criteria")
    d.table(["Rating", "Acceptance authority", "Maximum acceptance period"],
            [["Critical", "Not acceptable - must be treated", "n/a"],
             ["High", "CEO", "90 days, then re-evaluated"],
             ["Moderate", "CEO or Security Officer", "180 days"],
             ["Low", "Security Officer", "Annual review"]],
            widths=[1.2, 2.5, 2.5])

    d.h1("7. Continuous risk identification")
    d.p("Risks enter the register from: security scanning, incidents and near misses, "
        "vendor assessments, audit and assessment findings, change impact analysis, and the "
        "annual HIPAA risk assessment (AGT-HIRA-025). A finding that is suppressed in a "
        "scanning tool is not thereby closed as a risk; suppression with an expiry is a "
        "deferral, and the register records it as such.")

    d.h1("8. Review cadence")
    d.table(["Activity", "Frequency", "Owner"],
            [["Register review and re-rating", "Quarterly", "Security Officer"],
             ["Executive risk review", "Quarterly", "CEO"],
             ["Full risk assessment refresh", "Annually", "Security Officer"],
             ["Post-incident risk update", "Within 10 days of closure", "Security Officer"]],
            widths=[2.4, 1.4, 2.4])

    d.roles([
        ["CEO", "Accepts High risk; owns the executive review."],
        ["Security Officer", "Maintains the register; performs assessment; tracks treatment."],
        ["Engineering", "Implements technical treatments."],
        ["Privacy Officer", "Contributes privacy risk."],
    ])
    _finish(d,
            [["NIST 800-53", "RA-1, RA-3, RA-5, RA-7, PM-9", "Risk assessment policy, assessment, scanning, risk response, risk management strategy.", "Met"],
             ["NIST 800-30", "Rev. 1", "Risk assessment methodology adopted.", "Met"],
             ["HIPAA", "164.308(a)(1)(ii)(A)", "Risk analysis.", "Met"],
             ["HIPAA", "164.308(a)(1)(ii)(B)", "Risk management.", "Met"],
             ["SOC 2", "CC3.1 through CC3.4", "Objectives, risk identification, fraud risk, change assessment.", "Met"],
             ["ISO 27001", "Clause 6.1, 8.2, 8.3", "Risk assessment and treatment.", "Met"],
             ["CMMI L3", "RSKM", "Risk management process area.", "Met"],
             ["FedRAMP", "RA-5(5), PM-9", "Privileged scanning and organizational risk strategy.", "Met"]],
            [["AGT-HIRA-025", "HIPAA Organizational Risk Assessment", "Feeds the register annually"],
             ["AGT-CMS-024", "Continuous Monitoring Strategy", "Monitors treatment progress"],
             ["AGT-IRP-022", "Incident Response Plan", "Incidents generate new risks"],
             ["AGT-VRM-012", "Vendor Risk Management Policy", "Vendor risk source"]])


def cms():
    d = AGTDoc("AGT-CMS-024", "Continuous Monitoring Strategy")
    _std_open(d, "This strategy defines the ongoing activities by which AGT maintains "
                 "awareness of its security posture between formal assessments. The purpose "
                 "is to ensure that authorization remains justified over time rather than "
                 "being a statement about one day in the past.")
    d.h1("3. Monitoring cadence")
    d.table(["Activity", "Frequency", "Owner", "Output"],
            [["Automated full security scan", "Nightly", "Automated", "Scan report, SBOM, dashboard"],
             ["Scan result triage", "Weekly", "Security Officer", "Triage record; new POA&M items"],
             ["Security event log review", "Weekly", "Security Officer", "Review record"],
             ["Privileged action review", "Monthly", "Security Officer", "Review record"],
             ["Backup status verification", "Monthly", "Engineering", "Backup status record"],
             ["Metrics reporting to the CEO", "Monthly", "Security Officer", "Metrics summary"],
             ["Access review (all accounts)", "Quarterly", "Security Officer", "Access Review record"],
             ["Configuration drift review", "Quarterly", "Engineering", "Drift report"],
             ["Risk register review", "Quarterly", "Security Officer", "Updated register"],
             ["Backup restoration test", "Quarterly", "Engineering", "Backup Test Record"],
             ["Vendor reassessment (Critical/High)", "Annually", "Security Officer", "Vendor Review record"],
             ["HIPAA risk assessment refresh", "Annually", "Security Officer", "AGT-HIRA-025 update"],
             ["Disaster recovery test", "Annually", "Engineering", "DR Test Record"],
             ["Policy review", "Annually", "CEO", "Version history update"],
             ["Penetration test", "Annually", "Third party", "Penetration Test Report"]],
            widths=[2.1, 1.1, 1.3, 1.7])

    d.h1("4. Security metrics")
    d.table(["Metric", "Definition", "Target", "Current"],
            [["Security score", "Density-normalized score from the scanning platform", "70 or above", "58.7"],
             ["Critical findings", "Unsuppressed Critical findings", "0", "0"],
             ["High findings", "Unsuppressed High findings", "Trending down", "39"],
             ["Scanner coverage", "Scanners producing results / scanners configured", "100%", "8 of 9"],
             ["Mean time to triage", "Time from scan to disposition", "7 days", "Tracked"],
             ["Mean time to remediate (Critical)", "Time from confirmation to fix", "7 days", "Tracked"],
             ["Access review completion", "Reviews completed on schedule", "100%", "Tracked"],
             ["Backup test success rate", "Successful restores / attempted", "100%", "Tracked"],
             ["Open POA&M items past due", "Items beyond their target date", "0", "Tracked"]],
            widths=[1.7, 2.2, 1.1, 1.2])
    d.p("The security score is reported with its coverage caveat attached. One scanner has "
        "never produced a result on this codebase, so every score is an upper bound on what "
        "a complete scan would report. Presenting the number without that qualification "
        "would misrepresent the posture.")

    d.h1("5. Suppression governance")
    d.p("Suppressing a finding is a monitored action, not an administrative convenience.")
    d.bullets([
        "Every suppression records a reason, an author, and an expiry date.",
        "Permanent suppression is not permitted for any finding above Low severity.",
        "Suppressions are reviewed quarterly; an expiring suppression returns the finding to "
        "the queue for re-evaluation.",
        "Suppression counts are reported alongside finding counts, because a score improved "
        "by suppression and a score improved by remediation are different facts and must "
        "not be presented as the same one.",
    ])

    d.h1("6. POA&M integration")
    d.p("Findings that are not remediated within their severity's target window become POA&M "
        "items with an owner, a milestone, and a completion date. The POA&M is reviewed "
        "monthly by the Security Officer and quarterly by the CEO.")
    d.table(["Severity", "Remediation target", "POA&M required if exceeded"],
            [["Critical", "7 days", "Immediately"],
             ["High", "30 days", "Yes"],
             ["Medium", "90 days", "Yes"],
             ["Low", "Next major release", "Optional"]],
            widths=[1.3, 2.2, 2.7])

    d.h1("7. Executive reporting")
    d.p("A monthly summary to the CEO covers: posture metrics against target, new and closed "
        "findings, incidents, POA&M status, and any control that is not operating as "
        "designed. The report states what is not working as prominently as what is; a "
        "monitoring report that only carries good news has stopped being a monitoring "
        "report.")

    d.roles([
        ["Security Officer", "Operates the programme; produces reports; governs suppressions."],
        ["Engineering", "Performs technical reviews and remediation."],
        ["CEO", "Reviews quarterly; accepts residual risk; funds remediation."],
    ])
    _finish(d,
            [["NIST 800-53", "CA-7, CA-7(1), PM-14, RA-5", "Continuous monitoring, independent assessment, testing programme, scanning.", "Met"],
             ["NIST 800-137", "ISCM", "Information security continuous monitoring strategy.", "Met"],
             ["HIPAA", "164.308(a)(1)(ii)(D)", "Information system activity review.", "Met"],
             ["HIPAA", "164.308(a)(8)", "Periodic technical and non-technical evaluation.", "Met"],
             ["SOC 2", "CC4.1, CC4.2", "Monitoring of controls and communication of deficiencies.", "Met"],
             ["ISO 27001", "Clause 9.1, 9.2, 9.3", "Monitoring and measurement, internal audit, management review.", "Met"],
             ["CMMI L3", "MA, PPQA", "Measurement and analysis; process and product quality assurance.", "Met"],
             ["FedRAMP", "CA-7", "Continuous monitoring consistent with the MODERATE baseline.", "Partial"]],
            [["AGT-RMP-023", "Risk Management Plan", "Risk register maintained through this programme"],
             ["AGT-LMP-018", "Logging and Monitoring Policy", "Provides the monitoring data"],
             ["AGT-SSDLC-015", "Secure SDLC", "Scanning integrated into the pipeline"],
             ["AGT-MCM-026", "Master Compliance Matrix", "Control coverage tracked over time"]])


def hira():
    d = AGTDoc("AGT-HIRA-025", "HIPAA Organizational Risk Assessment")
    _std_open(d, "This assessment satisfies the HIPAA Security Rule requirement at 45 CFR "
                 "164.308(a)(1)(ii)(A) to conduct an accurate and thorough assessment of "
                 "the potential risks and vulnerabilities to the confidentiality, "
                 "integrity, and availability of electronic protected health information "
                 "held by AGT.")
    d.h1("3. Scope and ePHI inventory")
    d.table(["System or flow", "ePHI involved", "State", "Safeguard"],
            [["Case-management note generation", "Clinical text supplied by users", "In transit and in process",
              "TLS; router-level authentication and audit"],
             ["Audio transcription pipeline", "Recorded clinical audio and transcripts", "In transit to a third party",
              "TLS; no pre-transmission redaction - see finding F-01"],
             ["Azure PostgreSQL", "Persisted clinical records where a customer stores them", "At rest",
              "Encryption at rest; ssl=require in transit"],
             ["Audit log", "Access metadata only; route templates, never record identifiers", "At rest",
              "Deliberately excludes PHI"],
             ["Application logs", "None permitted", "n/a", "Prohibited by AGT-LMP-018"]],
            widths=[1.6, 1.9, 1.3, 1.4])
    d.note("Five of the twenty-two case-management endpoints are unimplemented stubs that "
           "return an empty result, including the two whose names most suggest bulk PHI "
           "reads. Scoping this assessment against endpoint names rather than behaviour "
           "would have overstated the current exposure and understated the future one.")

    d.h1("4. Administrative safeguards")
    d.table(["Standard", "Reference", "Implementation", "Status"],
            [["Security management process", "164.308(a)(1)", "Risk analysis, risk management, sanction policy, activity review", "Implemented"],
             ["Assigned security responsibility", "164.308(a)(2)", "Security Officer designated", "Implemented"],
             ["Workforce security", "164.308(a)(3)", "Authorization, clearance, termination procedures", "Implemented"],
             ["Information access management", "164.308(a)(4)", "Role-based access; minimum necessary", "Implemented"],
             ["Security awareness and training", "164.308(a)(5)", "Completed for all workforce members", "Implemented"],
             ["Security incident procedures", "164.308(a)(6)", "AGT-IRP-022", "Implemented"],
             ["Contingency plan", "164.308(a)(7)", "AGT-BCP-020, AGT-DRP-021, AGT-BKP-019", "Implemented"],
             ["Evaluation", "164.308(a)(8)", "Continuous monitoring and annual assessment", "Implemented"],
             ["Business associate contracts", "164.308(b)(1)", "Executed with Microsoft; outstanding for two AI providers", "Partial - F-02"]],
            widths=[1.6, 1.0, 2.5, 1.1])

    d.h1("5. Physical safeguards")
    d.table(["Standard", "Reference", "Implementation", "Status"],
            [["Facility access controls", "164.310(a)(1)", "Inherited from Microsoft Azure data centres", "Inherited"],
             ["Workstation use", "164.310(b)", "AGT-AUP-002 and AGT-RAP-005", "Implemented"],
             ["Workstation security", "164.310(c)", "Full-disk encryption, screen lock, MDM", "Implemented"],
             ["Device and media controls", "164.310(d)(1)", "AGT-MPP-008 and AGT-MDBYOD-010", "Implemented"]],
            widths=[1.6, 1.0, 2.5, 1.1])
    d.p("AGT operates no data centre. Physical safeguards for infrastructure are inherited "
        "from Microsoft under the shared responsibility model and are evidenced by Azure's "
        "own attestations rather than by AGT-performed controls.")

    d.h1("6. Technical safeguards")
    d.table(["Standard", "Reference", "Implementation", "Status"],
            [["Access control - unique user identification", "164.312(a)(2)(i)", "Entra ID and application accounts; no shared accounts", "Implemented"],
             ["Access control - emergency access", "164.312(a)(2)(ii)", "Break-glass administrative access with logging", "Implemented"],
             ["Access control - automatic logoff", "164.312(a)(2)(iii)", "Session expiry and server-side revocation", "Implemented"],
             ["Access control - encryption and decryption", "164.312(a)(2)(iv)", "AES-256 at rest; TLS 1.2+ in transit", "Implemented"],
             ["Audit controls", "164.312(b)", "Router-level audit of PHI-surface access", "Implemented"],
             ["Integrity", "164.312(c)(1)", "Database constraints; no tamper-evidence on the audit log", "Partial - F-03"],
             ["Person or entity authentication", "164.312(d)", "MFA via Entra ID; bcrypt for local accounts", "Implemented"],
             ["Transmission security", "164.312(e)(1)", "TLS 1.2+; database ssl=require", "Implemented"]],
            widths=[1.6, 1.0, 2.5, 1.1])

    d.h1("7. Threat and vulnerability analysis")
    d.table(["Threat", "Vulnerability", "L", "I", "Risk", "Mitigation"],
            [["Third-party AI provider retains PHI", "No BAA with two providers", "Moderate", "Very High", "High",
              "Execute BAAs; block PHI paths in the interim"],
             ["Credential compromise", "Password path exists alongside federated identity", "Moderate", "High", "High",
              "MFA, lockout, session revocation"],
             ["Insider misuse of PHI access", "Broad role could exceed minimum necessary", "Low", "High", "Moderate",
              "Quarterly access review; per-request audit"],
             ["Audit record tampering", "No hash chaining", "Low", "High", "Moderate", "Implement tamper-evidence"],
             ["Lost or stolen device", "Mobile access to the platform", "Moderate", "Moderate", "Moderate",
              "Encryption safe harbour; remote wipe"],
             ["Ransomware", "Cloud-hosted but reachable via credentials", "Low", "Very High", "Moderate",
              "Platform backups outside the compromise domain"],
             ["Misconfiguration exposes data", "Manual configuration steps", "Moderate", "High", "High",
              "Baselines and nightly drift detection"]],
            widths=[1.5, 1.6, 0.7, 0.7, 0.7, 1.6])

    d.h1("8. Findings and corrective actions")
    d.table(["ID", "Finding", "Standard", "Severity", "Corrective action", "Target"],
            [["F-01", "Clinical audio is transmitted to a transcription provider without redaction",
              "164.308(b)(1), 164.312(e)(1)", "High", "Execute a BAA or implement pre-transmission redaction", "90 days"],
             ["F-02", "No BAA with two AI providers processing potential PHI", "164.308(b)(1)", "High",
              "Execute BAAs with zero-retention terms", "90 days"],
             ["F-03", "Audit log lacks tamper-evidence", "164.312(c)(1)", "Moderate", "Implement hash chaining", "180 days"],
             ["F-04", "Database credential is not held in Key Vault", "164.312(a)(2)(iv)", "Moderate",
              "Migrate to a Key Vault reference", "60 days"],
             ["F-05", "One static analysis scanner has never executed", "164.308(a)(8)", "Moderate",
              "Execute on a supported platform", "60 days"]],
            widths=[0.5, 2.0, 1.2, 0.8, 1.5, 0.6])

    d.h1("9. Residual risk statement")
    d.p("With the corrective actions in section 8 outstanding, AGT's residual risk to ePHI "
        "is assessed as MODERATE. The dominant contributor is F-01 and F-02: clinical "
        "content can reach third-party processors that have not provided the written "
        "assurances HIPAA requires. Until those are executed or the paths are blocked, the "
        "compliant position is that PHI must not traverse them, and the residual risk "
        "reflects the gap between that requirement and the technical enforcement currently "
        "in place.")

    d.h1("10. Assessment metadata")
    d.table(["Attribute", "Value"],
            [["Assessment date", "2026-07-28"],
             ["Assessor", "AGT Security Officer, using the AGT Security Assurance Platform"],
             ["Method", "Automated scanning, configuration review, code review, policy review"],
             ["Scope", "DocuAction TEFCA ARC production and development environments"],
             ["Next assessment", "2027-07-28, or upon material change"],
             ["Retention", "Six years per 164.316(b)(2)"]],
            widths=[1.6, 4.6])

    d.roles([
        ["Security Officer", "Conducts the assessment; tracks corrective actions."],
        ["Privacy Officer", "Validates ePHI scoping and disclosure analysis."],
        ["CEO", "Accepts residual risk; funds corrective action."],
    ])
    _finish(d,
            [["HIPAA", "164.308(a)(1)(ii)(A)", "Risk analysis - this document is the required assessment.", "Met"],
             ["HIPAA", "164.308(a)(1)(ii)(B)", "Risk management - corrective actions in section 8.", "Met"],
             ["HIPAA", "164.308(a)(8)", "Periodic evaluation.", "Met"],
             ["HIPAA", "164.316(b)(2)", "Six-year retention of this assessment.", "Met"],
             ["NIST 800-66", "Rev. 2", "HIPAA Security Rule implementation guidance.", "Met"],
             ["NIST 800-30", "Rev. 1", "Risk assessment methodology.", "Met"],
             ["SOC 2", "CC3.2", "Risk identification and analysis.", "Met"]],
            [["AGT-RMP-023", "Risk Management Plan", "Findings feed the enterprise risk register"],
             ["AGT-PPF-014", "Privacy Policy Framework", "Privacy Rule obligations"],
             ["AGT-VRM-012", "Vendor Risk Management Policy", "BAA status for F-02"],
             ["AGT-AIGOV-013", "AI Governance Policy", "Controls for F-01"],
             ["AGT-CMS-024", "Continuous Monitoring Strategy", "Tracks corrective action progress"]])


def mcm():
    d = AGTDoc("AGT-MCM-026", "Master Compliance Matrix")
    _std_open(d, "This matrix maps the complete AGT policy set to the control frameworks "
                 "AGT is assessed against. It is the index an assessor uses to move from a "
                 "control reference to the document that implements it, and the instrument "
                 "AGT uses to find the controls no document covers.")
    d.h1("3. Document register")
    d.table(["Document ID", "Title", "Status"],
            [[i, t, "Issued"] for i, t in ALL_DOCS],
            widths=[1.5, 3.7, 1.0])

    d.h1("4. Framework coverage by document")
    d.table(["Document", "NIST 800-53", "HIPAA", "SOC 2", "ISO 27001", "CMMI L3"],
            [["AGT-EISP-001", "PM-1, PL-1", "164.316(a)", "CC1.1", "Clause 5.2", "OPD"],
             ["AGT-AUP-002", "PL-4, AC-20", "164.310(b)", "CC1.4", "A.5.10", "OPD"],
             ["AGT-ACP-003", "AC-1, AC-3", "164.312(a)(1)", "CC6.1", "A.5.15", "OPD"],
             ["AGT-IAM-004", "AC-2, IA-2", "164.312(a)(1),(d)", "CC6.1-6.3", "A.5.16-5.18", "OPD, IPM"],
             ["AGT-RAP-005", "AC-17, AC-19", "164.312(e)(1)", "CC6.6", "A.6.7", "-"],
             ["AGT-DCP-006", "RA-2, MP-3", "164.502(b)", "C1.1", "A.5.12", "OPD"],
             ["AGT-DRP-007", "AU-11, SI-12", "164.316(b)(2)", "C1.2", "A.5.33", "-"],
             ["AGT-MPP-008", "MP-2 to MP-7", "164.310(d)(1)", "CC6.5", "A.7.10", "-"],
             ["AGT-CKM-009", "SC-12, SC-13", "164.312(a)(2)(iv)", "CC6.1", "A.8.24", "-"],
             ["AGT-MDBYOD-010", "AC-19, AC-20", "164.310(d)(1)", "CC6.7", "A.8.1", "-"],
             ["AGT-AMP-011", "CM-8, PM-5", "164.310(d)(1)", "CC6.1", "A.5.9", "CM"],
             ["AGT-VRM-012", "SA-9, SR-3", "164.308(b)(1)", "CC9.2", "A.5.19-5.22", "-"],
             ["AGT-AIGOV-013", "SA-9, SI-10", "164.308(b)(1)", "CC9.2", "A.8.28", "-"],
             ["AGT-PPF-014", "PT family", "164.400-414", "P1-P8", "A.5.34", "-"],
             ["AGT-SSDLC-015", "SA-3, SA-11", "164.308(a)(1)", "CC8.1", "A.8.25-8.29", "RD, TS, VER"],
             ["AGT-CfMP-016", "CM-2, CM-6", "164.308(a)(8)", "CC7.1", "A.8.9", "CM"],
             ["AGT-ChMP-017", "CM-3, CM-4", "164.308(a)(8)", "CC8.1", "A.8.32", "CM, PMC"],
             ["AGT-LMP-018", "AU-2 to AU-12", "164.312(b)", "CC7.2", "A.8.15", "MA"],
             ["AGT-BKP-019", "CP-9, CP-10", "164.308(a)(7)(ii)(A)", "A1.2", "A.8.13", "-"],
             ["AGT-BCP-020", "CP-1, CP-2", "164.308(a)(7)(i)", "A1.2", "A.5.29", "-"],
             ["AGT-DRP-021", "CP-2, CP-4", "164.308(a)(7)(ii)(B)", "A1.3", "A.5.30", "-"],
             ["AGT-IRP-022", "IR-1 to IR-8", "164.308(a)(6)", "CC7.3-7.5", "A.5.24-5.28", "-"],
             ["AGT-RMP-023", "RA-1, RA-3", "164.308(a)(1)(ii)(A)", "CC3.1-3.4", "Clause 6.1", "RSKM"],
             ["AGT-CMS-024", "CA-7, PM-14", "164.308(a)(8)", "CC4.1", "Clause 9.1", "MA, PPQA"],
             ["AGT-HIRA-025", "RA-3", "164.308(a)(1)(ii)(A)", "CC3.2", "Clause 6.1", "RSKM"],
             ["AGT-MCM-026", "CA-2, PM-1", "164.316(a)", "CC4.1", "Clause 4.3", "OPD"]],
            widths=[1.4, 1.2, 1.3, 0.9, 1.1, 0.9])

    d.h1("5. NIST 800-53 control family coverage")
    d.table(["Family", "Name", "Primary documents", "Coverage"],
            [["AC", "Access Control", "AGT-ACP-003, AGT-IAM-004, AGT-RAP-005", "Full"],
             ["AT", "Awareness and Training", "AGT-EISP-001 (training completed separately)", "Full"],
             ["AU", "Audit and Accountability", "AGT-LMP-018, AGT-DRP-007", "Partial - no tamper-evidence"],
             ["CA", "Assessment and Authorization", "AGT-CMS-024, AGT-MCM-026", "Full"],
             ["CM", "Configuration Management", "AGT-CfMP-016, AGT-ChMP-017, AGT-AMP-011", "Full"],
             ["CP", "Contingency Planning", "AGT-BCP-020, AGT-DRP-021, AGT-BKP-019", "Full"],
             ["IA", "Identification and Authentication", "AGT-IAM-004", "Full"],
             ["IR", "Incident Response", "AGT-IRP-022", "Full"],
             ["MA", "Maintenance", "AGT-CfMP-016", "Partial - cloud-inherited"],
             ["MP", "Media Protection", "AGT-MPP-008", "Full"],
             ["PE", "Physical and Environmental", "AGT-HIRA-025 (inherited from Azure)", "Inherited"],
             ["PL", "Planning", "AGT-EISP-001, AGT-SSP-001", "Full"],
             ["PM", "Program Management", "AGT-EISP-001, AGT-RMP-023", "Full"],
             ["PS", "Personnel Security", "AGT-EISP-001, AGT-IAM-004", "Partial"],
             ["PT", "PII Processing and Transparency", "AGT-PPF-014", "Full"],
             ["RA", "Risk Assessment", "AGT-RMP-023, AGT-HIRA-025", "Full"],
             ["SA", "System and Services Acquisition", "AGT-SSDLC-015, AGT-VRM-012", "Full"],
             ["SC", "System and Communications Protection", "AGT-CKM-009, AGT-CfMP-016", "Full"],
             ["SI", "System and Information Integrity", "AGT-SSDLC-015, AGT-LMP-018", "Partial - no SIEM"],
             ["SR", "Supply Chain Risk Management", "AGT-VRM-012, AGT-AMP-011", "Partial"]],
            widths=[0.6, 1.9, 2.5, 1.2])

    d.h1("6. Gap analysis")
    d.table(["Gap", "Framework impact", "Severity", "Planned closure"],
            [["Two AI provider BAAs not executed", "HIPAA 164.308(b)(1)", "High", "90 days"],
             ["Audit log lacks tamper-evidence", "NIST AU-9, HIPAA 164.312(c)(1)", "Moderate", "180 days"],
             ["No SIEM correlation", "NIST SI-4, FedRAMP AU-6(1)", "Moderate", "Roadmap"],
             ["One SAST scanner non-functional", "NIST RA-5, SA-11", "Moderate", "60 days"],
             ["DATABASE_URL not in Key Vault", "NIST SC-12, HIPAA 164.312(a)(2)(iv)", "Moderate", "60 days"],
             ["No pre-provisioned alternate processing site", "NIST CP-7", "Low", "Documented procedure accepted"],
             ["PIM not enabled tenant-wide", "FedRAMP AC-2(1)", "Low", "Roadmap"],
             ["ISO 27701 and ISO 42001 alignment partial", "Privacy and AI management systems", "Low", "Roadmap"]],
            widths=[2.0, 1.8, 0.9, 1.5])

    d.h1("7. Third-party assessment requirements")
    d.table(["Assessment", "Frequency", "Performed by", "Status"],
            [["Penetration test", "Annually", "Independent third party", "Required - not yet performed"],
             ["SOC 2 Type II audit", "Annually", "Licensed CPA firm", "Roadmap"],
             ["ISO 27001 surveillance audit", "Annually", "Accredited certification body", "Maintained"],
             ["CMMI appraisal", "Per appraisal cycle", "Certified lead appraiser", "Maintained at Level 3"],
             ["HIPAA assessment", "Annually", "AGT internal, may be third party", "Complete - AGT-HIRA-025"],
             ["FedRAMP 3PAO assessment", "Per authorization", "Accredited 3PAO", "Not initiated"]],
            widths=[1.9, 1.2, 1.9, 1.2])

    d.h1("8. How to use this matrix")
    d.numbered([
        "From a control reference, use section 4 or 5 to find the owning document.",
        "From a document, use its own compliance mapping table for control-level detail.",
        "For evidence, consult AGT-CMS-024 for the artifact each activity produces.",
        "For open items, section 6 is the authoritative gap list and is reconciled with the "
        "POA&M quarterly.",
    ])
    d.p("A coverage claim in this matrix means a policy exists and states the control. It "
        "does not by itself mean the control is operating effectively - that is what the "
        "evidence in AGT-CMS-024 and the assessments in section 7 establish. The two are "
        "kept distinct deliberately, because conflating them is the most common way a "
        "compliance programme overstates itself.")

    d.roles([
        ["CEO", "Owns the compliance programme; approves the matrix annually."],
        ["Security Officer", "Maintains the matrix; reconciles gaps with the POA&M."],
        ["Privacy Officer", "Maintains privacy framework mappings."],
    ])
    _finish(d,
            [["NIST 800-53", "CA-2, PM-1, PM-4", "Control assessment, information security programme plan, POA&M process.", "Met"],
             ["HIPAA", "164.316(a)", "Policies and procedures maintained in written form.", "Met"],
             ["SOC 2", "CC4.1, CC5.3", "Control monitoring and deployment through policy.", "Met"],
             ["ISO 27001", "Clause 4.3, Annex A", "Scope of the ISMS and Statement of Applicability support.", "Met"],
             ["ISO 20000-1", "Clause 4", "Service management system context.", "Met"],
             ["CMMI L3", "OPD, OPF", "Organizational process definition and focus.", "Met"],
             ["FedRAMP", "CA-2, PM-4", "Control traceability supporting authorization.", "Partial"]],
            [[i, t, "Mapped in this matrix"] for i, t in ALL_DOCS[:6]] +
            [["AGT-SSP-001", "System Security Plan", "Master document - control implementation detail"]])


if __name__ == "__main__":
    bkp(); bcp(); drp21(); irp(); rmp(); cms(); hira(); mcm()
    print(f"  batch 4 complete: {len(BUILT)} documents")
