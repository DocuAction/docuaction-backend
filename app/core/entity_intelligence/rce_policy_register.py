"""RCE policy version register — DATA, verified against primary sources.

Every entry cites the RCE document, version, status, effective date and URL as
read on 2026-09-12. This is layer 4 (RCE governing material) unless stated: it
records what the RCE says, never what the ONC ARC contract requires. Nothing
here is consulted by the comparison engine; it exists so a run can answer
"which rule version applied on the review date" and so proposals are never
mistaken for requirements.

Source of truth for statuses: the RCE website's "TEFCA and RCE Resources" and
"TEFCA Topics in Change Management" pages, and the SOP PDFs themselves. Full
URLs are recorded in docs/entity-intelligence/RCE_POLICY_VERSION_REGISTER.md;
this module carries host-free document citations by design (the platform's
provenance guard forbids naming external directory systems in code, and the
RCE's published documents are cited here by file name only).
"""
from __future__ import annotations

from datetime import date

from .policy import AuthorityLayer, PolicyRegister, RuleDefinition, RuleStatus, RuleVersion

VERIFIED = date(2026, 9, 12)
RCE = AuthorityLayer.RCE_GOVERNING_MATERIAL
RES = "RCE-published: 'TEFCA and RCE Resources' page"
TICM = "RCE-published: 'TEFCA Topics in Change Management' page"


def build_register() -> PolicyRegister:
    r = PolicyRegister()

    r.add(RuleDefinition("RCE.XP_VETTING", "TEFCA", "Exchange Purpose vetting of Entrants for T-TRTMNT",
                         "Data points and evidence a Sponsoring QHIN must submit to the Entrant Review List before "
                         "an Entrant may be listed in the RCE Directory for a Vetted XP."),
          RuleVersion("RCE.XP_VETTING", "1.0", RuleStatus.SUPERSEDED, RCE, "SOP: Exchange Purpose (XP) Vetting Process",
                      section="4", effective_from=date(2024, 11, 13), effective_to=date(2026, 8, 3),
                      citation_url="RCE-published PDF: SOP-XP-Vetting-Process-508.pdf",
                      applicability="QHINs, RCE", last_verified=VERIFIED, note="Initial publication November 13, 2024 (version history in v2.0)."),
          RuleVersion("RCE.XP_VETTING", "2.0", RuleStatus.EFFECTIVE, RCE, "SOP: Exchange Purpose (XP) Vetting Process",
                      section="4.5.2 (data points), 4.5.3 (Representative Entrants), Appendix 1 (evidence tiers)",
                      effective_from=date(2026, 8, 3), approved_on=date(2026, 7, 1), supersedes="1.0",
                      citation_url="RCE-published PDF: XP-Vetting-Process-v2.0_7.1.2026_508.pdf",
                      evidence_requirement=("Sponsoring QHIN Name; Entrant legal entity name; DBA (if any); Corporate Business "
                                            "Address; Website (or attestation of none); Site of Care Address (may be none for "
                                            "home/mobile/virtual care); NPI; Type of Health Care Provider; HIPAA Covered Entity "
                                            "Health Care Provider evidence (Appendix 1 Tier 1 or, until 2026-12-31, Tier 2); "
                                            "description of T-TRTMNT triggers if the Principal Node is not an EHR."),
                      applicability="QHINs, RCE; compliance date August 3, 2026 for Q/P/S", last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.XP_VETTING.TIER2_CMS_DIRECTORY", "TEFCA", "Tier 2 evidence: link to a CMS directory listing",
                         "Appendix 1 Tier 2: a link to the Entrant's listing in any directory maintained by CMS, "
                         "including the Medicare FFS Public Provider Enrollment dataset. Accepted only within a window."),
          RuleVersion("RCE.XP_VETTING.TIER2_CMS_DIRECTORY", "2.0", RuleStatus.EFFECTIVE, RCE,
                      "SOP: Exchange Purpose (XP) Vetting Process", section="Appendix 1",
                      effective_from=date(2026, 8, 1), effective_to=date(2027, 1, 1), approved_on=date(2026, 7, 1),
                      citation_url="RCE-published PDF: XP-Vetting-Process-v2.0_7.1.2026_508.pdf",
                      evidence_requirement=("Between August 1, 2026 and December 31, 2026 a submission MUST include Tier 1 or Tier 2 "
                                            "evidence; Tier 2 = link to the Entrant's listing in a CMS-maintained directory "
                                            "(e.g. data.cms.gov Medicare FFS Public Provider Enrollment). Beginning January 1, 2027 "
                                            "Tier 2 is no longer accepted."),
                      applicability="Entrants for T-TRTMNT", last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.TREATMENT_XP", "TEFCA", "Treatment XP implementation and T-TRTMNT eligibility",
                         "Which entities may use T-TREAT / T-TRTMNT and who must respond."),
          RuleVersion("RCE.TREATMENT_XP", "1.2", RuleStatus.SUPERSEDED, RCE, "SOP: XP Implementation: Treatment",
                      effective_from=date(2026, 2, 15), effective_to=date(2026, 8, 3),
                      citation_url="RCE-published PDF: SOP-Treatment-XP-Implementation_v1.2_508.pdf",
                      last_verified=VERIFIED),
          RuleVersion("RCE.TREATMENT_XP", "2.0", RuleStatus.EFFECTIVE, RCE, "SOP: XP Implementation: Treatment",
                      section="5.1 (eligibility), 5.2 (circumstances)", effective_from=date(2026, 8, 3),
                      approved_on=date(2026, 7, 1), supersedes="1.2",
                      citation_url="RCE-published PDF: Treatment-XP-SOP-v2.0_7.1.2026_508.pdf",
                      evidence_requirement="A vetted Covered Entity Health Care Provider or its Delegate is eligible to use T-TRTMNT (5.1(a)).",
                      last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.EXCHANGE_PURPOSES", "TEFCA", "Authorized Exchange Purposes and XP Codes",
                         "XP code table, required responses, fees."),
          RuleVersion("RCE.EXCHANGE_PURPOSES", "5.0", RuleStatus.SUPERSEDED, RCE, "SOP: Exchange Purposes (XPs)",
                      effective_from=date(2026, 2, 15), effective_to=date(2026, 8, 3), last_verified=VERIFIED,
                      citation_url="RCE-published PDF: SOP-Exchange-Purposes-v5_508.pdf"),
          RuleVersion("RCE.EXCHANGE_PURPOSES", "5.1", RuleStatus.EFFECTIVE, RCE, "SOP: Exchange Purposes (XPs)",
                      section="4.1 Table 1", effective_from=date(2026, 8, 3), approved_on=date(2026, 7, 1), supersedes="5.0",
                      citation_url="RCE-published PDF: Exchange-Purposes-SOP-v5.1_7.1.2026_508.pdf",
                      last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.IAS_XP", "TEFCA", "Individual Access Services XP implementation",
                         "Individual identity verification and response obligations for IAS."),
          RuleVersion("RCE.IAS_XP", "3.0", RuleStatus.EFFECTIVE, RCE, "SOP: XP Implementation: Individual Access Services",
                      effective_from=date(2026, 8, 3), approved_on=date(2026, 7, 1),
                      citation_url="RCE-published PDF: SOP-IAS-XP-v3_June2026_Clean_-5081.pdf",
                      note="Not an organisational-identity rule; recorded for completeness of the August 3, 2026 set.",
                      last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.DIRECTORY_REQUIREMENTS", "TEFCA", "RCE Directory Service requirements",
                         "Directory represents technical relationships (Nodes); identifiers required for XPs; FEIN on Organization resources."),
          RuleVersion("RCE.DIRECTORY_REQUIREMENTS", "1.0", RuleStatus.SUPERSEDED, RCE,
                      "SOP: RCE Directory Service (Directory) Requirements Policy", effective_from=date(2024, 7, 1),
                      effective_to=date(2026, 9, 14),
                      citation_url="RCE-published PDF: SOP-Directory-Requirements_508-1.pdf",
                      note="July 2024 per the v1.2 draft's version history; exact day not stated on the resources page.",
                      last_verified=VERIFIED),
          RuleVersion("RCE.DIRECTORY_REQUIREMENTS", "1.1", RuleStatus.APPROVED_FUTURE, RCE,
                      "SOP: RCE Directory Service (Directory) Requirements Policy",
                      section="4.1(1) technical relationships; 4.1(13) required identifiers (e.g. NPI for Treatment); 4.1(13.1) FEIN",
                      effective_from=date(2026, 9, 14), supersedes="1.0",
                      citation_url="RCE-published PDF: SOP-Directory-Requirements-v1.1_508.pdf",
                      evidence_requirement=("Each Organization resource in the Directory must carry an IRS FEIN (tribal/government "
                                            "agencies excepted). FEIN/TIN is OUT OF SCOPE for DocuAction by program rule; recorded only."),
                      last_verified=VERIFIED),
          RuleVersion("RCE.DIRECTORY_REQUIREMENTS", "1.2-draft", RuleStatus.UNDER_CONSIDERATION, RCE,
                      "DRAFT SOP: RCE Directory Service (Directory) Requirements Policy v1.2 (TICM 7.13.26)",
                      citation_url="RCE-published PDF: Clean-Draft-SOP-Directory-Requirements-v1.2_TICM-7.13.26-5081.pdf",
                      note="Under consideration Summer 2026; FEIN attribution clarifications. Not effective.", last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.KYP_PROPOSAL", "TEFCA", "Know Your Participant (KYP) lists",
                         "Standard KYP List and T-TRTMNT KYP List proposed to replace the vetting process."),
          RuleVersion("RCE.KYP_PROPOSAL", "2026-02", RuleStatus.UNDER_CONSIDERATION, RCE,
                      "TEFCA Treatment Exchange Purpose Changes Under Consideration – February 2026",
                      citation_url="RCE-published PDF: TEFCA-Treatment-2-pager-DRAFT-5081.pdf",
                      evidence_requirement=("Proposed Standard KYP List: legal name; state of incorporation/organization; website; "
                                            "verification that address/legal name are consistent with the identifier to be listed "
                                            "(NPI, CLIA, NAIC, etc.); existing-TEFCAID check; prior TEFCA participation and termination "
                                            "circumstances; 5%+ owners; organizational chart; LEIE check of entity and owners."),
                      note=("Never adopted as a KYP process. Per the RCE FAQ, 'most of the data elements' were carried into the Vetting "
                            "SOP v2.0 (legal entity name, website, corporate business address, site of care address, NPI, provider "
                            "type, HIPAA CE evidence). State of incorporation, prior participation, ownership, org chart and LEIE "
                            "checks are NOT in the effective v2.0 data points."),
                      last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.RESTRICTED_PARTICIPATION", "TEFCA", "Restricted Participation Status",
                         "Sharing of suspended/terminated Participant and Subparticipant status among QHINs."),
          RuleVersion("RCE.RESTRICTED_PARTICIPATION", "1.0-draft", RuleStatus.DRAFT, RCE,
                      "DRAFT SOP: Restricted Participation Status v1.0",
                      citation_url="RCE-published PDF: DRAFT-TEFCA-RCE-SOP-Restricted-Participation-Listv1.0-clean-5.21.261.pdf",
                      note="Comment deadline June 22, 2026; awaiting approval. Relevant to 'prior participation' concepts; not effective.",
                      last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.INQUIRIES_INVESTIGATIONS", "TEFCA", "Inquiries and Investigations",
                         "Process for escalating compliance questions and RCE investigations."),
          RuleVersion("RCE.INQUIRIES_INVESTIGATIONS", "1.0-draft", RuleStatus.DRAFT, RCE,
                      "DRAFT SOP: Inquiries and Investigations v1.0",
                      citation_url="RCE-published PDF: DRAFT-TEFCA-RCE-SOP-Inquiries-and-Investigations-v1.0-_-clean5.21.2026.pdf",
                      note="Comment deadline June 22, 2026; awaiting approval.", last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.QHIN_NONCOMPLIANCE_CONSEQUENCES", "TEFCA", "Consequences for QHIN Non-Compliance", ""),
          RuleVersion("RCE.QHIN_NONCOMPLIANCE_CONSEQUENCES", "1.0", RuleStatus.EFFECTIVE, RCE,
                      "SOP: Consequences for QHIN Non-Compliance", effective_from=date(2026, 6, 4),
                      citation_url="RCE-published PDF: Consequences-for-QHIN-Non-Compliance-SOP-v1.0_508.pdf",
                      last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.COMMON_AGREEMENT", "TEFCA", "Common Agreement",
                         "Framework agreement; Section 8 RCE Directory Service; definitions of Participant, Subparticipant, U.S. Entity, Node."),
          RuleVersion("RCE.COMMON_AGREEMENT", "2.1", RuleStatus.EFFECTIVE, RCE, "Common Agreement for Nationwide Health Information Interoperability",
                      section="8.3 QHIN Directory Entries", effective_from=date(2024, 11, 1),
                      citation_url="RCE-published PDF: Common-Agreement-2.1_ASTP-508.pdf",
                      evidence_requirement=("Signatory is responsible for entering its Participant and Subparticipant Nodes in the RCE "
                                            "Directory Service and maintaining the accuracy of such entries."),
                      note="Released November 2024; the resources page shows no day; month used.", last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.ENTITY_TYPES", "TEFCA", "Types of entities that can be a Participant or Subparticipant", ""),
          RuleVersion("RCE.ENTITY_TYPES", "final-2022", RuleStatus.EFFECTIVE, RCE,
                      "SOP: Types of Entities That Can Be a Participant or Subparticipant in TEFCA", section="3",
                      effective_from=date(2022, 7, 1),
                      citation_url="RCE-published PDF: SOP-Participant-Subparticipant-Definition_Final.pdf",
                      note="Marked Final; July 2022 upload; day not stated.", last_verified=VERIFIED))

    r.add(RuleDefinition("RCE.FAQ_VETTING_KYP", "TEFCA", "RCE FAQ on vetting and KYP", "Explanatory only."),
          RuleVersion("RCE.FAQ_VETTING_KYP", "2026", RuleStatus.GUIDANCE, RCE, "RCE Frequently Asked Questions",
                      citation_url="RCE-published: RCE website",
                      evidence_requirement=("'The updated Vetting Process Exchange Purpose (XP) SOP ... includes most of the data elements "
                                            "that were proposed as part of the Know Your Participant List'"),
                      last_verified=VERIFIED))
    return r


REGISTER = build_register()
