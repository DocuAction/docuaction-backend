"""Tier 1 policies, batch 1: AGT-IAM-004 .. AGT-CKM-009."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402

OUT = Path(__file__).resolve().parents[1]
BUILT = []


def _std_open(d, purpose, scope_extra=None):
    d.h1("1. Purpose")
    d.p(purpose)
    d.h1("2. Scope")
    d.p("This policy applies to all Alliance Global Tech Inc. (AGT) personnel, "
        "contractors, and third parties who access, operate, or support the DocuAction "
        "TEFCA ARC platform or its supporting Azure infrastructure. It covers production, "
        "development, and any environment holding customer data, protected health "
        "information (PHI), or controlled unclassified information (CUI).")
    if scope_extra:
        d.bullets(scope_extra)
    d.platform_context()


def _finish(d, mapping, related, history=None):
    d.page_break()
    d.compliance_mapping(mapping)
    d.related(related)
    d.closing(history)
    p = d.save(OUT)
    BUILT.append((d.doc_id, d.title, p))
    print(f"  {d.doc_id:16s} {p.name:64s} {p.stat().st_size/1024:6.1f} KB")


# ─────────────────────────── AGT-IAM-004 ───────────────────────────
def iam():
    d = AGTDoc("AGT-IAM-004", "Identity and Access Management Policy")
    _std_open(d, "This policy establishes how identities are created, authenticated, "
                 "authorized, reviewed, and removed across the DocuAction TEFCA ARC "
                 "platform. Identity is the primary security boundary in a cloud system "
                 "with no network perimeter: every control in this document exists because "
                 "an attacker who holds a valid identity is indistinguishable from a user "
                 "unless the identity itself is constrained.")
    d.definitions([
        ["Identity", "A unique digital representation of a person, service, or workload."],
        ["Principal", "An identity as presented to an authorization decision."],
        ["Entra ID", "Microsoft Entra ID, AGT's authoritative identity provider."],
        ["Managed identity", "An Azure-issued service identity with no stored credential."],
        ["JWT", "JSON Web Token; the bearer credential issued after authentication."],
        ["RBAC", "Role-based access control; authorization by assigned role."],
        ["PIM", "Privileged Identity Management; time-bound elevation of privilege."],
        ["Service account", "A non-human identity used by an application or job."],
    ])

    d.h1("3. Identity sources and federation")
    d.p("Entra ID is the authoritative identity provider for AGT personnel. The platform "
        "additionally supports local email/password authentication for external users who "
        "are not members of the AGT tenant. Both paths issue the same application JWT, "
        "which means authorization decisions downstream are identical regardless of how "
        "the user authenticated.")
    d.p("This is a deliberate design decision with a consequence worth stating plainly: "
        "because both paths converge on one token format, a weakness in the local password "
        "path is not contained to local users - it yields the same token an Entra-federated "
        "user would hold. Local authentication therefore carries the same password, "
        "lockout, and session controls as the federated path, not a reduced set.")
    d.table(["Path", "Protocol", "Credential", "Applies to"],
            [["Entra ID SSO", "OAuth 2.0 / OIDC authorization code + PKCE",
              "Entra ID account, MFA enforced", "AGT personnel"],
             ["Local authentication", "Password grant over TLS",
              "bcrypt-hashed password", "External and partner users"],
             ["Service-to-service", "Azure managed identity",
              "Platform-issued, no stored secret", "App Service to Key Vault"]],
            widths=[1.4, 2.0, 1.7, 1.3])

    d.h1("4. Authentication requirements")
    d.h2("4.1 Multi-factor authentication")
    d.bullets([
        "MFA is required for all AGT personnel accounts without exception, enforced by "
        "Entra ID Conditional Access rather than by application logic.",
        "MFA is required for all administrative access to the Azure portal, Azure CLI, "
        "and the platform's admin endpoints.",
        "Approved factors: Microsoft Authenticator (number matching), FIDO2 security key, "
        "or Windows Hello for Business.",
        "SMS and voice call are not approved factors for administrative accounts. They are "
        "vulnerable to SIM-swap and interception, and an administrative account is exactly "
        "the target for which that attack is worth mounting.",
    ])
    d.h2("4.2 Password requirements (local authentication path)")
    d.table(["Requirement", "Setting", "Rationale"],
            [["Minimum length", "12 characters", "Length dominates composition for offline resistance."],
             ["Composition rules", "None mandated", "Enforced complexity drives predictable substitutions."],
             ["Breached-password check", "Required", "Rejects credentials already public."],
             ["Storage", "bcrypt, cost factor 12", "Deliberately slow; resists offline cracking."],
             ["Rotation", "On suspicion of compromise only", "Scheduled rotation degrades password quality."],
             ["Lockout", "Progressive delay after 5 failures", "Blunts credential stuffing without a lockout DoS."]],
            widths=[1.5, 1.6, 3.1])
    d.note("Scheduled password expiry is deliberately not required. NIST SP 800-63B "
           "withdrew that recommendation because forced rotation produces weaker, more "
           "predictable passwords and users who write them down.")

    d.h1("5. Token and session lifecycle")
    d.p("The platform issues a signed JWT on successful authentication. Token handling is "
        "where most identity systems fail in practice, so the lifecycle is specified rather "
        "than left to implementation.")
    d.table(["Property", "Control"],
            [["Signing", "HMAC using a secret of at least 64 characters, held in Azure Key Vault"],
             ["Lifetime", "Short-lived access token; re-authentication required on expiry"],
             ["Transport", "TLS 1.2 or higher only; tokens are never placed in a URL or query string"],
             ["Storage", "Browser storage scoped to the application origin; never a cookie without SameSite"],
             ["Revocation", "Server-side session revocation invalidates tokens before their natural expiry"],
             ["Claims", "Subject, role, issued-at, expiry; no PHI and no PII beyond the user identifier"]],
            widths=[1.5, 4.7])
    d.p("Server-side revocation matters more than token lifetime. A stateless JWT is valid "
        "until it expires no matter what happens to the account behind it, so disablement, "
        "role change, and offboarding are only effective if the platform checks revocation "
        "state on each request. The platform performs this check in the shared "
        "authentication dependency, which also enforces account-disabled and "
        "pending-approval states.")

    d.h1("6. Authorization model")
    d.p("Authorization is role-based, with a strict hierarchy. A role at a given level "
        "inherits the permissions of every level below it.")
    d.table(["Level", "Role", "Typical capability"],
            [["1", "viewer", "Read published content and aggregate dashboards"],
             ["2", "contributor", "Trigger collection, refresh, and analysis operations"],
             ["4", "reviewer", "Front-line review queues and dispositions"],
             ["5", "senior_analyst", "Bucket overrides, escalation queues, calibration"],
             ["6", "qalead", "Delivery and approval, methodology sign-off, all queues"],
             ["max", "admin", "Registry administration, purge operations, user management"]],
            widths=[0.8, 1.5, 3.9])
    d.h2("6.1 Enforcement location")
    d.p("Authorization is enforced at the router level, not inside individual handlers. "
        "This is a deliberate structural choice: a control attached to each handler is a "
        "control that the next handler added will be missing. Router-level dependencies "
        "mean a new endpoint inherits the requirement by existing.")
    d.h2("6.2 Least privilege")
    d.bullets([
        "Access is granted on documented business need, not on convenience or seniority.",
        "Azure role assignments are scoped to the resource group, never to the subscription, "
        "for any principal that does not require subscription-wide reach. A CI service "
        "principal with subscription Contributor is a standing path to every resource owned.",
        "Default deny: an endpoint with no explicit authorization requirement is treated as "
        "a defect, not as an intentional public endpoint, unless it is documented as such.",
    ])

    d.h1("7. Account lifecycle")
    d.table(["Stage", "Trigger", "Action", "Owner", "SLA"],
            [["Provision", "Approved access request", "Create identity, assign minimum role", "Security Officer", "2 business days"],
             ["Modify", "Role change or transfer", "Re-baseline to new role; remove prior role", "Security Officer", "2 business days"],
             ["Disable", "Termination or extended leave", "Disable account, revoke sessions", "Security Officer", "Same day"],
             ["Delete", "Retention period elapsed", "Remove identity; retain audit records", "Security Officer", "Per AGT-DRP-007"]],
            widths=[0.9, 1.5, 1.9, 1.2, 0.9])
    d.p("Disablement revokes active sessions rather than only preventing new logins. An "
        "account disabled without session revocation remains usable for the life of any "
        "token already issued to it, which is precisely the window in which a departing "
        "user acts.")

    d.h1("8. Privileged access")
    d.bullets([
        "Administrative roles are assigned to named individuals. Shared administrative "
        "accounts are prohibited: they destroy attribution, which is the property that "
        "makes an audit log useful.",
        "Privileged access is time-bound through Entra ID Privileged Identity Management "
        "where available, with activation requiring justification and MFA.",
        "Administrative actions are logged to the platform audit log and to Azure activity "
        "logs, which are separate stores; an attacker who compromises the application does "
        "not thereby control the Azure-side record.",
        "Quarterly review of all privileged assignments is mandatory (see AGT-CMS-024).",
    ])

    d.h1("9. Service and workload identities")
    d.p("The platform uses Azure system-assigned managed identity to read secrets from Key "
        "Vault. This removes the bootstrap secret problem: there is no credential to store, "
        "rotate, or leak, because the platform issues and rotates it.")
    d.bullets([
        "Service principals used by CI/CD are scoped to the specific resource groups they "
        "deploy to and hold no standing subscription-level role.",
        "Service account credentials, where unavoidable, are stored in Key Vault and never "
        "in application configuration, source control, or a .env file.",
        "Every non-human identity has a named human owner accountable for its review.",
    ])
    d.note("A known finding is recorded against this control: the production DATABASE_URL "
           "is presently a literal connection string in App Service configuration rather "
           "than a Key Vault reference. Remediation is tracked in the POA&M and documented "
           "in the runbook docs/runbooks/database-url-to-keyvault.md.")

    d.h1("10. Access review")
    d.table(["Review", "Frequency", "Reviewer", "Evidence"],
            [["All user accounts and roles", "Quarterly", "Security Officer", "Quarterly Access Review record"],
             ["Privileged and admin roles", "Quarterly", "CEO", "Quarterly Access Review record"],
             ["Service principals and managed identities", "Quarterly", "Security Officer", "Azure IAM export"],
             ["Azure RBAC assignments", "Quarterly", "Security Officer", "az role assignment list export"]],
            widths=[2.2, 1.0, 1.3, 1.7])

    d.h1("11. Policy violations")
    d.p("Credential sharing, MFA bypass attempts, and unauthorized privilege escalation are "
        "treated as security incidents under AGT-IRP-022, not as administrative matters.")

    d.roles([
        ["Chief Executive Officer", "Approves this policy; approves privileged role assignments."],
        ["Security Officer", "Operates identity lifecycle; performs access reviews; investigates violations."],
        ["Privacy Officer", "Confirms access to PHI is limited to the minimum necessary."],
        ["Engineering", "Implements authorization at the router level; keeps the role hierarchy authoritative."],
        ["All personnel", "Protect credentials; report suspected compromise immediately."],
    ])
    _finish(d,
            [["NIST 800-53", "AC-2, AC-3, AC-5, AC-6", "Account management, access enforcement, separation of duties, least privilege.", "Met"],
             ["NIST 800-53", "IA-2, IA-4, IA-5, IA-8", "Identification and authentication, identifier and authenticator management.", "Met"],
             ["HIPAA", "164.312(a)(1)", "Technical access control - unique user identification and role-based restriction.", "Met"],
             ["HIPAA", "164.312(d)", "Person or entity authentication via Entra ID and MFA.", "Met"],
             ["HIPAA", "164.308(a)(4)", "Information access management and authorization procedures.", "Met"],
             ["SOC 2", "CC6.1, CC6.2, CC6.3", "Logical access provisioning, authentication, and removal.", "Met"],
             ["ISO 27001", "A.5.15, A.5.16, A.5.17, A.5.18", "Access control, identity management, authentication information.", "Met"],
             ["CMMI L3", "OPD, IPM", "Defined organizational process for access administration.", "Met"],
             ["FedRAMP", "AC-2(1), IA-2(1)", "Automated account management and MFA for privileged access.", "Partial - PIM not yet enabled tenant-wide."]],
            [["AGT-EISP-001", "Enterprise Information Security Policy", "Parent policy"],
             ["AGT-ACP-003", "Access Control Policy", "Companion - defines access principles this document operationalizes"],
             ["AGT-RAP-005", "Remote Access Policy", "Extends this policy to remote connectivity"],
             ["AGT-CKM-009", "Cryptographic Key Management Policy", "Governs the signing key and Key Vault"],
             ["AGT-LMP-018", "Logging and Monitoring Policy", "Defines audit capture for identity events"],
             ["AGT-CMS-024", "Continuous Monitoring Strategy", "Defines access review cadence"]])


# ─────────────────────────── AGT-RAP-005 ───────────────────────────
def rap():
    d = AGTDoc("AGT-RAP-005", "Remote Access Policy")
    _std_open(d, "This policy defines the conditions under which AGT systems may be "
                 "accessed from outside a controlled AGT facility. AGT operates as a "
                 "distributed organization on cloud-hosted infrastructure; remote access is "
                 "the normal case, not the exception, and this policy is written on that "
                 "assumption rather than treating remote work as a deviation.")
    d.definitions([
        ["Remote access", "Access to AGT systems from any network not controlled by AGT."],
        ["Split tunneling", "A VPN configuration where only some traffic traverses the tunnel."],
        ["Conditional Access", "Entra ID policy evaluating signals before granting access."],
        ["Managed device", "A device enrolled in AGT device management and meeting policy."],
    ])

    d.h1("3. Access model")
    d.p("There is no corporate network perimeter to enter. AGT systems are Azure-hosted and "
        "reachable from the public internet; the controlling boundary is identity and "
        "device posture, not network location. A VPN would add an encrypted tunnel to "
        "traffic that is already TLS-protected while creating a false sense that being "
        "'inside' confers trust.")
    d.p("Accordingly, remote access is governed by Entra ID Conditional Access and by the "
        "application's own authentication and authorization controls. Where a VPN is "
        "required by a specific customer contract, the requirements in section 6 apply.")

    d.h1("4. Requirements for all remote access")
    d.bullets([
        "Multi-factor authentication is required for every remote session without exception.",
        "TLS 1.2 or higher for all connections; the platform rejects lower versions.",
        "Access from a device meeting the requirements of AGT-MDBYOD-010.",
        "No access to production data from a shared, public, or kiosk device.",
        "Screen lock after no more than 10 minutes of inactivity.",
        "Full-disk encryption on any device used to access AGT systems.",
    ])

    d.h1("5. Conditional Access baseline")
    d.table(["Signal", "Policy", "Action on failure"],
            [["MFA", "Required for all users", "Block"],
             ["Device compliance", "Required for administrative roles", "Block"],
             ["Impossible travel", "Detected by Entra ID Protection", "Require re-authentication"],
             ["Unfamiliar sign-in properties", "Risk-based evaluation", "Require MFA challenge"],
             ["Legacy authentication protocols", "Blocked", "Block"]],
            widths=[1.7, 2.3, 2.2])
    d.note("Legacy authentication is blocked outright because it cannot carry an MFA "
           "challenge. Leaving it enabled makes every other MFA rule optional from an "
           "attacker's point of view.")

    d.h1("6. VPN requirements where contractually required")
    d.bullets([
        "Split tunneling is prohibited. A split tunnel means an infected home network "
        "shares a routing table with a session holding AGT credentials.",
        "VPN authentication requires MFA and uses a certificate or device-bound credential.",
        "Idle sessions terminate after 30 minutes; maximum session duration is 12 hours.",
        "VPN concentrator logs are retained per AGT-LMP-018.",
    ])

    d.h1("7. Administrative and privileged remote access")
    d.bullets([
        "Azure portal and CLI access requires MFA and a compliant device.",
        "Production database access is not permitted from a general-purpose workstation "
        "session without an approved, logged justification.",
        "Deployment to production occurs through the CI/CD pipeline under AGT-ChMP-017. "
        "Direct interactive deployment from a workstation is an exception requiring "
        "documented approval.",
    ])

    d.h1("8. Remote work environment")
    d.bullets([
        "Work in a location where screens displaying PHI or CUI are not visible to others.",
        "Do not use public Wi-Fi for administrative access; use a cellular hotspot or a "
        "trusted network.",
        "Household members and other non-authorized persons must not use a device that "
        "holds AGT credentials.",
        "Printed material containing PHI or CUI must be handled per AGT-MPP-008 and is not "
        "to be disposed of in household waste.",
    ])

    d.h1("9. Monitoring and revocation")
    d.p("Remote sessions are subject to the same logging as any other access "
        "(AGT-LMP-018). Anomalous sign-in patterns trigger review under AGT-IRP-022. "
        "Remote access is revoked immediately on termination through account disablement "
        "and session revocation, as specified in AGT-IAM-004 section 7.")

    d.roles([
        ["Security Officer", "Maintains Conditional Access policy; reviews anomalous sign-ins."],
        ["Engineering", "Ensures the platform enforces TLS and rejects legacy protocols."],
        ["All personnel", "Comply with device and environment requirements; report loss or compromise."],
    ])
    _finish(d,
            [["NIST 800-53", "AC-17, AC-17(1)-(4)", "Remote access authorization, monitoring, encryption, privileged commands.", "Met"],
             ["NIST 800-53", "AC-19, SC-8", "Access control for mobile devices; transmission confidentiality.", "Met"],
             ["HIPAA", "164.312(e)(1)", "Transmission security for ePHI over open networks.", "Met"],
             ["HIPAA", "164.308(a)(4)(ii)(B)", "Access authorization for remote workforce members.", "Met"],
             ["SOC 2", "CC6.6, CC6.7", "Boundary protection and transmission of data.", "Met"],
             ["ISO 27001", "A.6.7, A.8.1", "Remote working; user endpoint devices.", "Met"],
             ["FedRAMP", "AC-17(2)", "Cryptographic protection of remote access sessions.", "Met"]],
            [["AGT-IAM-004", "Identity and Access Management Policy", "Defines the identity controls this policy relies on"],
             ["AGT-MDBYOD-010", "Mobile Device and BYOD Policy", "Defines device requirements referenced here"],
             ["AGT-ACP-003", "Access Control Policy", "Parent access control principles"],
             ["AGT-IRP-022", "Incident Response Plan", "Handles anomalous remote access events"]])


# ─────────────────────────── AGT-DCP-006 ───────────────────────────
def dcp():
    d = AGTDoc("AGT-DCP-006", "Data Classification Policy")
    _std_open(d, "This policy establishes how AGT classifies information so that "
                 "protection is proportionate to sensitivity. Classification is the "
                 "prerequisite for every other data control: retention, encryption, "
                 "handling, and disposal all reference the class, and a system that cannot "
                 "say what class a record belongs to cannot demonstrate it is protecting it "
                 "correctly.")
    d.definitions([
        ["PHI", "Protected Health Information as defined by HIPAA 45 CFR 160.103."],
        ["PII", "Personally Identifiable Information."],
        ["CUI", "Controlled Unclassified Information as defined by 32 CFR 2002."],
        ["Data owner", "The role accountable for classification and access decisions."],
        ["Data custodian", "The role operating the systems that store or process the data."],
    ])

    d.h1("3. Classification levels")
    d.table(["Level", "Definition", "Examples in this platform", "Impact if disclosed"],
            [["Public", "Approved for unrestricted release.",
              "Published FCC bulletin content, marketing material, public API docs.", "None"],
             ["Internal", "Routine business information; not for public release.",
              "Architecture documents, non-sensitive configuration, internal runbooks.", "Low"],
             ["Confidential", "Sensitive business or personal information.",
              "User accounts, audit logs, vendor contracts, source code.", "Moderate"],
             ["Restricted", "Highest sensitivity; regulated or contractually protected.",
              "PHI, PII, CUI, credentials, cryptographic keys, TEFCA participant records.", "High"]],
            widths=[0.9, 1.7, 2.2, 1.0])
    d.note("Where a record could fall into two classes, the higher class applies. A dataset "
           "takes the classification of its most sensitive element - de-identification is a "
           "deliberate, documented process, not an assumption.")

    d.h1("4. Handling requirements by level")
    d.table(["Control", "Public", "Internal", "Confidential", "Restricted"],
            [["Encryption in transit", "TLS 1.2+", "TLS 1.2+", "TLS 1.2+", "TLS 1.2+"],
             ["Encryption at rest", "Optional", "Required", "Required", "Required, AES-256"],
             ["Access control", "None", "Authenticated", "Role-based", "Role-based, minimum necessary"],
             ["Audit logging", "No", "Access to systems", "Access to records", "Every access, per AGT-LMP-018"],
             ["Emailing externally", "Permitted", "Permitted", "Encrypted only", "Prohibited"],
             ["Removable media", "Permitted", "Permitted", "Encrypted only", "Prohibited"],
             ["Cloud AI processing", "Permitted", "Permitted", "Approved vendors only", "Prohibited without BAA"],
             ["Disposal", "Standard", "Standard", "Secure deletion", "Secure deletion + record"]],
            widths=[1.5, 0.9, 0.9, 1.2, 1.7])

    d.h1("5. PHI-specific requirements")
    d.p("PHI is always Restricted. The platform's PHI surface is the case-management module "
        "and the audio transcription pipeline.")
    d.bullets([
        "Access to PHI is limited to the minimum necessary for the role (HIPAA 164.502(b)).",
        "Every access to a PHI-bearing endpoint is recorded in the audit log, which captures "
        "who, what route, when, and source address - and deliberately does not capture "
        "request bodies or query strings, because an audit log that contains PHI is itself "
        "a breach surface.",
        "PHI must not be transmitted to any AI provider that has not executed a Business "
        "Associate Agreement. This is a live constraint, not a hypothetical: see "
        "AGT-AIGOV-013 and the vendor register in AGT-VRM-012.",
        "PHI is not permitted in log messages, error responses, URLs, or ticket systems.",
    ])

    d.h1("6. Labeling")
    d.bullets([
        "Documents carry their classification in the footer of every page.",
        "Data stores are labeled at the schema or container level; a table holding any "
        "Restricted column is treated as Restricted in its entirety.",
        "Source files handling Restricted data carry a module docstring stating so.",
        "Azure resources holding Restricted data are tagged dataClassification=Restricted.",
    ])

    d.h1("7. Declassification and de-identification")
    d.p("Data may be reclassified downward only through a documented process. For PHI this "
        "means de-identification under 45 CFR 164.514 by either the Safe Harbor method "
        "(removal of all 18 identifiers) or Expert Determination. Aggregate statistics "
        "derived from PHI are not automatically de-identified: small cell sizes can "
        "re-identify individuals, and any published aggregate must be reviewed for that "
        "risk before release.")

    d.roles([
        ["Data Owner (CEO)", "Approves classification of new data types."],
        ["Privacy Officer", "Determines PHI classification; approves de-identification."],
        ["Security Officer", "Verifies handling controls match classification."],
        ["Engineering", "Implements labeling, encryption, and audit capture."],
        ["All personnel", "Classify data they create; handle per its level."],
    ])
    _finish(d,
            [["NIST 800-53", "RA-2, MP-3, SC-28", "Security categorization, media marking, protection at rest.", "Met"],
             ["HIPAA", "164.502(b)", "Minimum necessary standard for PHI use and disclosure.", "Met"],
             ["HIPAA", "164.514", "De-identification standard and re-identification risk.", "Met"],
             ["SOC 2", "CC3.2, C1.1", "Risk identification; confidential information handling.", "Met"],
             ["ISO 27001", "A.5.12, A.5.13", "Classification and labelling of information.", "Met"],
             ["CMMI L3", "OPD", "Defined organizational asset classification process.", "Met"],
             ["FedRAMP", "RA-2", "Security categorization consistent with FIPS 199 MODERATE.", "Met"]],
            [["AGT-DRP-007", "Data Retention Policy", "Retention schedules keyed to these levels"],
             ["AGT-MPP-008", "Media Protection Policy", "Media handling by classification"],
             ["AGT-CKM-009", "Cryptographic Key Management Policy", "Encryption requirements referenced here"],
             ["AGT-AIGOV-013", "AI Governance Policy", "Restricts which classes may reach AI providers"],
             ["AGT-PPF-014", "Privacy Policy Framework", "Privacy handling of PII and PHI"]])


# ─────────────────────────── AGT-DRP-007 ───────────────────────────
def drp7():
    d = AGTDoc("AGT-DRP-007", "Data Retention Policy")
    _std_open(d, "This policy defines how long AGT retains each category of information and "
                 "how it is disposed of at end of life. Retention is a two-sided control: "
                 "keeping data longer than required enlarges the breach surface and the "
                 "discovery burden, while destroying it early can breach a regulatory or "
                 "contractual obligation. Both failures are treated here as failures.")
    d.h1("3. Retention schedule")
    d.table(["Data category", "Retention period", "Basis", "Disposal method"],
            [["HIPAA-required documentation", "6 years from creation or last effective date",
              "45 CFR 164.316(b)(2)", "Secure deletion + record"],
             ["Audit and access logs", "6 years", "HIPAA 164.316(b)(2); NIST AU-11", "Secure deletion"],
             ["PHI in case-management records", "Per customer contract; minimum 6 years",
              "HIPAA and contract", "Secure deletion + record"],
             ["Security incident records", "6 years from closure", "HIPAA; NIST IR-5", "Secure deletion"],
             ["Risk assessments and SSP", "Life of system + 3 years", "NIST; FedRAMP", "Secure deletion"],
             ["User account records", "Duration of relationship + 6 years", "HIPAA; contract", "Secure deletion"],
             ["Application and diagnostic logs", "90 days hot, 1 year archive",
              "Operational need", "Automatic expiry"],
             ["Database backups", "35 days point-in-time; geo-redundant", "Operational RPO", "Automatic expiry"],
             ["Source code and build artifacts", "Life of system", "Operational", "Repository deletion"],
             ["Vendor contracts and BAAs", "Term + 6 years", "HIPAA 164.308(b)", "Secure deletion"],
             ["FCC bulletin published content", "Indefinite", "Public content, business value", "N/A"],
             ["Email and business correspondence", "3 years", "Business need", "Automatic expiry"]],
            widths=[1.9, 1.4, 1.5, 1.4])
    d.note("The 6-year floor recurs because HIPAA 164.316(b)(2) requires retention of "
           "policies, procedures, and required documentation for six years from creation or "
           "last effective date - whichever is later. 'Last effective date' means a policy "
           "revised in 2026 starts its six years in 2026, not at original authorship.")

    d.h1("4. Legal hold")
    d.p("A legal hold suspends all disposal for the data in scope and overrides every "
        "schedule in this document.")
    d.numbered([
        "Legal counsel or the CEO issues the hold in writing, identifying scope and custodians.",
        "The Security Officer suspends automated expiry for the affected stores and records "
        "the suspension.",
        "Custodians are notified and acknowledge in writing.",
        "The hold is reviewed quarterly and released only in writing.",
        "On release, normal retention resumes; data already past schedule is disposed of "
        "and the disposal is recorded.",
    ])
    d.p("Automated expiry is the usual point of failure here. A hold that is issued to "
        "people but not applied to the Azure retention setting will be silently defeated by "
        "the platform doing exactly what it was configured to do.")

    d.h1("5. Azure retention configuration")
    d.table(["Store", "Setting", "Configured value"],
            [["Azure PostgreSQL", "Point-in-time restore window", "35 days"],
             ["Azure PostgreSQL", "Geo-redundant backup", "Enabled (must be set at server creation)"],
             ["Application Insights", "Data retention", "90 days default; extended archive for audit data"],
             ["Azure Monitor logs", "Workspace retention", "Configured to meet the 6-year audit requirement"],
             ["Blob storage", "Lifecycle management", "Tier to cool at 90 days; delete per schedule"]],
            widths=[1.6, 1.9, 2.7])
    d.note("Geo-redundant backup on Azure Database for PostgreSQL Flexible Server can only "
           "be enabled at server creation time. It cannot be turned on later. Any migration "
           "or rebuild is the only opportunity to correct this, which makes it a "
           "cutover-time checklist item rather than a backlog item.")

    d.h1("6. Disposal")
    d.bullets([
        "Electronic media: cryptographic erase where the medium is encrypted, otherwise "
        "NIST SP 800-88 purge.",
        "Cloud data: deletion through the platform API, relying on Azure's media "
        "sanitization for the underlying storage (inherited control).",
        "Paper: cross-cut shredding.",
        "Disposal of Restricted data is recorded: what, when, method, and by whom.",
    ])

    d.roles([
        ["CEO / Legal", "Issues and releases legal holds."],
        ["Privacy Officer", "Determines retention for PHI categories."],
        ["Security Officer", "Configures and verifies retention settings; records disposal."],
        ["Engineering", "Implements automated expiry consistent with this schedule."],
    ])
    _finish(d,
            [["NIST 800-53", "AU-11, SI-12, MP-6", "Audit record retention, information handling, media sanitization.", "Met"],
             ["HIPAA", "164.316(b)(2)", "Six-year retention of required documentation.", "Met"],
             ["HIPAA", "164.310(d)(2)(i)", "Disposal of media containing ePHI.", "Met"],
             ["SOC 2", "CC6.5, C1.2", "Disposal of data; confidential information retention.", "Met"],
             ["ISO 27001", "A.5.33, A.8.10", "Protection of records; information deletion.", "Met"],
             ["FedRAMP", "AU-11", "Audit record retention consistent with MODERATE baseline.", "Met"]],
            [["AGT-DCP-006", "Data Classification Policy", "Defines the categories retained here"],
             ["AGT-MPP-008", "Media Protection Policy", "Disposal method detail"],
             ["AGT-BKP-019", "Backup Policy", "Backup retention interacts with this schedule"],
             ["AGT-LMP-018", "Logging and Monitoring Policy", "Log retention requirements"]])


# ─────────────────────────── AGT-MPP-008 ───────────────────────────
def mpp():
    d = AGTDoc("AGT-MPP-008", "Media Protection Policy")
    _std_open(d, "This policy governs the protection, handling, transport, sanitization, "
                 "and disposal of media that stores AGT information. AGT is a cloud-native "
                 "organization and holds very little physical media; the controls here are "
                 "correspondingly weighted toward endpoint storage and removable media, "
                 "which is where the residual exposure actually sits.")
    d.h1("3. Media in scope")
    d.table(["Media type", "Present at AGT", "Primary control"],
            [["Cloud storage (Azure)", "Yes - primary", "Azure Storage Service Encryption, inherited"],
             ["Endpoint internal drives", "Yes", "Full-disk encryption (BitLocker / FileVault)"],
             ["Removable USB media", "Discouraged; exception only", "Encryption required; approval required"],
             ["Optical media", "No", "Prohibited for Restricted data"],
             ["Backup tape", "No", "Not used; Azure-managed backup only"],
             ["Paper", "Minimal", "Locked storage; cross-cut shred at disposal"],
             ["Mobile devices", "Yes", "AGT-MDBYOD-010"]],
            widths=[1.7, 1.6, 2.9])

    d.h1("4. Removable media controls")
    d.bullets([
        "Removable media must not hold Restricted data. There is no approved workflow that "
        "requires PHI on a USB drive, and the ones that appear to are better solved with a "
        "shared, access-controlled location.",
        "Where removable media is used for Confidential data, it must be encrypted with "
        "AES-256 and registered with the Security Officer.",
        "Auto-run and auto-mount of removable media are disabled on managed endpoints.",
        "Unknown or found media is never connected to an AGT device. It is surrendered to "
        "the Security Officer.",
    ])

    d.h1("5. Encryption of media at rest")
    d.table(["Location", "Mechanism", "Key custody"],
            [["Azure PostgreSQL", "Service-managed encryption at rest", "Microsoft-managed"],
             ["Azure Storage / Blob", "Storage Service Encryption, AES-256", "Microsoft-managed"],
             ["Azure Key Vault", "HSM-backed key protection", "Microsoft-managed, AGT-controlled access"],
             ["Endpoint drives", "BitLocker (Windows) / FileVault (macOS)", "Escrowed to Entra ID"],
             ["Removable media (exception)", "AES-256 container", "AGT Security Officer"]],
            widths=[1.7, 2.3, 2.2])

    d.h1("6. Transport")
    d.bullets([
        "Restricted data is not transported on physical media. Transfer occurs over TLS "
        "through the platform or an approved managed file transfer.",
        "Where physical transport is unavoidable and approved, media is encrypted, "
        "hand-carried or sent by tracked courier, and the transfer is logged with sender, "
        "recipient, and confirmation of receipt.",
    ])

    d.h1("7. Sanitization and disposal")
    d.p("Sanitization follows NIST SP 800-88 Rev. 1.")
    d.table(["Medium", "Method", "Verification"],
            [["Encrypted SSD or drive", "Cryptographic erase (destroy the key)", "Key destruction recorded"],
             ["Unencrypted drive", "Purge (ATA secure erase or degauss)", "Tool output retained"],
             ["Failed or unreadable drive", "Physical destruction (shred or disintegrate)", "Certificate of destruction"],
             ["Cloud storage", "API deletion; underlying sanitization inherited from Azure", "Deletion log"],
             ["Paper", "Cross-cut shred", "Witnessed for Restricted"]],
            widths=[1.7, 2.5, 2.0])
    d.p("Cryptographic erase is the preferred method for encrypted media because it is fast "
        "and verifiable, but it is only valid if the medium was encrypted before any "
        "Restricted data was written to it. Encrypting a drive that already holds "
        "unencrypted PHI does not make prior sectors unrecoverable.")

    d.h1("8. Media inventory")
    d.p("Removable media approved for AGT use is inventoried, with owner, encryption "
        "status, classification of data held, and disposal date. The inventory is reviewed "
        "annually and reconciled with the asset register in AGT-AMP-011.")

    d.roles([
        ["Security Officer", "Approves removable media exceptions; maintains inventory; records disposal."],
        ["All personnel", "Do not place Restricted data on removable media; surrender found media."],
        ["Engineering", "Maintain endpoint encryption enforcement and auto-run restrictions."],
    ])
    _finish(d,
            [["NIST 800-53", "MP-2, MP-4, MP-5, MP-6, MP-7", "Media access, storage, transport, sanitization, use.", "Met"],
             ["NIST 800-88", "Rev. 1", "Sanitization methods adopted verbatim.", "Met"],
             ["HIPAA", "164.310(d)(1)", "Device and media controls.", "Met"],
             ["HIPAA", "164.310(d)(2)(i)-(ii)", "Disposal and media re-use.", "Met"],
             ["SOC 2", "CC6.5", "Disposal of physical and logical media.", "Met"],
             ["ISO 27001", "A.7.10, A.7.14", "Storage media; secure disposal or re-use of equipment.", "Met"],
             ["FedRAMP", "MP-6(1)", "Review and verification of sanitization actions.", "Met"]],
            [["AGT-DCP-006", "Data Classification Policy", "Determines handling by class"],
             ["AGT-DRP-007", "Data Retention Policy", "Determines when disposal occurs"],
             ["AGT-AMP-011", "Asset Management Policy", "Media inventory reconciliation"],
             ["AGT-MDBYOD-010", "Mobile Device and BYOD Policy", "Mobile media controls"]])


# ─────────────────────────── AGT-CKM-009 ───────────────────────────
def ckm():
    d = AGTDoc("AGT-CKM-009", "Cryptographic Key Management Policy")
    _std_open(d, "This policy defines approved cryptography and the lifecycle of the keys "
                 "that make it meaningful. Correct algorithm selection is the easy part; "
                 "nearly all real cryptographic failures are key management failures - a "
                 "key in source control, a key that was never rotated, or a key whose "
                 "compromise nobody would detect.")
    d.definitions([
        ["Key Vault", "Azure Key Vault; AGT's managed secret and key store."],
        ["Managed identity", "An Azure-issued identity used to authenticate to Key Vault without a stored secret."],
        ["Key rotation", "Replacing a key with a new one and retiring the old."],
        ["Cryptographic erase", "Rendering data unrecoverable by destroying its encryption key."],
    ])

    d.h1("3. Approved algorithms")
    d.table(["Purpose", "Approved", "Minimum strength", "Prohibited"],
            [["Encryption at rest", "AES-GCM, AES-CBC", "AES-256", "DES, 3DES, RC4, Blowfish"],
             ["Encryption in transit", "TLS 1.2, TLS 1.3", "TLS 1.2", "SSL v2/v3, TLS 1.0, TLS 1.1"],
             ["Hashing (integrity)", "SHA-256, SHA-384, SHA-512", "SHA-256", "MD5, SHA-1"],
             ["Password storage", "bcrypt, Argon2id", "bcrypt cost 12", "Unsalted hashes, SHA-family alone"],
             ["Token signing", "HMAC-SHA256, RS256", "256-bit key", "'none' algorithm, HS256 with a short key"],
             ["Key exchange", "ECDHE, DHE", "P-256 / 2048-bit", "Static RSA key exchange"],
             ["Random generation", "OS CSPRNG", "n/a", "Language default PRNG for security purposes"]],
            widths=[1.5, 1.7, 1.3, 1.7])
    d.note("A JWT signing secret shorter than the hash output undermines HMAC-SHA256 "
           "regardless of the algorithm's strength. The platform enforces a 64-character "
           "minimum on SECRET_KEY at startup and refuses to run below it.")

    d.h1("4. Key storage")
    d.p("All application secrets and keys are held in Azure Key Vault and resolved at "
        "runtime by the App Service system-assigned managed identity. No key is stored in "
        "source control, in a .env file committed to a repository, in application "
        "configuration as a literal, or in a CI variable that is not a protected secret.")
    d.table(["Secret", "Storage", "Status"],
            [["SECRET_KEY (JWT signing)", "Key Vault reference", "Implemented"],
             ["ANTHROPIC_API_KEY", "Key Vault reference", "Implemented"],
             ["AZURE_AD_CLIENT_SECRET", "Key Vault reference", "Implemented"],
             ["SENDGRID_API_KEY", "Key Vault reference", "Implemented"],
             ["DATABASE_URL", "App Service configuration literal", "Open finding - see POA&M"]],
            widths=[2.0, 2.2, 2.0])
    d.p("The failure mode of a Key Vault reference deserves specific attention. An "
        "unresolved reference is delivered to the application as the literal string "
        "@Microsoft.KeyVault(VaultName=...;SecretName=...), which is roughly 71 characters "
        "- long enough to satisfy a naive length check on a signing key. Verification of a "
        "reference must therefore confirm the application is serving correctly, not merely "
        "that it started.")

    d.h1("5. Key lifecycle")
    d.table(["Stage", "Requirement"],
            [["Generation", "Generated by Key Vault or an OS CSPRNG; never derived from a passphrase."],
             ["Distribution", "Never distributed. Access is granted to an identity, not handed to a person."],
             ["Storage", "Key Vault, HSM-backed where the key protects Restricted data."],
             ["Use", "Retrieved at runtime by managed identity; never logged, never echoed in errors."],
             ["Rotation", "Per the schedule in section 6, and immediately on suspected compromise."],
             ["Revocation", "Disable in Key Vault; confirm no principal retains a cached copy."],
             ["Destruction", "Purge-protected soft delete for the retention window, then purge."]],
            widths=[1.3, 4.9])

    d.h1("6. Rotation schedule")
    d.table(["Key or secret", "Rotation", "Trigger for immediate rotation"],
            [["JWT signing key (SECRET_KEY)", "Annually", "Suspected exposure; personnel departure with access"],
             ["Database credentials", "Annually", "Exposure in a log, ticket, or repository"],
             ["Third-party API keys", "Annually", "Vendor breach notification; key seen in plaintext"],
             ["Azure service principal credentials", "Annually", "Departure of the owner; workflow log made public"],
             ["TLS certificates", "Managed by Azure, auto-renewed", "Revocation or private key exposure"]],
            widths=[2.0, 1.5, 2.7])
    d.p("Rotating the JWT signing key invalidates every token issued under the previous "
        "key, logging all users out. This is a deliberate consequence and is the intended "
        "behaviour after a suspected compromise; it should be scheduled outside business "
        "hours for routine rotation.")

    d.h1("7. Certificate management")
    d.bullets([
        "TLS certificates for custom domains are Azure-managed and renew automatically.",
        "Certificate expiry is monitored; a certificate within 30 days of expiry raises an "
        "alert (AGT-LMP-018).",
        "Self-signed certificates are not permitted for any externally reachable endpoint.",
    ])

    d.h1("8. Known exceptions")
    d.table(["Exception", "Risk", "Compensating control", "Target"],
            [["DATABASE_URL held as a literal", "Anyone with Reader on the resource group can read the database password",
              "Reader is tightly restricted; database enforces ssl=require; access reviewed quarterly",
              "Next maintenance window"],
             ["Local .env contains a live API key on a developer workstation", "Credential exposure if the endpoint is compromised",
              "Full-disk encryption; key is non-production scope where possible", "Rotate and move to Key Vault"]],
            widths=[1.6, 1.6, 2.0, 1.0])

    d.roles([
        ["Security Officer", "Owns Key Vault; performs and records rotation; approves exceptions."],
        ["Engineering", "Consumes secrets via managed identity; ensures no secret reaches source control."],
        ["CEO", "Approves exceptions with a documented expiry."],
    ])
    _finish(d,
            [["NIST 800-53", "SC-12, SC-13, SC-17, SC-28", "Key establishment and management, cryptographic protection, PKI, protection at rest.", "Met"],
             ["FIPS", "140-2 / 140-3", "Azure Key Vault HSM-backed keys use validated modules.", "Inherited"],
             ["HIPAA", "164.312(a)(2)(iv)", "Encryption and decryption of ePHI.", "Met"],
             ["HIPAA", "164.312(e)(2)(ii)", "Encryption of ePHI in transit.", "Met"],
             ["SOC 2", "CC6.1, CC6.7", "Logical access and transmission protection.", "Met"],
             ["ISO 27001", "A.8.24", "Use of cryptography.", "Met"],
             ["FedRAMP", "SC-12(1), SC-13", "Key management and FIPS-validated cryptography.", "Partial - DATABASE_URL exception open."]],
            [["AGT-DCP-006", "Data Classification Policy", "Determines what must be encrypted"],
             ["AGT-IAM-004", "Identity and Access Management Policy", "Managed identity access to Key Vault"],
             ["AGT-CfMP-016", "Configuration Management Policy", "Secret configuration baselines"],
             ["AGT-IRP-022", "Incident Response Plan", "Key compromise response"]])


if __name__ == "__main__":
    iam(); rap(); dcp(); drp7(); mpp(); ckm()
    print(f"  batch 1 complete: {len(BUILT)} documents")
