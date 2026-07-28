"""Tier 1 policies, batch 3: AGT-SSDLC-015 .. AGT-LMP-018."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402
from tier1_batch1 import _std_open, _finish, BUILT  # noqa: E402


def ssdlc():
    d = AGTDoc("AGT-SSDLC-015", "Secure Software Development Lifecycle")
    _std_open(d, "This policy defines how security is built into the DocuAction TEFCA ARC "
                 "software lifecycle rather than tested for at the end. It covers "
                 "requirements, design, implementation, verification, release, and "
                 "maintenance, and it aligns with CMMI Level 3 process expectations for "
                 "defined, organization-wide engineering processes.")
    d.h1("3. Lifecycle phases and security gates")
    d.table(["Phase", "Security activity", "Gate", "Evidence"],
            [["Requirements", "Identify data classes, regulatory obligations, abuse cases",
              "Security requirements documented", "Requirement record"],
             ["Architecture", "Threat model; authorization boundary; data-flow review",
              "Threat model reviewed", "Threat model document"],
             ["Implementation", "Secure coding standards; secrets kept out of source",
              "Pre-commit hooks pass", "Commit history"],
             ["Code review", "Peer review with a security lens", "One approving review required",
              "Pull request record"],
             ["Verification", "SAST, secrets detection, dependency scan, DAST",
              "No Critical findings; Highs triaged", "Scan reports and SBOM"],
             ["Release", "Artifact verification; change approval",
              "Change record approved", "Deployment record"],
             ["Maintenance", "Vulnerability monitoring; patch management",
              "Nightly scan reviewed", "Scan history"]],
            widths=[1.2, 2.3, 1.5, 1.2])

    d.h1("4. Threat modelling")
    d.p("A threat model is required for any change that introduces a new trust boundary, a "
        "new external dependency, or a new class of data. The model identifies the assets, "
        "the boundaries, the adversary, and the controls at each crossing.")
    d.bullets([
        "Method: STRIDE applied to the data-flow diagram.",
        "Boundaries in this system: browser to API, API to database, API to AI provider, "
        "API to Key Vault, CI/CD to Azure.",
        "Each identified threat is either mitigated by a named control, accepted with "
        "documented rationale, or transferred by contract.",
    ])

    d.h1("5. Secure coding standards")
    d.bullets([
        "Parameterized queries only. String-built SQL is a defect regardless of whether the "
        "input appears trusted.",
        "Authorization enforced at the router level, not per handler, so a new endpoint "
        "inherits the requirement rather than needing to remember it.",
        "No secret in source, in a committed .env, or in a log line.",
        "Input validated at the boundary with typed models; output encoded for its context.",
        "Errors return a correlation identifier, never a stack trace or internal path.",
        "Dependencies pinned to explicit versions so a build is reproducible and auditable.",
        "Route declaration order matters: a static path must be declared before a "
        "same-prefix parameterized path, or the catch-all silently shadows it.",
    ])

    d.h1("6. Automated verification")
    d.table(["Control", "Tool", "Trigger", "Failure handling"],
            [["SAST (Python)", "Bandit + AGT custom rules", "Every build and nightly", "Critical blocks release"],
             ["SAST (multi-language)", "Semgrep", "Nightly", "Currently non-functional on Windows - see note"],
             ["Secrets detection", "Gitleaks", "Every build and nightly", "Any finding blocks release"],
             ["Dependency scan (Python)", "pip-audit", "Every build and nightly", "Critical blocks release"],
             ["Dependency scan (JS)", "npm audit", "Every build", "High triaged within 7 days"],
             ["SBOM", "CycloneDX", "Every full scan", "Absence blocks release"],
             ["DAST", "AGT DAST suite", "Against dev only", "Never run against production"],
             ["Code scanning", "GitHub CodeQL", "Push and pull request", "Alerts triaged"]],
            widths=[1.5, 1.5, 1.5, 1.7])
    d.note("Semgrep is installed but has never produced a result on this codebase because "
           "no semgrep-core build exists for the development platform. Every security score "
           "AGT reports is therefore missing one scanner's coverage. This is stated in the "
           "gate output rather than omitted, because a coverage gap that is not reported "
           "reads as a clean result.")

    d.h1("7. CI/CD security")
    d.bullets([
        "Pipelines are defined as code and reviewed like application code.",
        "Deployment credentials are GitHub environment secrets scoped to the resource groups "
        "they deploy to, never subscription-wide.",
        "Production deployment requires an environment approval; a tag push builds and "
        "retains an artifact but does not deploy.",
        "The build verifies its own artifact before upload: no .env file present, the "
        "expected runtime dependencies present, no duplicate package metadata.",
        "Application runtime secrets never enter the pipeline; they are resolved at runtime "
        "from Key Vault by managed identity.",
    ])

    d.h1("8. Release management")
    d.table(["Requirement", "Detail"],
            [["Versioning", "Annotated git tag; the tag is the deployable unit"],
             ["Artifact provenance", "Built from the tagged commit, reproducibly"],
             ["Environment order", "Dev first, verified, then production"],
             ["Production deployment", "Replaces rather than overlays, so stale components cannot persist"],
             ["Dev deployment", "Build-on-deploy; must not use the replace flag, which removes the build manifest"],
             ["Rollback", "Redeploy the previous tag; documented in AGT-ChMP-017"],
             ["Verification", "Health endpoint plus a functional probe unique to the new build"]],
            widths=[1.6, 4.6])
    d.p("A health endpoint alone does not verify a deployment. It answers successfully from "
        "the previous build throughout, so the only reliable confirmation is an endpoint or "
        "behaviour that exists solely in the new version.")

    d.h1("9. CMMI Level 3 alignment")
    d.table(["Process area", "How this policy satisfies it"],
            [["RD - Requirements Development", "Security requirements are elicited per section 3"],
             ["TS - Technical Solution", "Secure design and coding standards in sections 4 and 5"],
             ["VER - Verification", "Peer review and automated scanning in sections 3 and 6"],
             ["VAL - Validation", "DAST and functional verification against dev"],
             ["CM - Configuration Management", "Versioned artifacts and baselines; see AGT-CfMP-016"],
             ["PPQA - Process and Product QA", "Gate enforcement and evidence retention"],
             ["OPD - Organizational Process Definition", "This document is the defined process"]],
            widths=[2.0, 4.2])

    d.roles([
        ["Engineering", "Implements the lifecycle; remediates findings; maintains pipelines."],
        ["Security Officer", "Owns scanning configuration; triages findings; approves gate exceptions."],
        ["CEO", "Approves release when a gate exception is required."],
    ])
    _finish(d,
            [["NIST 800-53", "SA-3, SA-8, SA-11, SA-15", "SDLC, security engineering principles, developer testing, development process.", "Met"],
             ["NIST 800-53", "SI-2, RA-5", "Flaw remediation and vulnerability scanning.", "Met"],
             ["NIST SSDF", "PO, PS, PW, RV", "Prepare, protect, produce, respond practices.", "Met"],
             ["HIPAA", "164.308(a)(1)(ii)(B)", "Security measures reducing risk in developed software.", "Met"],
             ["SOC 2", "CC8.1", "Change management for system development.", "Met"],
             ["ISO 27001", "A.8.25, A.8.26, A.8.27, A.8.28, A.8.29", "Secure development lifecycle, requirements, architecture, coding, testing.", "Met"],
             ["CMMI L3", "RD, TS, VER, VAL, CM, PPQA, OPD", "Defined engineering process across the lifecycle.", "Met"],
             ["FedRAMP", "SA-11, RA-5(1)", "Developer testing and scanning with update capability.", "Partial - one scanner non-functional."]],
            [["AGT-CfMP-016", "Configuration Management Policy", "Baselines the pipeline deploys"],
             ["AGT-ChMP-017", "Change Management Policy", "Approval path for releases"],
             ["AGT-AMP-011", "Asset Management Policy", "SBOM as component inventory"],
             ["AGT-CKM-009", "Cryptographic Key Management Policy", "Secret handling in build and runtime"]])


def cfmp():
    d = AGTDoc("AGT-CfMP-016", "Configuration Management Policy")
    _std_open(d, "This policy establishes baseline configurations for AGT systems and the "
                 "process for maintaining them. Configuration drift is the mechanism by "
                 "which a system that passed assessment stops being the system that was "
                 "assessed, usually without anyone deciding that it should.")
    d.h1("3. Baseline configurations")
    d.table(["Component", "Baseline element", "Required value"],
            [["App Service (prod)", "Build mode", "Pre-built dependencies; no build on deploy"],
             ["App Service (prod)", "Startup command", "Gunicorn with a Uvicorn worker class, bound to the platform port"],
             ["App Service (dev)", "Build mode", "Build on deploy enabled"],
             ["App Service (both)", "Minimum TLS version", "1.2"],
             ["App Service (both)", "HTTPS only", "Enabled"],
             ["App Service (both)", "FTP state", "Disabled (open finding)"],
             ["App Service (both)", "Health check path", "/health (open finding)"],
             ["PostgreSQL", "SSL enforcement", "Required"],
             ["PostgreSQL", "Geo-redundant backup", "Enabled at creation"],
             ["Key Vault", "Authorization model", "Azure RBAC"],
             ["Key Vault", "Soft delete and purge protection", "Enabled"],
             ["Static Web Apps", "Configuration file", "staticwebapp.config.json present and reviewed"],
             ["Application", "Allowed hosts", "Explicit list; wildcard prohibited in production"],
             ["Application", "OpenAPI exposure", "Disabled in production"],
             ["Application", "Signing key length", "64 characters minimum, enforced at startup"]],
            widths=[1.4, 1.8, 3.0])

    d.h1("4. Hardening standards")
    d.bullets([
        "Azure resources follow the Microsoft cloud security benchmark where applicable.",
        "Only required ports and protocols are exposed; the platform is reachable over HTTPS only.",
        "Default credentials do not exist anywhere in the deployment.",
        "Diagnostic and debug interfaces are disabled in production, including the "
        "interactive API documentation.",
        "Security headers are set at the application layer and verified by DAST.",
    ])

    d.h1("5. The host-allowlist interaction")
    d.p("Trusted-host middleware rejects a request whose Host header is not in the allowed "
        "list with a 400 on every path, including the health endpoint. This has a "
        "non-obvious operational consequence: enabling a platform health probe before the "
        "probe's hostname is in the allowlist will fail every check and remove every "
        "instance from rotation. Configuration changes to these two settings are therefore "
        "sequenced deliberately, not applied independently.")

    d.h1("6. Change control for configuration")
    d.p("Configuration changes follow AGT-ChMP-017. Changes to any value in section 3 are "
        "treated as normal or major changes, never as standard, because each of them can "
        "take the platform down or silently weaken a control.")

    d.h1("7. Drift detection")
    d.table(["Check", "Method", "Frequency"],
            [["Azure resource configuration", "Read-only Azure assessment in the security platform", "Nightly"],
             ["Application settings and Key Vault references", "Scripted comparison against the baseline", "Nightly"],
             ["Deployed component inventory", "SBOM comparison between releases", "Every release"],
             ["Unauthorized resources", "Resource Graph reconciliation against the asset register", "Quarterly"]],
            widths=[2.2, 2.6, 1.4])
    d.p("Component drift has a specific failure mode worth naming. A deployment that "
        "overlays rather than replaces leaves package metadata from previous releases in "
        "place; dependency scanning and the SBOM then describe a component set that is not "
        "what is running. The production deployment method replaces the content directory "
        "precisely to prevent this.")

    d.h1("8. Configuration documentation")
    d.bullets([
        "Infrastructure is described as code where practical; templates are versioned.",
        "Every baseline value in section 3 has a stated rationale, not only a value.",
        "Deviations are recorded as exceptions with an owner and an expiry date.",
    ])

    d.h1("9. Open configuration findings")
    d.table(["Finding", "Environments", "Risk", "Remediation reference"],
            [["FTP/FTPS deployment enabled", "prod, dev", "A second write path that bypasses pipeline verification", "docs/runbooks/ftp-disable.md"],
             ["Health check path not configured", "prod, dev", "A wedged instance keeps receiving traffic", "docs/runbooks/app-service-health-check.md"],
             ["DATABASE_URL not a Key Vault reference", "prod", "Database password readable with Reader role", "docs/runbooks/database-url-to-keyvault.md"],
             ["No Key Vault in the dev environment", "dev", "Secrets held as literals; drift from the production pattern", "docs/runbooks/dev-keyvault-setup.md"]],
            widths=[1.9, 1.0, 1.9, 1.4])

    d.roles([
        ["Engineering", "Defines and maintains baselines; implements drift detection."],
        ["Security Officer", "Reviews drift reports; approves exceptions with expiry."],
        ["CEO", "Approves baseline changes affecting the authorization boundary."],
    ])
    _finish(d,
            [["NIST 800-53", "CM-2, CM-3, CM-6, CM-7", "Baseline configuration, change control, configuration settings, least functionality.", "Met"],
             ["NIST 800-53", "CM-8, SI-7", "Component inventory; software and information integrity.", "Met"],
             ["HIPAA", "164.308(a)(8)", "Evaluation of technical and non-technical safeguards.", "Met"],
             ["SOC 2", "CC7.1, CC8.1", "Configuration monitoring and change authorization.", "Met"],
             ["ISO 27001", "A.8.9, A.8.19", "Configuration management; installation of software on systems.", "Met"],
             ["CMMI L3", "CM, PPQA", "Configuration identification, control, and audit.", "Met"],
             ["FedRAMP", "CM-2(2), CM-6(1)", "Automated baseline maintenance and setting enforcement.", "Partial"]],
            [["AGT-ChMP-017", "Change Management Policy", "Approval path for configuration change"],
             ["AGT-AMP-011", "Asset Management Policy", "Assets that carry these baselines"],
             ["AGT-SSDLC-015", "Secure SDLC", "Pipeline that applies configuration"],
             ["AGT-LMP-018", "Logging and Monitoring Policy", "Detects drift and failures"]])


def chmp():
    d = AGTDoc("AGT-ChMP-017", "Change Management Policy")
    _std_open(d, "This policy governs how changes to AGT production systems are proposed, "
                 "assessed, approved, implemented, verified, and reversed. It aligns with "
                 "ISO 20000-1 service management expectations and CMMI Level 3 "
                 "configuration management.")
    d.h1("3. Change categories")
    d.table(["Category", "Definition", "Approval", "Lead time"],
            [["Standard", "Pre-approved, repeatable, low risk, documented procedure",
              "Pre-approved", "None"],
             ["Normal", "Any change to production not pre-approved", "Change Advisory Board", "3 business days"],
             ["Major", "Architecture, authorization boundary, or data model change",
              "CAB + CEO", "10 business days"],
             ["Emergency", "Required to restore service or close an active exploit",
              "CEO or delegate, retrospective CAB", "Immediate"]],
            widths=[1.1, 2.4, 1.5, 1.2])

    d.h1("4. Request for change")
    d.p("Every normal, major, and emergency change is recorded with the following, and a "
        "change without a stated rollback is not approved:")
    d.numbered([
        "Description and business justification.",
        "Systems and data classes affected.",
        "Risk assessment, including what breaks if it fails.",
        "Test evidence from a non-production environment.",
        "Implementation steps.",
        "Verification steps, including at least one check unique to the new state.",
        "Rollback procedure and the point of no return.",
        "Requested window and expected user impact.",
    ])

    d.h1("5. Change Advisory Board")
    d.table(["Member", "Role in review"],
            [["CEO or delegate", "Chair; approves major and emergency changes"],
             ["Security Officer", "Assesses security and compliance impact"],
             ["Engineering lead", "Assesses technical risk and rollback viability"],
             ["Privacy Officer", "Consulted where PHI handling changes"]],
            widths=[1.8, 4.4])

    d.h1("6. Deployment controls")
    d.p("Production deployment is performed by the CI/CD pipeline from a tagged commit. "
        "Direct interactive deployment is an exception requiring documented approval.")
    d.table(["Environment", "Deployment method", "Replace flag", "Why"],
            [["Production", "Zip deploy of a pre-built artifact", "Required",
              "Without it the deployment overlays and stale components persist, corrupting the SBOM and dependency scan"],
             ["Development", "Zip deploy with build-on-deploy", "Prohibited",
              "It deletes the build manifest the startup script needs, producing a crash loop and a 503"]],
            widths=[1.2, 1.9, 0.9, 2.2])
    d.note("This asymmetry has caused a dev outage. The correct flag depends on the "
           "environment's build mode, not on preference, and is the single most important "
           "operational detail in this policy.")

    d.h1("7. Verification after change")
    d.bullets([
        "Confirm the deployment record server-side. The deployment CLI can report a "
        "connection failure while the deployment is still succeeding, so its exit status is "
        "not authoritative.",
        "Never re-run a deployment to obtain a cleaner error message. A second concurrent "
        "build on the same application collides with the first and produces a real failure "
        "from a false one.",
        "Verify with an endpoint that exists only in the new build. The health endpoint "
        "answers from the old code throughout and proves nothing about the change.",
        "Development environments may require an explicit restart to load the new build.",
    ])

    d.h1("8. Emergency change")
    d.numbered([
        "Implement the minimum change required to restore service or stop an exploit.",
        "Notify the CEO and Security Officer at the time of implementation, not afterwards.",
        "Record the change within 24 hours with full detail.",
        "Present to the next CAB for retrospective review.",
        "Where the emergency change bypassed a control, record it as a finding and restore "
        "the control.",
    ])

    d.h1("9. Rollback")
    d.p("Rollback is redeployment of the previous tag. Database migrations complicate this: "
        "a change that alters schema must be forward-compatible with the previous "
        "application version, or the rollback plan must include a tested down-migration. "
        "Additive, nullable columns are the preferred pattern precisely because they make "
        "rollback trivial.")

    d.h1("10. Change record retention")
    d.p("Change records are retained for six years per AGT-DRP-007 and constitute the "
        "primary evidence for SOC 2 CC8.1 and HIPAA evaluation requirements.")

    d.roles([
        ["CEO", "Chairs the CAB; approves major and emergency changes."],
        ["Security Officer", "Assesses security impact; verifies control restoration after emergencies."],
        ["Engineering", "Prepares RFCs; implements and verifies; executes rollback."],
    ])
    _finish(d,
            [["NIST 800-53", "CM-3, CM-4, CM-5, CM-9", "Configuration change control, impact analysis, access restrictions, CM plan.", "Met"],
             ["HIPAA", "164.308(a)(8)", "Periodic evaluation following environmental or operational change.", "Met"],
             ["SOC 2", "CC8.1", "Authorization, design, development, and implementation of changes.", "Met"],
             ["ISO 27001", "A.8.32", "Change management.", "Met"],
             ["ISO 20000-1", "Clause 8.5.1", "Change management for services.", "Met"],
             ["CMMI L3", "CM, PMC", "Change control and project monitoring.", "Met"],
             ["FedRAMP", "CM-3(2)", "Testing, validation, and documentation of changes.", "Met"]],
            [["AGT-CfMP-016", "Configuration Management Policy", "Defines the baselines being changed"],
             ["AGT-SSDLC-015", "Secure SDLC", "Release process feeding change control"],
             ["AGT-IRP-022", "Incident Response Plan", "Emergency changes during incidents"],
             ["AGT-BKP-019", "Backup Policy", "Recovery point before a risky change"]])


def lmp():
    d = AGTDoc("AGT-LMP-018", "Logging and Monitoring Policy")
    _std_open(d, "This policy defines what AGT logs, how long it keeps it, what it alerts "
                 "on, and who reviews it. Logging is the control that makes every other "
                 "control auditable; an access restriction that cannot be shown to have "
                 "operated is an assertion rather than evidence.")
    d.h1("3. Events that must be logged")
    d.table(["Event class", "Examples", "Source", "Retention"],
            [["Authentication", "Success, failure, lockout, MFA challenge", "Application + Entra ID", "6 years"],
             ["Authorization", "Access denied, role elevation, privilege use", "Application", "6 years"],
             ["PHI access", "Every request to a PHI-bearing endpoint", "Application audit log", "6 years"],
             ["Administrative action", "User creation, role change, purge, configuration change", "Application + Azure activity", "6 years"],
             ["Data modification", "Create, update, delete of regulated records", "Application audit log", "6 years"],
             ["Security events", "Rate limiting, injection attempts, invalid tokens", "Application", "6 years"],
             ["Deployment", "Deployment start, result, active version", "Azure deployment log", "6 years"],
             ["Application diagnostics", "Errors, latency, exceptions", "Application Insights", "90 days hot"],
             ["Infrastructure", "Resource changes, scaling, restarts", "Azure Monitor", "1 year"]],
            widths=[1.3, 2.1, 1.5, 1.3])

    d.h1("4. Required fields")
    d.p("An audit record answers who, what, when, where, and outcome. The platform's audit "
        "helper captures user identifier, action, resource type, resource identifier, "
        "timestamp, source address, and result.")
    d.p("Source address is taken from the forwarded-for header rather than the socket peer, "
        "because the platform terminates TLS at a load balancer and the socket address is "
        "always the balancer. An audit log recording the load balancer as the actor's "
        "location is worse than no address field, because it looks correct.")

    d.h1("5. What must never be logged")
    d.bullets([
        "PHI in any form, including in request bodies, query strings, and error messages.",
        "Credentials, tokens, API keys, or connection strings.",
        "Full request bodies for endpoints that carry regulated data.",
        "Cardholder data or government identifiers.",
    ])
    d.p("For PHI-access auditing the platform records the route template rather than the "
        "concrete path, because a patient identifier embedded in a URL becomes PHI inside "
        "the audit log - turning the privacy control into a privacy exposure.")

    d.h1("6. Log protection")
    d.bullets([
        "Application audit records are written to the database within the transaction "
        "boundary of the action being audited where practical.",
        "Azure activity logs are a separate store from the application, so compromise of "
        "the application does not confer control of the infrastructure record.",
        "Log stores are access-controlled; read access is limited to the Security Officer "
        "and CEO.",
        "Audit records are never edited or deleted before their retention period expires.",
    ])
    d.note("Open finding: the audit log has no tamper-evidence mechanism such as a hash "
           "chain. A database-level compromise could rewrite history without detection. "
           "This is tracked in the POA&M; it does not prevent the log from serving its "
           "operational purpose but it does limit its strength as forensic evidence.")

    d.h1("7. Audit availability requirement")
    d.p("Audit writes must not take the audited function down. Where the audit path fails, "
        "the platform logs the failure at error level and continues, rather than refusing "
        "the clinical operation. This is a deliberate availability-over-completeness "
        "decision, and the error line is the signal that the trail has a gap.")

    d.h1("8. Alerting")
    d.table(["Alert", "Condition", "Severity", "Response"],
            [["Availability", "Health endpoint non-200 for 5 minutes", "Critical", "Immediate investigation"],
             ["Error rate", "More than 10 server errors in 5 minutes", "High", "Investigate within 1 hour"],
             ["Authentication anomaly", "Repeated failures or impossible travel", "High", "Security Officer review"],
             ["Privilege escalation", "Role change to admin", "High", "Confirm authorization"],
             ["Audit failure", "PHI access audit write failure", "High", "Investigate the trail gap"],
             ["Certificate expiry", "Within 30 days", "Medium", "Renewal check"],
             ["Latency", "95th percentile above 5 seconds for 15 minutes", "Medium", "Performance review"],
             ["Scheduled job failure", "Daily job did not complete", "Medium", "Self-heal, then investigate"]],
            widths=[1.4, 2.0, 0.9, 1.9])
    d.p("An alert keyed on the deployment CLI's exit status will produce false alarms, "
        "because the CLI reports connection failures while the deployment is succeeding. "
        "Deployment alerting is keyed on the server-side deployment record.")

    d.h1("9. Review cadence")
    d.table(["Activity", "Frequency", "Reviewer"],
            [["Security event review", "Weekly", "Security Officer"],
             ["Privileged action review", "Monthly", "Security Officer"],
             ["PHI access pattern review", "Quarterly", "Privacy Officer"],
             ["Alert rule effectiveness review", "Quarterly", "Security Officer"],
             ["Log retention verification", "Annually", "Security Officer"]],
            widths=[2.4, 1.4, 2.4])

    d.h1("10. SIEM roadmap")
    d.p("AGT does not currently operate a SIEM. Correlation is performed manually against "
        "Application Insights and Azure Monitor. Planned progression: centralize application "
        "and infrastructure logs into a single Log Analytics workspace, define correlation "
        "rules for credential-stuffing and privilege-escalation patterns, then evaluate "
        "Microsoft Sentinel. This is a known maturity gap, recorded rather than implied.")

    d.roles([
        ["Security Officer", "Owns alerting; performs weekly and monthly reviews."],
        ["Privacy Officer", "Reviews PHI access patterns quarterly."],
        ["Engineering", "Implements audit capture; ensures prohibited data is never logged."],
    ])
    _finish(d,
            [["NIST 800-53", "AU-2, AU-3, AU-6, AU-9, AU-11, AU-12", "Event logging, content, review, protection, retention, generation.", "Met"],
             ["NIST 800-53", "SI-4", "System monitoring and alerting.", "Partial - no SIEM correlation."],
             ["HIPAA", "164.312(b)", "Audit controls over systems containing ePHI.", "Met"],
             ["HIPAA", "164.308(a)(1)(ii)(D)", "Information system activity review.", "Met"],
             ["HIPAA", "164.316(b)(2)", "Six-year retention of audit documentation.", "Met"],
             ["SOC 2", "CC7.2, CC7.3", "Monitoring for anomalies and evaluation of events.", "Met"],
             ["ISO 27001", "A.8.15, A.8.16", "Logging; monitoring activities.", "Met"],
             ["FedRAMP", "AU-6(1), SI-4(2)", "Automated audit review and near real-time analysis.", "Partial"]],
            [["AGT-DRP-007", "Data Retention Policy", "Log retention schedule"],
             ["AGT-IRP-022", "Incident Response Plan", "Alerts feed incident detection"],
             ["AGT-CMS-024", "Continuous Monitoring Strategy", "Review cadence and metrics"],
             ["AGT-DCP-006", "Data Classification Policy", "What must never appear in a log"]])


if __name__ == "__main__":
    ssdlc(); cfmp(); chmp(); lmp()
    print(f"  batch 3 complete: {len(BUILT)} documents")
