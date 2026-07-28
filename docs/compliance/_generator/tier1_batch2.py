"""Tier 1 policies, batch 2: AGT-MDBYOD-010 .. AGT-PPF-014."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402
from tier1_batch1 import _std_open, _finish, BUILT  # noqa: E402

OUT = Path(__file__).resolve().parents[1]


def mdm():
    d = AGTDoc("AGT-MDBYOD-010", "Mobile Device and BYOD Policy")
    _std_open(d, "This policy defines the requirements a device must meet before it is used "
                 "to access AGT systems or data, whether the device is AGT-issued or "
                 "personally owned. AGT permits personally owned devices because forbidding "
                 "them produces shadow usage rather than compliance; the trade is that a "
                 "personal device carrying AGT data accepts AGT controls over the portion "
                 "that holds that data.")
    d.definitions([
        ["BYOD", "Bring Your Own Device; a personally owned device used for work."],
        ["MDM", "Mobile Device Management; centralized enforcement of device policy."],
        ["Containerization", "Isolation of work data in a managed area of the device."],
        ["Remote wipe", "Erasure of managed data on a device, initiated centrally."],
    ])

    d.h1("3. Device requirements")
    d.table(["Requirement", "AGT-issued", "BYOD", "Notes"],
            [["Full-disk encryption", "Required", "Required", "BitLocker, FileVault, or platform default"],
             ["Screen lock", "Required, <=10 min", "Required, <=10 min", "Biometric or 6+ digit PIN"],
             ["OS currency", "Vendor-supported version", "Vendor-supported version", "Devices past end-of-support are blocked"],
             ["Security updates", "Within 14 days", "Within 30 days", "Critical patches within 7 days"],
             ["MDM enrolment", "Required", "Required for PHI access", "Conditional Access enforces"],
             ["Anti-malware", "Required", "Recommended", "Platform-native acceptable"],
             ["Jailbreak / root", "Prohibited", "Prohibited", "Detected devices are blocked"],
             ["Shared use", "Prohibited", "Work container not shared", "No family use of the work profile"]],
            widths=[1.5, 1.2, 1.2, 2.3])

    d.h1("4. Containerization and data separation")
    d.p("On BYOD, AGT data is confined to a managed work profile or application container. "
        "AGT does not read, index, or wipe personal data, and the technical boundary is "
        "what makes that promise credible rather than merely stated.")
    d.bullets([
        "Copy and paste from the work container to personal applications is restricted.",
        "AGT data is not backed up to a personal cloud account.",
        "Screenshots within the work container may be restricted where PHI is displayed.",
        "Remote wipe removes only the work container on a BYOD device.",
    ])

    d.h1("5. PHI on mobile devices")
    d.bullets([
        "PHI must not be stored locally on a mobile device. Access is permitted through the "
        "application, which holds no persistent local copy.",
        "PHI must not be captured in a photograph, screenshot, or personal note application.",
        "A device used to access PHI must be MDM-enrolled and encrypted without exception.",
    ])

    d.h1("6. Loss, theft, and departure")
    d.numbered([
        "Report loss or theft to the Security Officer immediately, and in no case later "
        "than 24 hours after discovery.",
        "The Security Officer revokes sessions and initiates a remote wipe of the work "
        "container.",
        "The event is assessed under AGT-IRP-022 for breach-notification obligations. A "
        "lost encrypted device with no evidence of access is generally not a reportable "
        "breach under the HIPAA safe harbour; an unencrypted one generally is. That "
        "distinction is the entire practical value of the encryption requirement.",
        "On termination, the work container is wiped and enrolment removed before the final "
        "day of access.",
    ])

    d.h1("7. Acceptable use on mobile")
    d.p("The Acceptable Use Policy (AGT-AUP-002) applies in full to mobile devices. Use of "
        "unapproved cloud storage, messaging applications, or AI assistants to process AGT "
        "data is prohibited regardless of the device's ownership.")

    d.roles([
        ["Security Officer", "Operates MDM; approves device exceptions; executes remote wipe."],
        ["All personnel", "Maintain device compliance; report loss within 24 hours."],
        ["Privacy Officer", "Assesses lost-device events for breach notification."],
    ])
    _finish(d,
            [["NIST 800-53", "AC-19, AC-20, MP-7", "Access control for mobile devices, use of external systems, media use.", "Met"],
             ["HIPAA", "164.310(d)(1)", "Device and media controls for mobile endpoints.", "Met"],
             ["HIPAA", "164.312(a)(2)(iv)", "Encryption as the safe-harbour control for lost devices.", "Met"],
             ["SOC 2", "CC6.7", "Restriction of data movement to and from endpoints.", "Met"],
             ["ISO 27001", "A.8.1, A.7.9", "User endpoint devices; security of assets off-premises.", "Met"],
             ["FedRAMP", "AC-19(5)", "Full-device or container-based encryption.", "Met"]],
            [["AGT-AUP-002", "Acceptable Use Policy", "Applies in full to mobile use"],
             ["AGT-RAP-005", "Remote Access Policy", "Device requirements for remote access"],
             ["AGT-MPP-008", "Media Protection Policy", "Media handling on devices"],
             ["AGT-IRP-022", "Incident Response Plan", "Lost or stolen device response"]])


def amp():
    d = AGTDoc("AGT-AMP-011", "Asset Management Policy")
    _std_open(d, "This policy establishes how AGT identifies, records, owns, and retires "
                 "its information assets. An asset inventory is the substrate every other "
                 "control depends on: vulnerability management, configuration management, "
                 "and incident response all begin with the question of what exists, and an "
                 "incomplete answer silently reduces the coverage of each.")
    d.h1("3. Asset categories")
    d.table(["Category", "Examples", "Inventory source", "Owner"],
            [["Cloud compute", "Azure App Service (prod, dev)", "Azure Resource Graph", "Engineering"],
             ["Cloud data", "Azure PostgreSQL Flexible Server", "Azure Resource Graph", "Engineering"],
             ["Cloud static hosting", "Azure Static Web Apps (prod, dev)", "Azure Resource Graph", "Engineering"],
             ["Secret stores", "Azure Key Vault", "Azure Resource Graph", "Security Officer"],
             ["Identity", "Entra ID tenant, service principals, managed identities", "Entra ID export", "Security Officer"],
             ["Software components", "Python and npm dependencies", "SBOM (CycloneDX)", "Engineering"],
             ["Source repositories", "GitHub - docuaction-backend, docuaction-frontend", "GitHub API", "Engineering"],
             ["Endpoints", "Workstations and mobile devices", "MDM inventory", "Security Officer"],
             ["Third-party services", "AI providers, email, data feeds", "Vendor register (AGT-VRM-012)", "Security Officer"]],
            widths=[1.4, 2.0, 1.6, 1.2])

    d.h1("4. Software bill of materials")
    d.p("A CycloneDX SBOM is generated for both the backend and frontend on every full "
        "security scan and is retained as evidence. The SBOM is the authoritative component "
        "inventory; a dependency that does not appear in it is not being assessed for "
        "vulnerabilities.")
    d.bullets([
        "SBOM generation is automated as part of the security platform's scan pipeline.",
        "SBOMs are retained for the life of the release they describe.",
        "A build artifact that produces an SBOM inconsistent with its manifest is treated "
        "as a build defect. Orphaned package metadata left by an overlay deployment will "
        "misreport component versions and silently corrupt vulnerability assessment - which "
        "is why production deployment replaces rather than overlays.",
    ])

    d.h1("5. Asset lifecycle")
    d.table(["Stage", "Requirement"],
            [["Acquisition", "Recorded in the inventory before it holds data; owner assigned."],
             ["Classification", "Tagged with the highest data classification it holds (AGT-DCP-006)."],
             ["Operation", "Covered by configuration baseline (AGT-CfMP-016) and monitoring (AGT-LMP-018)."],
             ["Change", "Modified only through AGT-ChMP-017."],
             ["Retirement", "Data sanitized per AGT-MPP-008; entry closed with a disposal record."]],
            widths=[1.3, 4.9])

    d.h1("6. Tagging standard")
    d.table(["Tag", "Values", "Purpose"],
            [["environment", "prod | dev", "Determines change control and deployment method"],
             ["dataClassification", "Public | Internal | Confidential | Restricted", "Drives handling controls"],
             ["owner", "Named individual", "Accountability for review"],
             ["costCenter", "AGT cost code", "Financial attribution"],
             ["system", "docuaction-tefca-arc", "Groups assets to the authorization boundary"]],
            widths=[1.4, 2.4, 2.4])

    d.h1("7. Unauthorized assets")
    d.p("Any asset discovered that is not in the inventory is treated as unauthorized until "
        "an owner is identified and it is either recorded or removed. Shadow resources are "
        "a common source of unmonitored exposure precisely because nothing scans what "
        "nothing knows about.")

    d.h1("8. Review")
    d.table(["Activity", "Frequency", "Owner"],
            [["Reconcile cloud inventory against Azure Resource Graph", "Quarterly", "Engineering"],
             ["Reconcile endpoint inventory against MDM", "Quarterly", "Security Officer"],
             ["Review SBOM for unmaintained components", "Monthly with scan", "Engineering"],
             ["Review asset owners for accuracy", "Annually", "Security Officer"]],
            widths=[3.0, 1.4, 1.8])

    d.roles([
        ["Engineering", "Maintains cloud and software inventory; generates SBOMs."],
        ["Security Officer", "Maintains endpoint and identity inventory; investigates unauthorized assets."],
        ["Asset owners", "Confirm accuracy at review; approve retirement."],
    ])
    _finish(d,
            [["NIST 800-53", "CM-8, CM-8(1), PM-5", "Information system component inventory and updates.", "Met"],
             ["NIST 800-53", "SA-4, SR-4", "Acquisition process; provenance of components.", "Partial"],
             ["HIPAA", "164.310(d)(1)", "Accountability for hardware and media movement.", "Met"],
             ["SOC 2", "CC6.1, CC3.2", "Inventory as the basis for access and risk identification.", "Met"],
             ["ISO 27001", "A.5.9, A.5.10", "Inventory of information and associated assets; acceptable use.", "Met"],
             ["CMMI L3", "CM", "Configuration identification of work products.", "Met"],
             ["FedRAMP", "CM-8(3)", "Automated unauthorized component detection.", "Partial - detection is periodic, not continuous."]],
            [["AGT-DCP-006", "Data Classification Policy", "Classification tags applied to assets"],
             ["AGT-CfMP-016", "Configuration Management Policy", "Baselines applied to inventoried assets"],
             ["AGT-VRM-012", "Vendor Risk Management Policy", "Third-party service inventory"],
             ["AGT-MPP-008", "Media Protection Policy", "Sanitization at retirement"]])


def vrm():
    d = AGTDoc("AGT-VRM-012", "Vendor Risk Management Policy")
    _std_open(d, "This policy governs how AGT selects, assesses, contracts with, and "
                 "monitors third parties that store, process, or transmit AGT or customer "
                 "information. AGT's platform depends on external services for identity, "
                 "email, data feeds, and AI inference; each is a path by which AGT data "
                 "leaves AGT's control, and the contract is the only control that operates "
                 "once it has.")
    d.h1("3. Vendor classification")
    d.table(["Tier", "Definition", "Assessment", "Review"],
            [["Critical", "Processes PHI or Restricted data, or an outage halts the platform",
              "Full security assessment + BAA", "Annual + on material change"],
             ["High", "Processes Confidential data or holds privileged access",
              "Security questionnaire + contract review", "Annual"],
             ["Moderate", "Processes Internal data only", "Questionnaire", "Every 2 years"],
             ["Low", "Public data only; no access to AGT systems", "Record only", "On renewal"]],
            widths=[1.0, 2.2, 1.7, 1.3])

    d.h1("4. Current vendor register")
    d.table(["Vendor", "Service", "Data exposure", "Tier", "BAA status"],
            [["Microsoft Azure", "Cloud hosting, identity, database, Key Vault",
              "All classes including PHI", "Critical", "In place (Microsoft BAA)"],
             ["Anthropic", "AI inference (Claude) for document and clinical text analysis",
              "Clinical text - potential PHI", "Critical", "REQUIRED - see section 7"],
             ["OpenAI", "AI inference and audio transcription",
              "Audio and transcripts - potential PHI", "Critical", "REQUIRED - see section 7"],
             ["SendGrid (Twilio)", "Transactional email", "PII in recipient addresses", "High", "Assess"],
             ["Perigon", "News and article data feed", "Public data only", "Low", "Not applicable"],
             ["GitHub", "Source control and CI/CD", "Source code, build secrets", "High", "Not applicable"],
             ["NPPES / PECOS / LEIE", "Federal provider registries (read-only, public)",
              "Public provider data", "Low", "Not applicable"],
             ["SAM.gov", "Federal entity registry", "Public entity data", "Low", "Not applicable"]],
            widths=[1.2, 1.7, 1.6, 0.8, 1.3])

    d.h1("5. Assessment requirements")
    d.bullets([
        "Before a vendor receives Confidential or Restricted data, a security assessment is "
        "completed and recorded.",
        "Evidence accepted: SOC 2 Type II report, ISO 27001 certificate, HITRUST "
        "certification, or a completed AGT security questionnaire with supporting artifacts.",
        "A SOC 2 report is reviewed for scope and exceptions, not merely for existence. A "
        "report whose scope excludes the service AGT actually uses is not evidence about "
        "that service.",
        "Sub-processors are identified; a vendor that will not disclose its sub-processors "
        "cannot be assessed and is not approved for Restricted data.",
    ])

    d.h1("6. Contract requirements")
    d.table(["Requirement", "Critical", "High", "Moderate"],
            [["Business Associate Agreement (where PHI)", "Required", "Required", "n/a"],
             ["Breach notification to AGT", "Within 24 hours", "Within 72 hours", "Within 30 days"],
             ["Right to audit or equivalent attestation", "Required", "Required", "Optional"],
             ["Data return and deletion on termination", "Required", "Required", "Required"],
             ["Subcontractor flow-down of obligations", "Required", "Required", "Optional"],
             ["Data residency commitment", "Required (US)", "Required (US)", "Preferred"],
             ["Prohibition on training models with AGT data", "Required", "Required", "n/a"]],
            widths=[2.4, 1.3, 1.3, 1.2])

    d.h1("7. Open finding - AI provider BAAs")
    d.p("Two Critical-tier vendors process content that can contain PHI and do not have an "
        "executed Business Associate Agreement with AGT.")
    d.table(["Vendor", "Exposure", "Status", "Required action"],
            [["Anthropic", "Clinical text submitted for note and care-plan generation",
              "No BAA executed", "Execute BAA with zero-retention terms, or block PHI paths"],
             ["OpenAI", "Audio submitted for transcription; transcripts returned",
              "No BAA executed", "Execute BAA with zero-retention terms, or block PHI paths"]],
            widths=[1.1, 2.2, 1.3, 1.8])
    d.p("Under HIPAA 164.308(b)(1) a covered entity or business associate may not permit a "
        "business associate to create, receive, maintain, or transmit PHI on its behalf "
        "without satisfactory assurances in the form of a written agreement. Until those "
        "agreements exist, the compliant position is that PHI must not reach these "
        "providers, and the controls in AGT-AIGOV-013 are what enforce that. This is "
        "recorded in the POA&M as an open item with executive visibility rather than "
        "presented as an accepted risk.")

    d.h1("8. Ongoing monitoring")
    d.bullets([
        "Annual reassessment for Critical and High tiers; the review record is retained.",
        "Vendor breach notifications are handled as AGT incidents under AGT-IRP-022.",
        "Vendor status changes - acquisition, certification lapse, publicized breach - "
        "trigger an out-of-cycle review.",
        "Vendor concentration is reviewed annually: the platform's dependence on a single "
        "cloud provider is a deliberate, documented risk acceptance rather than an "
        "oversight.",
    ])

    d.h1("9. Termination")
    d.numbered([
        "Revoke the vendor's access to AGT systems and rotate any shared credential.",
        "Obtain written confirmation of data return or destruction.",
        "Record the confirmation as evidence; retain per AGT-DRP-007.",
        "Update the vendor register and the asset inventory.",
    ])

    d.roles([
        ["CEO", "Approves Critical-tier vendors and accepts residual vendor risk."],
        ["Security Officer", "Performs assessments; maintains the register; monitors annually."],
        ["Privacy Officer", "Determines BAA applicability; reviews PHI flows to vendors."],
        ["Engineering", "Implements technical controls that constrain what data reaches a vendor."],
    ])
    _finish(d,
            [["NIST 800-53", "SA-9, SA-4, SR-3, SR-6", "External system services, acquisition, supply chain controls and assessments.", "Met"],
             ["HIPAA", "164.308(b)(1)", "Business associate contracts and other arrangements.", "Partial - two AI provider BAAs outstanding."],
             ["HIPAA", "164.314(a)", "Business associate contract content requirements.", "Met where executed"],
             ["SOC 2", "CC9.2", "Vendor and business partner risk management.", "Met"],
             ["ISO 27001", "A.5.19, A.5.20, A.5.21, A.5.22", "Supplier relationships, agreements, ICT supply chain, monitoring.", "Met"],
             ["FedRAMP", "SA-9(1)", "Risk assessment of external service providers.", "Partial"]],
            [["AGT-AIGOV-013", "AI Governance Policy", "Controls what data may reach AI vendors"],
             ["AGT-DCP-006", "Data Classification Policy", "Determines what may be shared externally"],
             ["AGT-AMP-011", "Asset Management Policy", "Third-party service inventory"],
             ["AGT-IRP-022", "Incident Response Plan", "Vendor breach handling"],
             ["AGT-RMP-023", "Risk Management Plan", "Vendor risk feeds the risk register"]])


def aigov():
    d = AGTDoc("AGT-AIGOV-013", "AI Governance Policy")
    _std_open(d, "This policy governs the use of artificial intelligence services within "
                 "the DocuAction TEFCA ARC platform and by AGT personnel. AI providers are "
                 "the newest and least settled category of data processor AGT uses, and the "
                 "platform sends them clinical text and audio. The controls here exist "
                 "because prompt content leaves AGT's boundary in a form that is difficult "
                 "to recall and, absent contractual terms, may be retained.")
    d.definitions([
        ["Prompt", "The input sent to an AI model, including any context or documents."],
        ["Zero data retention", "A contractual commitment that inputs are not stored after inference."],
        ["Human in the loop", "Mandatory human review before an AI output is acted upon."],
        ["Model training", "Use of submitted data to improve a vendor's model."],
    ])

    d.h1("3. Approved AI services and permitted data")
    d.table(["Provider", "Use in platform", "Maximum data class permitted", "Condition"],
            [["Anthropic (Claude)", "Clinical note generation, care plans, discharge summaries, document analysis",
              "Confidential", "PHI prohibited until a BAA is executed"],
             ["OpenAI", "Audio transcription (Whisper), analysis",
              "Confidential", "PHI prohibited until a BAA is executed"],
             ["Perigon", "News article retrieval", "Public", "No restriction - public data only"]],
            widths=[1.3, 2.2, 1.4, 1.5])
    d.p("The distinction that matters operationally: 'Confidential permitted, Restricted "
        "prohibited' is a statement about what the platform must prevent, not a description "
        "of what it currently prevents. Where a code path can carry PHI to a provider "
        "without a BAA, that path is a finding and is tracked in the POA&M.")

    d.h1("4. Known PHI exposure paths")
    d.table(["Path", "Data sent", "Control status"],
            [["Audio transcription pipeline", "Raw audio, unredacted, sent to the transcription provider",
              "No pre-transmission redaction. Disclosed in the API response rather than implied."],
             ["Case-management note generation", "Clinical facts extracted from user-supplied text",
              "Human review required before clinical use"],
             ["Document analysis", "Document text as supplied by the user",
              "Classification-dependent; user is responsible for input class"]],
            widths=[1.7, 2.2, 2.3])
    d.note("The transcription pipeline previously reported a masking step it did not "
           "perform. Its phase label and verification fields were corrected to state "
           "plainly that audio is sent to the provider unredacted. A control that is "
           "described but not implemented is worse than an absent control, because it "
           "suppresses the risk assessment that would otherwise occur.")

    d.h1("5. Mandatory controls")
    d.bullets([
        "Zero data retention terms are required in any agreement covering Confidential or "
        "Restricted data.",
        "Training on AGT or customer data is contractually prohibited for all providers.",
        "Prompts must not include credentials, keys, or configuration secrets. Where code is "
        "sent for analysis, secret values are scrubbed before transmission.",
        "Only excerpts necessary for the task are sent, not entire files or datasets.",
        "AI-generated clinical content is never delivered to a patient or entered into a "
        "record without human review.",
        "AI outputs that inform a security or compliance conclusion are labelled as "
        "AI-generated and carry the model's own confidence, so that an LLM opinion is never "
        "silently promoted to the standing of a deterministic result.",
    ])

    d.h1("6. Human-in-the-loop requirements")
    d.table(["Output type", "Review required", "Reviewer"],
            [["Clinical note, care plan, discharge summary", "Always, before clinical use", "Licensed clinician"],
             ["Transcription of clinical audio", "Always, before entry into a record", "Clinical staff"],
             ["Compliance or security finding", "Always, before remediation action", "Security Officer"],
             ["Published bulletin content", "Editorial review before delivery", "Editorial lead"]],
            widths=[2.2, 1.9, 2.1])
    d.p("The escalation logic that routes content to human review must fail safe. A rule "
        "that escalates only when the model reports a positive detection will silently stop "
        "escalating if the model reports nothing - which is exactly what happens when a "
        "prompt template pre-fills a zero count. Escalation is therefore triggered by "
        "absence of verification as well as by positive detection.")

    d.h1("7. Prompt and output security")
    d.bullets([
        "Untrusted content in a prompt is treated as data, not instructions. Prompt "
        "injection is assumed rather than hoped against.",
        "AI output is not executed, and is not interpolated into a query, command, or "
        "template without validation.",
        "Prompts and outputs containing Confidential data are not logged in plaintext.",
        "API keys for AI providers are held in Key Vault (AGT-CKM-009).",
    ])

    d.h1("8. Cost and abuse controls")
    d.p("AI inference is a metered, attacker-exploitable resource. An unauthenticated "
        "endpoint that triggers inference is a cost-amplification vector as well as an "
        "information-disclosure one.")
    d.bullets([
        "Endpoints that trigger inference require authentication and are rate-limited.",
        "Endpoints that report spend and token consumption require authentication, because "
        "they hand an attacker the measurement needed to size an amplification attack.",
        "Per-run cost is tracked and reviewed; unexplained increases are investigated.",
    ])

    d.h1("9. AI risk register")
    d.table(["Risk", "Likelihood", "Impact", "Treatment"],
            [["PHI reaches a provider without a BAA", "Medium", "High", "Execute BAAs; block PHI paths until then"],
             ["Prompt injection alters model behaviour", "Medium", "Medium", "Treat content as data; validate outputs"],
             ["Model output is clinically wrong and acted upon", "Medium", "High", "Mandatory human review"],
             ["Provider retains or trains on submitted data", "Low", "High", "Contractual zero-retention and no-training terms"],
             ["Cost amplification via an exposed endpoint", "Low", "Medium", "Authentication and rate limiting"],
             ["Vendor outage halts a dependent workflow", "Medium", "Low", "Graceful degradation; no silent failure"]],
            widths=[2.2, 1.0, 0.9, 2.1])

    d.h1("10. Responsible AI principles")
    d.bullets([
        "Transparency: users are told when content is AI-generated.",
        "Accountability: a named human is responsible for every AI-informed decision.",
        "Fairness: outputs affecting individuals are reviewed for disparate impact.",
        "Contestability: an individual affected by an AI-informed decision can request "
        "human review.",
        "Honesty about capability: the platform does not describe a safeguard it does not "
        "implement.",
    ])

    d.roles([
        ["CEO", "Approves AI providers and accepts residual AI risk."],
        ["Privacy Officer", "Determines whether a data flow constitutes PHI disclosure."],
        ["Security Officer", "Maintains the AI risk register; verifies scrubbing and rate limits."],
        ["Engineering", "Implements data-minimization, redaction, and fail-safe escalation."],
        ["Clinical staff", "Perform mandatory human review of clinical AI output."],
    ])
    _finish(d,
            [["NIST AI RMF", "GOVERN, MAP, MEASURE, MANAGE", "AI risk governance, context mapping, measurement, and treatment.", "Met"],
             ["NIST 800-53", "SA-9, SI-10, AC-4", "External services, input validation, information flow enforcement.", "Met"],
             ["HIPAA", "164.308(b)(1)", "Business associate assurances before PHI disclosure to AI vendors.", "Open - see AGT-VRM-012"],
             ["HIPAA", "164.502(b)", "Minimum necessary applied to prompt content.", "Met"],
             ["SOC 2", "CC9.2, CC3.2", "Vendor risk and risk identification for AI services.", "Met"],
             ["ISO 27001", "A.5.19, A.8.28", "Supplier relationships; secure coding for AI integration.", "Met"],
             ["ISO 42001", "Clause 6, 8", "AI management system planning and operation.", "Partial"]],
            [["AGT-VRM-012", "Vendor Risk Management Policy", "Vendor assessment and BAA status"],
             ["AGT-DCP-006", "Data Classification Policy", "Defines what may be sent to a provider"],
             ["AGT-CKM-009", "Cryptographic Key Management Policy", "Provider API key custody"],
             ["AGT-PPF-014", "Privacy Policy Framework", "Privacy assessment of AI processing"],
             ["AGT-RMP-023", "Risk Management Plan", "AI risks feed the enterprise register"]])


def ppf():
    d = AGTDoc("AGT-PPF-014", "Privacy Policy Framework")
    _std_open(d, "This framework establishes how AGT governs personal information across "
                 "its lifecycle, satisfies the HIPAA Privacy Rule where AGT acts as a "
                 "business associate, and responds to the rights of individuals. Privacy "
                 "and security overlap but are not the same discipline: security asks "
                 "whether access was authorized, privacy asks whether it should have been "
                 "permitted at all.")
    d.definitions([
        ["Individual", "The natural person to whom personal information relates."],
        ["Covered entity", "A health plan, clearinghouse, or provider under HIPAA."],
        ["Business associate", "An entity that handles PHI on behalf of a covered entity."],
        ["PIA", "Privacy Impact Assessment."],
        ["Minimum necessary", "The HIPAA principle limiting use to what the purpose requires."],
    ])

    d.h1("3. AGT's role")
    d.p("For the DocuAction TEFCA ARC platform, AGT generally acts as a business associate "
        "to covered-entity customers. This determines which obligations apply directly and "
        "which flow through the Business Associate Agreement. AGT does not make treatment, "
        "payment, or operations determinations on its own behalf with customer PHI.")

    d.h1("4. Privacy principles")
    d.numbered([
        "Lawfulness and transparency - processing has a documented basis and is disclosed.",
        "Purpose limitation - data collected for one purpose is not repurposed silently.",
        "Data minimization - collect and transmit the least data that accomplishes the task.",
        "Accuracy - individuals may correct inaccurate records.",
        "Storage limitation - retained only as long as AGT-DRP-007 requires.",
        "Integrity and confidentiality - protected per the security policy set.",
        "Accountability - AGT can demonstrate compliance, not merely assert it.",
    ])

    d.h1("5. Individual rights")
    d.table(["Right", "Basis", "AGT response", "Timeline"],
            [["Access to PHI", "HIPAA 164.524", "Forward to the covered entity; support their response", "Per BAA, support within 10 days"],
             ["Amendment", "HIPAA 164.526", "Support the covered entity's determination", "Per BAA"],
             ["Accounting of disclosures", "HIPAA 164.528", "Provide audit records of disclosures", "Per BAA, within 30 days"],
             ["Restriction request", "HIPAA 164.522", "Honour restrictions communicated by the covered entity", "On notification"],
             ["Deletion (state laws)", "CCPA/CPRA, VCDPA and similar", "Assess applicability; PHI is generally exempt", "45 days"],
             ["Opt out of sale or sharing", "State privacy laws", "AGT does not sell personal information", "n/a"]],
            widths=[1.3, 1.4, 2.2, 1.3])

    d.h1("6. Privacy by design")
    d.bullets([
        "New features handling personal information require a PIA before release.",
        "Audit logs record the route accessed rather than the record identifier, so the "
        "privacy control does not itself become a PHI store.",
        "Error messages and logs are reviewed to ensure they do not disclose personal data.",
        "Default settings favour the more private option; sharing is opt-in.",
    ])

    d.h1("7. Privacy Impact Assessment")
    d.p("A PIA is required when a change introduces a new category of personal data, a new "
        "recipient of personal data, a new purpose, or automated decision-making affecting "
        "individuals.")
    d.numbered([
        "Describe the data, its source, and the individuals affected.",
        "State the purpose and lawful basis.",
        "Map the flow, including every third party that receives it.",
        "Identify privacy risks and the controls that reduce them.",
        "Record residual risk and the accepting authority.",
        "Privacy Officer approves before release.",
    ])

    d.h1("8. Breach notification")
    d.p("A breach of unsecured PHI triggers obligations with hard deadlines. The controlling "
        "clock for AGT as a business associate is the notification to the covered entity, "
        "because their 60-day obligation to individuals begins on discovery.")
    d.table(["Obligation", "Recipient", "Deadline", "Reference"],
            [["Notify the covered entity", "Customer", "Without unreasonable delay; per BAA, within 24 hours", "164.410"],
             ["Notify affected individuals", "Individuals (by covered entity)", "Within 60 days of discovery", "164.404"],
             ["Notify HHS", "HHS OCR", "Within 60 days if 500+ affected; annually otherwise", "164.408"],
             ["Notify media", "Prominent media outlet", "Within 60 days if 500+ in a state", "164.406"],
             ["Notify contracting officer", "Government COR", "Within 24 hours", "Contract"]],
            widths=[1.7, 1.7, 1.8, 1.0])
    d.p("Encryption meeting HHS guidance is a safe harbour: loss of properly encrypted PHI "
        "is not a breach of unsecured PHI. This is the practical reason encryption at rest "
        "and in transit is mandatory rather than recommended.")

    d.h1("9. State privacy laws")
    d.p("Where state law applies to information outside HIPAA's scope, AGT honours the more "
        "protective requirement. HIPAA-covered PHI is generally exempt from CCPA/CPRA and "
        "similar state regimes, but employee and marketing data held by AGT is not, and is "
        "handled under the same principles.")

    d.roles([
        ["Privacy Officer", "Owns this framework; approves PIAs; determines breach status."],
        ["Security Officer", "Provides audit evidence; implements technical privacy controls."],
        ["CEO", "Accountable for notification decisions and regulator communication."],
        ["All personnel", "Report suspected privacy incidents immediately."],
    ])
    _finish(d,
            [["HIPAA Privacy Rule", "164.502, 164.504, 164.514", "Uses and disclosures, business associate provisions, de-identification.", "Met"],
             ["HIPAA Breach Rule", "164.400-414", "Breach discovery, assessment, and notification.", "Met"],
             ["NIST 800-53", "PT-1 through PT-8, AR family", "Personally identifiable information processing and transparency.", "Met"],
             ["NIST Privacy Framework", "IDENTIFY-P, GOVERN-P, CONTROL-P", "Privacy risk governance and individual control.", "Met"],
             ["SOC 2", "P1.0 through P8.0", "Privacy criteria - notice, choice, collection, use, retention, disclosure.", "Met"],
             ["ISO 27001", "A.5.34", "Privacy and protection of personally identifiable information.", "Met"],
             ["ISO 27701", "Clause 7, 8", "PII controller and processor guidance.", "Partial"]],
            [["AGT-DCP-006", "Data Classification Policy", "Identifies PHI and PII"],
             ["AGT-DRP-007", "Data Retention Policy", "Storage limitation"],
             ["AGT-IRP-022", "Incident Response Plan", "Breach response procedure"],
             ["AGT-AIGOV-013", "AI Governance Policy", "Privacy assessment of AI processing"],
             ["AGT-VRM-012", "Vendor Risk Management Policy", "BAA requirements"]])


if __name__ == "__main__":
    mdm(); amp(); vrm(); aigov(); ppf()
    print(f"  batch 2 complete: {len(BUILT)} documents")
