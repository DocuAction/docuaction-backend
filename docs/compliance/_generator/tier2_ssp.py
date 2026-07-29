"""Tier 2: AGT-SSP-001 System Security Plan - the master document."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402

OUT = Path(__file__).resolve().parents[1]

# NIST 800-53 control implementation. Each row: family, control, status,
# implementation, responsible party, inherited, evidence.
CONTROLS = [
    ("AC", "AC-1", "Implemented", "Access control policy documented and reviewed annually.", "Security Officer", "No", "AGT-ACP-003"),
    ("AC", "AC-2", "Implemented", "Account lifecycle defined; provisioning, modification, disablement with same-day SLA on termination.", "Security Officer", "No", "AGT-IAM-004 s7"),
    ("AC", "AC-3", "Implemented", "Router-level authorization dependencies enforce role requirements on every endpoint.", "Engineering", "No", "Code; DAST results"),
    ("AC", "AC-5", "Implemented", "Separation between approval (CEO) and implementation (Engineering) for changes.", "CEO", "No", "AGT-ChMP-017"),
    ("AC", "AC-6", "Implemented", "Least privilege; Azure roles scoped to resource group, never subscription.", "Security Officer", "No", "AGT-IAM-004 s6.2"),
    ("AC", "AC-7", "Implemented", "Progressive delay after five failed authentication attempts.", "Engineering", "No", "Code; DAST AUTH tests"),
    ("AC", "AC-11", "Implemented", "Session expiry with server-side revocation.", "Engineering", "No", "AGT-IAM-004 s5"),
    ("AC", "AC-17", "Implemented", "Remote access governed by Conditional Access; TLS 1.2+ enforced.", "Security Officer", "Partial", "AGT-RAP-005"),
    ("AC", "AC-19", "Implemented", "Mobile device requirements including encryption and MDM enrolment.", "Security Officer", "No", "AGT-MDBYOD-010"),
    ("AC", "AC-20", "Implemented", "External system use restricted by acceptable use policy.", "Security Officer", "No", "AGT-AUP-002"),
    ("AT", "AT-1", "Implemented", "Awareness and training policy established.", "CEO", "No", "AGT-EISP-001"),
    ("AT", "AT-2", "Implemented", "Security awareness training completed by all workforce members.", "Security Officer", "No", "Training records"),
    ("AT", "AT-3", "Implemented", "Role-based training for engineering and privileged roles.", "Security Officer", "No", "Training records"),
    ("AU", "AU-2", "Implemented", "Auditable events defined: authentication, authorization, PHI access, admin action.", "Engineering", "No", "AGT-LMP-018 s3"),
    ("AU", "AU-3", "Implemented", "Records capture who, what, when, source address, outcome.", "Engineering", "No", "Audit schema"),
    ("AU", "AU-6", "Implemented", "Weekly security event review; monthly privileged action review.", "Security Officer", "No", "Review records"),
    ("AU", "AU-9", "Partial", "Access-controlled log stores; separate Azure activity log. No tamper-evidence on the application audit log.", "Engineering", "Partial", "POA&M item"),
    ("AU", "AU-11", "Implemented", "Six-year retention aligned to HIPAA 164.316(b)(2).", "Security Officer", "No", "AGT-DRP-007"),
    ("AU", "AU-12", "Implemented", "Audit generation at the router level for the PHI surface.", "Engineering", "No", "Code"),
    ("CA", "CA-2", "Partial", "Internal assessment performed; independent penetration test not yet conducted.", "Security Officer", "No", "AGT-HIRA-025"),
    ("CA", "CA-5", "Implemented", "POA&M maintained with owners and target dates.", "Security Officer", "No", "POA&M"),
    ("CA", "CA-7", "Implemented", "Continuous monitoring with nightly automated scanning.", "Security Officer", "No", "AGT-CMS-024"),
    ("CA", "CA-9", "Implemented", "Internal system connections documented in the data flow.", "Engineering", "No", "SSP s6"),
    ("CM", "CM-2", "Implemented", "Baseline configurations documented per component.", "Engineering", "No", "AGT-CfMP-016 s3"),
    ("CM", "CM-3", "Implemented", "Change control with CAB review and documented rollback.", "CEO", "No", "AGT-ChMP-017"),
    ("CM", "CM-4", "Implemented", "Security impact analysis required in every RFC.", "Security Officer", "No", "Change records"),
    ("CM", "CM-5", "Implemented", "Deployment restricted to the CI/CD pipeline from a tagged commit.", "Engineering", "No", "Workflow definitions"),
    ("CM", "CM-6", "Partial", "Settings enforced by baseline; FTP and health check settings are open findings.", "Engineering", "No", "AGT-CfMP-016 s9"),
    ("CM", "CM-7", "Implemented", "HTTPS only; OpenAPI disabled in production; least functionality.", "Engineering", "No", "Configuration"),
    ("CM", "CM-8", "Implemented", "Component inventory maintained; SBOM generated on every full scan.", "Engineering", "No", "SBOM artifacts"),
    ("CP", "CP-1", "Implemented", "Contingency planning policy established.", "CEO", "No", "AGT-BCP-020"),
    ("CP", "CP-2", "Implemented", "Contingency plan with critical functions and tolerable outage.", "CEO", "No", "AGT-BCP-020 s3"),
    ("CP", "CP-4", "Implemented", "Annual tabletop and DR test with recorded results.", "Engineering", "No", "DR Test Record"),
    ("CP", "CP-6", "Implemented", "Geo-redundant backup storage in the Azure paired region.", "Engineering", "Yes", "Azure configuration"),
    ("CP", "CP-7", "Partial", "Alternate processing is a documented procedure, not a pre-provisioned site.", "Engineering", "Partial", "AGT-DRP-021 s4.2"),
    ("CP", "CP-9", "Implemented", "Automated continuous backup with 35-day point-in-time restore.", "Engineering", "Yes", "AGT-BKP-019"),
    ("CP", "CP-10", "Implemented", "Recovery procedures with quarterly restoration testing.", "Engineering", "No", "Backup Test Record"),
    ("IA", "IA-2", "Implemented", "Unique identification; MFA required for all personnel and admin access.", "Security Officer", "Yes", "Entra ID configuration"),
    ("IA", "IA-4", "Implemented", "Identifier management through Entra ID; no shared accounts.", "Security Officer", "Yes", "AGT-IAM-004"),
    ("IA", "IA-5", "Implemented", "Authenticator management: bcrypt cost 12, breached-password check, no forced expiry.", "Engineering", "No", "AGT-IAM-004 s4.2"),
    ("IA", "IA-8", "Implemented", "Non-organizational users authenticate through the local path with identical controls.", "Engineering", "No", "AGT-IAM-004 s3"),
    ("IR", "IR-1", "Implemented", "Incident response plan established and exercised annually.", "Security Officer", "No", "AGT-IRP-022"),
    ("IR", "IR-4", "Implemented", "Incident handling across the NIST 800-61 lifecycle with scenario playbooks.", "Security Officer", "No", "AGT-IRP-022 s9"),
    ("IR", "IR-5", "Implemented", "Incident tracking with six-year record retention.", "Security Officer", "No", "Incident records"),
    ("IR", "IR-6", "Implemented", "Reporting obligations defined including 24-hour COR notification.", "CEO", "No", "AGT-IRP-022 s8"),
    ("IR", "IR-8", "Implemented", "Plan maintained and updated after each real event.", "Security Officer", "No", "Version history"),
    ("MA", "MA-2", "Inherited", "Physical maintenance of infrastructure performed by Microsoft.", "Microsoft", "Yes", "Azure attestation"),
    ("MA", "MA-4", "Implemented", "Remote maintenance via authenticated, logged administrative access.", "Engineering", "No", "AGT-RAP-005"),
    ("MP", "MP-2", "Implemented", "Media access restricted by classification.", "Security Officer", "No", "AGT-MPP-008"),
    ("MP", "MP-4", "Implemented", "Encrypted storage; Restricted data prohibited on removable media.", "Security Officer", "No", "AGT-MPP-008 s4"),
    ("MP", "MP-6", "Implemented", "NIST 800-88 sanitization with recorded disposal.", "Security Officer", "Partial", "Disposal records"),
    ("MP", "MP-7", "Implemented", "Removable media use restricted and inventoried.", "Security Officer", "No", "AGT-MPP-008 s8"),
    ("PE", "PE-1 to PE-18", "Inherited", "Physical and environmental protection of infrastructure provided by Microsoft Azure.", "Microsoft", "Yes", "Azure SOC 2 / ISO 27001"),
    ("PL", "PL-1", "Implemented", "Security planning policy established.", "CEO", "No", "AGT-EISP-001"),
    ("PL", "PL-2", "Implemented", "This System Security Plan.", "Security Officer", "No", "AGT-SSP-001"),
    ("PL", "PL-4", "Implemented", "Rules of behaviour in the acceptable use policy.", "Security Officer", "No", "AGT-AUP-002"),
    ("PL", "PL-8", "Implemented", "Security architecture documented in section 5 of this plan.", "Engineering", "No", "SSP s5"),
    ("PM", "PM-1", "Implemented", "Information security programme plan established.", "CEO", "No", "AGT-EISP-001"),
    ("PM", "PM-4", "Implemented", "POA&M process with monthly and quarterly review.", "Security Officer", "No", "AGT-CMS-024 s6"),
    ("PM", "PM-5", "Implemented", "System inventory maintained.", "Engineering", "No", "AGT-AMP-011"),
    ("PM", "PM-9", "Implemented", "Risk management strategy defined.", "CEO", "No", "AGT-RMP-023"),
    ("PS", "PS-2", "Implemented", "Position risk designation for privileged roles.", "CEO", "No", "AGT-EISP-001"),
    ("PS", "PS-3", "Partial", "Personnel screening performed; formal re-screening cadence not defined.", "CEO", "No", "POA&M"),
    ("PS", "PS-4", "Implemented", "Termination procedures revoke access same day including session revocation.", "Security Officer", "No", "AGT-IAM-004 s7"),
    ("PS", "PS-5", "Implemented", "Transfer triggers role re-baselining.", "Security Officer", "No", "AGT-IAM-004 s7"),
    ("PS", "PS-6", "Implemented", "Access agreements executed.", "CEO", "No", "AGT-AUP-002"),
    ("PT", "PT-1 to PT-8", "Implemented", "PII processing, transparency, consent, and individual rights.", "Privacy Officer", "No", "AGT-PPF-014"),
    ("RA", "RA-2", "Implemented", "FIPS 199 categorization MODERATE; data classification scheme defined.", "Security Officer", "No", "AGT-DCP-006"),
    ("RA", "RA-3", "Implemented", "Risk assessment performed and refreshed annually.", "Security Officer", "No", "AGT-HIRA-025"),
    ("RA", "RA-5", "Partial", "Nightly scanning across SAST, secrets, SCA and DAST. One scanner non-functional.", "Security Officer", "No", "Scan reports"),
    ("RA", "RA-7", "Implemented", "Risk response documented with acceptance authority and expiry.", "CEO", "No", "AGT-RMP-023 s6"),
    ("SA", "SA-3", "Implemented", "SDLC with security integrated at each phase.", "Engineering", "No", "AGT-SSDLC-015"),
    ("SA", "SA-4", "Partial", "Acquisition requirements defined for vendors; formal component provenance limited.", "Security Officer", "No", "AGT-VRM-012"),
    ("SA", "SA-8", "Implemented", "Security engineering principles applied including least privilege and defence in depth.", "Engineering", "No", "AGT-SSDLC-015 s5"),
    ("SA", "SA-9", "Implemented", "External service providers assessed and contracted.", "Security Officer", "Partial", "AGT-VRM-012"),
    ("SA", "SA-11", "Partial", "Developer testing includes SAST, SCA, DAST. Coverage gap from one scanner.", "Engineering", "No", "Scan reports"),
    ("SA", "SA-15", "Implemented", "Documented development process with security gates.", "Engineering", "No", "AGT-SSDLC-015 s3"),
    ("SC", "SC-7", "Implemented", "Boundary protection: HTTPS only, trusted-host enforcement, strict CORS.", "Engineering", "No", "Configuration"),
    ("SC", "SC-8", "Implemented", "TLS 1.2+ for all transmission; database connection requires SSL.", "Engineering", "Partial", "AGT-CKM-009"),
    ("SC", "SC-12", "Partial", "Key management via Key Vault and managed identity. DATABASE_URL remains a literal.", "Security Officer", "Yes", "POA&M"),
    ("SC", "SC-13", "Implemented", "AES-256 at rest, TLS 1.2+ in transit, approved algorithms only.", "Engineering", "Yes", "AGT-CKM-009 s3"),
    ("SC", "SC-17", "Implemented", "Certificates managed and auto-renewed by Azure.", "Engineering", "Yes", "Azure configuration"),
    ("SC", "SC-28", "Implemented", "Encryption at rest for database, storage, and Key Vault.", "Engineering", "Yes", "Azure configuration"),
    ("SI", "SI-2", "Implemented", "Flaw remediation with severity-based target windows.", "Engineering", "No", "AGT-CMS-024 s6"),
    ("SI", "SI-3", "Implemented", "Malicious code protection on endpoints.", "Security Officer", "Partial", "MDM records"),
    ("SI", "SI-4", "Partial", "Monitoring and alerting configured; no SIEM correlation.", "Security Officer", "No", "AGT-LMP-018 s10"),
    ("SI", "SI-7", "Partial", "Integrity via database constraints; audit log tamper-evidence outstanding.", "Engineering", "No", "POA&M"),
    ("SI", "SI-10", "Implemented", "Input validation at the boundary using typed models.", "Engineering", "No", "Code"),
    ("SI", "SI-12", "Implemented", "Information handling and retention defined.", "Security Officer", "No", "AGT-DRP-007"),
    ("SR", "SR-3", "Implemented", "Supply chain controls through vendor assessment and contracts.", "Security Officer", "No", "AGT-VRM-012"),
    ("SR", "SR-4", "Partial", "Provenance tracked through SBOM; upstream provenance not independently verified.", "Engineering", "No", "SBOM"),
    ("SR", "SR-6", "Implemented", "Supplier assessment annually for Critical and High tiers.", "Security Officer", "No", "Vendor Review records"),
]

FAMILY_NAMES = {
    "AC": "Access Control", "AT": "Awareness and Training", "AU": "Audit and Accountability",
    "CA": "Assessment, Authorization and Monitoring", "CM": "Configuration Management",
    "CP": "Contingency Planning", "IA": "Identification and Authentication",
    "IR": "Incident Response", "MA": "Maintenance", "MP": "Media Protection",
    "PE": "Physical and Environmental Protection", "PL": "Planning",
    "PM": "Program Management", "PS": "Personnel Security",
    "PT": "PII Processing and Transparency", "RA": "Risk Assessment",
    "SA": "System and Services Acquisition",
    "SC": "System and Communications Protection",
    "SI": "System and Information Integrity", "SR": "Supply Chain Risk Management",
}


def build():
    d = AGTDoc("AGT-SSP-001", "System Security Plan", classification="Confidential")

    d.h1("1. Executive summary")
    d.p("This System Security Plan describes the security controls implemented for "
        "DocuAction TEFCA ARC, a healthcare interoperability and document intelligence "
        "platform operated by Alliance Global Tech Inc. on Microsoft Azure. It is the "
        "master security document for the system: every other AGT policy describes how a "
        "class of control is governed, while this plan states how each control is actually "
        "implemented for this system, by whom, and with what evidence.")
    d.p("The system is categorized FIPS 199 MODERATE. Of the controls assessed in section "
        "8, the majority are implemented, a defined set are partially implemented with "
        "tracked remediation, and the physical and environmental family is inherited in "
        "full from Microsoft Azure. Partial implementations are stated as such rather than "
        "rounded up; the value of this plan to an assessor depends on it being an accurate "
        "description rather than an aspirational one.")

    d.h1("2. System identification")
    d.table(["Attribute", "Value"],
            [["System name", "DocuAction TEFCA ARC"],
             ["System owner", "Alliance Global Tech Inc. (AGT)"],
             ["System owner contact", "Imran Siddiqui, Chief Executive Officer"],
             ["Security Officer", "Designated; see AGT-EISP-001"],
             ["Privacy Officer", "Designated; see AGT-PPF-014"],
             ["System type", "Major application, cloud-hosted (SaaS delivery)"],
             ["Operational status", "Operational"],
             ["Cloud service model", "Platform as a Service consumed from Microsoft Azure"],
             ["Deployment model", "Public cloud, single tenant application"],
             ["Primary region", "East US 2 (production), Central US (development)"],
             ["Plan version", "1.0"],
             ["Plan date", "2026-07-28"]],
            widths=[1.8, 4.4])

    d.h1("3. Security categorization (FIPS 199)")
    d.table(["Information type", "Confidentiality", "Integrity", "Availability", "Basis"],
            [["Protected health information", "High", "High", "Moderate", "HIPAA-regulated clinical data"],
             ["Federal interoperability records (TEFCA)", "Moderate", "High", "Moderate", "Contract deliverable; integrity is the product"],
             ["Authentication and authorization data", "High", "High", "High", "Compromise defeats every other control"],
             ["Audit records", "Moderate", "High", "Moderate", "Evidentiary value depends on integrity"],
             ["Published bulletin content", "Low", "Moderate", "Moderate", "Public content with a daily commitment"],
             ["System configuration", "Moderate", "High", "Moderate", "Drift weakens controls silently"]],
            widths=[1.9, 1.1, 0.9, 1.0, 1.3])
    d.p("Overall system categorization: MODERATE. The high-water mark for confidentiality "
        "and integrity is High at the information-type level, driven by PHI and "
        "authentication data; the system categorization is set at MODERATE because the "
        "volume and scope of PHI processed is bounded, the platform is a business associate "
        "rather than a system of record, and the availability impact of an outage is "
        "measured in hours rather than in patient safety. This determination is reviewed "
        "annually and would be revised upward if the platform became the primary clinical "
        "record for any customer.")

    d.h1("4. Authorization boundary")
    d.p("The authorization boundary encloses the components AGT deploys, configures, and is "
        "accountable for. Components outside the boundary are either inherited from "
        "Microsoft under the shared responsibility model or are external services reached "
        "across a documented interface.")
    d.h2("4.1 Inside the boundary")
    d.bullets([
        "Azure App Service instances hosting the FastAPI backend (production and development).",
        "Azure Static Web Apps hosting the Next.js static export (production and development).",
        "Azure Database for PostgreSQL Flexible Server, including the geo-redundant backup.",
        "Azure Key Vault holding application secrets.",
        "Application code, configuration, and deployment pipelines.",
        "Application audit and diagnostic data in Application Insights and Azure Monitor.",
    ])
    d.h2("4.2 Outside the boundary, inherited")
    d.bullets([
        "Azure physical facilities, hardware, hypervisor, and network fabric.",
        "Microsoft Entra ID identity platform.",
        "Azure platform-level encryption at rest and key protection hardware.",
    ])
    d.h2("4.3 Outside the boundary, external interfaces")
    d.bullets([
        "AI inference providers (Anthropic, OpenAI) reached over TLS.",
        "Transactional email provider (SendGrid).",
        "Federal registries consumed read-only (NPPES, PECOS, LEIE, SAM.gov).",
        "News and article data feeds.",
        "GitHub for source control and CI/CD.",
    ])

    d.h1("5. System architecture")
    d.table(["Tier", "Component", "Technology", "Security relevance"],
            [["Presentation", "Static Web App", "Next.js 14 static export", "No server-side execution; no secrets in the bundle"],
             ["API", "App Service (Linux)", "FastAPI on Python 3.12, Gunicorn with Uvicorn workers", "All authorization decisions occur here"],
             ["Data", "PostgreSQL Flexible Server", "PostgreSQL, SSL required", "Encryption at rest; geo-redundant backup"],
             ["Secrets", "Key Vault", "RBAC authorization, soft delete", "Resolved by managed identity at runtime"],
             ["Identity", "Entra ID", "OAuth 2.0, OIDC, MFA", "Federated authentication for AGT personnel"],
             ["Observability", "Application Insights, Azure Monitor", "Managed telemetry", "Audit and alerting substrate"]],
            widths=[1.0, 1.5, 1.9, 1.8])
    d.p("The frontend is a static export with no server-side rendering, which removes an "
        "entire class of server-side vulnerability from the presentation tier and means the "
        "browser bundle can be treated as public by construction. Every authorization "
        "decision is made in the API tier; the frontend's role in access control is "
        "presentational only, and the API assumes a hostile client.")

    d.h1("6. Data flow")
    d.h2("6.1 Primary request flow")
    d.numbered([
        "User authenticates - either through Entra ID (OIDC authorization code with PKCE) "
        "or through the local password path. Both issue the same application JWT.",
        "Browser calls the API over TLS 1.2+ with the token as a bearer credential.",
        "Trusted-host middleware validates the Host header; an unlisted host is rejected "
        "with 400 on every path.",
        "The shared authentication dependency validates the token signature, expiry, "
        "revocation state, and account status.",
        "Router-level authorization dependencies evaluate the role requirement.",
        "For PHI-surface routes, the audit dependency records the access before the handler "
        "executes.",
        "The handler reads or writes PostgreSQL over an SSL-required connection.",
        "Where AI inference is required, only the minimum necessary excerpt is sent to the "
        "provider over TLS.",
        "The response is returned; errors carry a correlation identifier and never a stack "
        "trace.",
    ])
    d.h2("6.2 Secret resolution flow")
    d.numbered([
        "App Service starts and reads its application settings.",
        "Settings containing a Key Vault reference are resolved by the platform using the "
        "system-assigned managed identity.",
        "The application never sees or stores the credential used to obtain the secret.",
    ])
    d.note("An unresolved Key Vault reference is delivered as a literal string of roughly 71 "
           "characters, which is long enough to pass a naive length check on a signing key. "
           "Verification of secret resolution must confirm the application serves correctly, "
           "not merely that it started.")
    d.h2("6.3 PHI flow and its boundary crossing")
    d.p("PHI enters through the case-management and audio endpoints, is processed in memory "
        "in the API tier, may be persisted to PostgreSQL where the customer's configuration "
        "requires it, and - for note generation and transcription - crosses the "
        "authorization boundary to an external AI provider. That crossing is the single "
        "most consequential data flow in this system and is the subject of open findings "
        "F-01 and F-02 in AGT-HIRA-025.")

    d.h1("7. Shared responsibility model")
    d.table(["Layer", "Microsoft", "AGT"],
            [["Physical facilities and hardware", "Full", "None"],
             ["Network infrastructure and DDoS", "Full", "Configuration of exposure"],
             ["Hypervisor and host OS", "Full", "None"],
             ["Guest runtime (App Service)", "Patching of the platform image", "Application dependencies and their currency"],
             ["Database engine", "Patching, backup infrastructure, encryption at rest", "Schema, access, SSL enforcement, backup settings"],
             ["Identity platform", "Entra ID availability and protocol implementation", "Tenant configuration, Conditional Access, MFA policy"],
             ["Key management hardware", "HSM and platform key protection", "Vault access policy, secret rotation, reference correctness"],
             ["Application code", "None", "Full"],
             ["Application data", "Storage durability and encryption", "Classification, retention, access control, disposal"],
             ["Access control decisions", "None", "Full"],
             ["Incident response", "Platform incidents", "Application and data incidents; customer notification"]],
            widths=[1.9, 2.1, 2.2])
    d.p("The line that matters most in practice: Microsoft secures the cloud, AGT secures "
        "what it puts in the cloud. A misconfiguration on AGT's side is not mitigated by the "
        "strength of the underlying platform, and inherited controls do not transfer "
        "accountability - they transfer implementation.")

    d.h1("8. NIST 800-53 control implementation")
    d.p("The table below states, for each control, its implementation status, how it is "
        "implemented for this system, the responsible party, whether it is inherited from "
        "Azure, and where the supporting evidence lives. Statuses are Implemented, Partial, "
        "or Inherited. A Partial entry names what is missing.")
    for fam in sorted({c[0] for c in CONTROLS}, key=lambda f: list(FAMILY_NAMES).index(f)):
        rows = [c for c in CONTROLS if c[0] == fam]
        d.h2(f"8.{list(FAMILY_NAMES).index(fam) + 1} {fam} - {FAMILY_NAMES[fam]}")
        d.table(["Control", "Status", "Implementation", "Responsible", "Inherited", "Evidence"],
                [[r[1], r[2], r[3], r[4], r[5], r[6]] for r in rows],
                widths=[0.7, 0.8, 2.4, 0.9, 0.7, 0.9])

    d.page_break()
    d.h1("9. POA&M summary")
    d.p("Open items with a control impact. The authoritative POA&M is maintained separately "
        "and reviewed monthly; this is a point-in-time summary as of the plan date.")
    d.table(["ID", "Weakness", "Controls affected", "Severity", "Target"],
            [["P-01", "No BAA with two AI providers processing potential PHI", "SA-9, HIPAA 164.308(b)(1)", "High", "90 days"],
             ["P-02", "Clinical audio transmitted without pre-transmission redaction", "SC-8, HIPAA 164.312(e)(1)", "High", "90 days"],
             ["P-03", "One static analysis scanner non-functional; coverage gap in all scores", "RA-5, SA-11", "Moderate", "60 days"],
             ["P-04", "DATABASE_URL held as a literal rather than a Key Vault reference", "SC-12", "Moderate", "60 days"],
             ["P-05", "Audit log lacks tamper-evidence", "AU-9, SI-7", "Moderate", "180 days"],
             ["P-06", "No SIEM correlation; detection depends on manual review", "SI-4, AU-6", "Moderate", "Roadmap"],
             ["P-07", "FTP deployment path enabled, bypassing pipeline verification", "CM-7", "Low", "30 days"],
             ["P-08", "Platform health check not configured", "CP-10, SI-4", "Low", "30 days"],
             ["P-09", "No Key Vault in the development environment", "SC-12", "Low", "90 days"],
             ["P-10", "Independent penetration test not yet performed", "CA-2, CA-8", "Moderate", "Next fiscal year"],
             ["P-11", "Personnel re-screening cadence not formally defined", "PS-3", "Low", "180 days"],
             ["P-12", "No pre-provisioned alternate processing site", "CP-7", "Low", "Accepted with documented procedure"]],
            widths=[0.5, 2.2, 1.5, 0.8, 1.2])

    d.h1("10. Continuous monitoring")
    d.p("Ongoing assurance that these controls remain effective is governed by AGT-CMS-024. "
        "Its cadence, metrics, and suppression governance are incorporated here by "
        "reference. In summary: automated scanning nightly, triage weekly, access and "
        "configuration review quarterly, risk assessment and disaster recovery test "
        "annually, and monthly executive reporting that states what is not working as "
        "prominently as what is.")
    d.p("Two properties of that programme matter for the credibility of this plan. First, "
        "suppressed findings are reported separately from remediated ones, so a score "
        "improved by deferral is never presented as a score improved by repair. Second, "
        "every suppression carries an expiry, so a deferred finding returns for "
        "re-evaluation rather than disappearing.")

    d.h1("11. Plan maintenance")
    d.table(["Trigger", "Action"],
            [["Annual review", "Full review and reissue"],
             ["Material architecture change", "Update sections 4, 5, 6 and affected controls"],
             ["New information type or data class", "Re-evaluate section 3 categorization"],
             ["Control status change", "Update section 8 and the POA&M"],
             ["Security incident with control implications", "Update after the post-incident review"]],
            widths=[2.0, 4.2])

    d.roles([
        ["CEO", "System owner; accepts residual risk; approves this plan."],
        ["Security Officer", "Maintains the plan; tracks control status and the POA&M."],
        ["Privacy Officer", "Maintains PHI scoping and privacy control accuracy."],
        ["Engineering", "Implements and evidences technical controls."],
    ])
    d.page_break()
    d.compliance_mapping(
        [["NIST 800-53", "PL-2", "This document is the system security plan.", "Met"],
         ["NIST 800-18", "Rev. 1", "Guide for developing security plans - structure adopted.", "Met"],
         ["FIPS 199", "Categorization", "MODERATE determination in section 3.", "Met"],
         ["HIPAA", "164.308(a)(1)", "Security management process documented across the plan.", "Met"],
         ["HIPAA", "164.316(a)", "Written policies and procedures maintained.", "Met"],
         ["SOC 2", "CC1-CC9", "Control environment through monitoring, mapped in section 8.", "Met"],
         ["ISO 27001", "Clause 4-10, Annex A", "ISMS scope, controls, and applicability.", "Met"],
         ["CMMI L3", "OPD, OPF, IPM", "Defined process across the engineering lifecycle.", "Met"],
         ["FedRAMP", "MODERATE baseline", "Control implementation described; 3PAO assessment not yet initiated.", "Partial"]])
    d.related([[i, t, r] for i, t, r in [
        ("AGT-MCM-026", "Master Compliance Matrix", "Index from control to policy"),
        ("AGT-HIRA-025", "HIPAA Organizational Risk Assessment", "Risk basis for control selection"),
        ("AGT-RMP-023", "Risk Management Plan", "Risk methodology and register"),
        ("AGT-CMS-024", "Continuous Monitoring Strategy", "Ongoing control assurance"),
        ("AGT-IRP-022", "Incident Response Plan", "IR family implementation"),
        ("AGT-SSDLC-015", "Secure Software Development Lifecycle", "SA family implementation"),
        ("AGT-CfMP-016", "Configuration Management Policy", "CM family implementation"),
        ("AGT-IAM-004", "Identity and Access Management Policy", "AC and IA family implementation"),
    ]])
    d.closing()
    p = d.save(OUT)
    print(f"  AGT-SSP-001      {p.name:64s} {p.stat().st_size/1024:6.1f} KB  ({len(CONTROLS)} controls)")
    return p


if __name__ == "__main__":
    build()
