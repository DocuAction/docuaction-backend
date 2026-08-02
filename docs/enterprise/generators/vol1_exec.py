"""AGT-EX-001 — Volume I, Executive Documentation."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from agtdoc import AGTDoc  # noqa: E402

OUT = pathlib.Path(r"C:\Imran_Coding projects\DocuAction\backend\docs\enterprise")
OUT.mkdir(parents=True, exist_ok=True)

D = AGTDoc(
    doc_id="AGT-EX-001",
    title="DocuAction TEFCA ARC Platform",
    subtitle="Prepared for the Assistant Secretary for Technology Policy / "
             "Office of the National Coordinator for Health IT (ASTP/ONC)",
    version="1.0", date="August 2026", author="Imran Siddiqui")

D.cover("Volume I — Executive Documentation")

D.doc_control([
    ("Document ID", "AGT-EX-001"),
    ("Document Title", "Volume I — Executive Documentation"),
    ("Version", "1.0"),
    ("Status", "Released"),
    ("Date", "August 2026"),
    ("Contract Number", "7571MN26F80064"),
    ("Contractor", "Alliance Global Tech, Inc. (AGT)"),
    ("CAGE Code", "8ERE8"),
    ("UEI", "MP2FLV1MAW93"),
    ("Contracting Officer", "Lydina M. Battle, HHS OMAS"),
    ("Contracting Officer's Representative", "Jeff Riordan, ONC"),
    ("Base Period of Performance", "25 June 2026 – 24 June 2027"),
    ("Year 1 Value", "$1,258,524"),
    ("Five-Year Ceiling", "$5,620,291"),
    ("Author", "Imran Siddiqui, Chief Executive Officer"),
    ("Classification", "CONFIDENTIAL — Controlled Unclassified Information (CUI)"),
    ("Distribution", "ASTP/ONC programme staff; AGT programme staff"),
    ("Related Documents", "AGT-REQ-001, AGT-EA-001, AGT-SD-001, AGT-DA-001, "
                          "AGT-SA-001, AGT-TE-005, AGT-TE-006"),
])
D.page_break()
D.toc()

# ── 1. Executive Summary ─────────────────────────────────────────────────────
D.h1("1. Executive Summary")
D.p("DocuAction is an automated entity verification and review platform built by "
    "Alliance Global Tech, Inc. (AGT) to support the Assistant Secretary for "
    "Technology Policy / Office of the National Coordinator for Health IT "
    "(ASTP/ONC) in reviewing the accuracy of the Trusted Exchange Framework and "
    "Common Agreement (TEFCA) participant registry.")

D.h2("1.1 The problem")
D.p("Under TEFCA, Qualified Health Information Networks (QHINs) submit registry "
    "entries describing themselves, their Participants and their "
    "Sub-Participants. The resulting population is large — on the order of "
    "96,000 entities and growing — and self-reported. ONC has no practical way "
    "to confirm, at that scale and by hand, that a registry entry corresponds to "
    "a real, currently operating, non-excluded healthcare organisation.")
D.p("Manual review does not close this gap. A reviewer verifying one entity "
    "against the National Plan and Provider Enumeration System (NPPES), the "
    "Provider Enrollment, Chain and Ownership System (PECOS) and the HHS Office "
    "of Inspector General List of Excluded Individuals and Entities (OIG LEIE) "
    "performs several minutes of work per record. Applied to the full "
    "population that is tens of thousands of staff-hours per cycle, and it "
    "produces a result that is out of date before it is finished.")

D.h2("1.2 What DocuAction does")
D.p("DocuAction automates the verification and classification steps while "
    "keeping the judgement steps with people.")
D.bullets([
    "Ingests the registry population from ONC-supplied data.",
    "Queries authoritative federal sources for each entity and records what "
    "each source actually returned, with a timestamp and a hash of the response.",
    "Classifies each entity into one of four buckets (B1–B4) using a versioned, "
    "database-driven rule set, so the rule that produced a classification is "
    "recoverable months later.",
    "Draws statistically valid samples using Cochran's formula with a finite "
    "population correction, and reports discrepancy rates with Wilson score "
    "confidence intervals.",
    "Routes entities that cannot be resolved automatically (B3) to a human "
    "reviewer, and records that reviewer's decision and rationale.",
    "Produces weekly and quarterly reports with a mandatory limitations section.",
])

D.h2("1.3 Design principle: report what is known, disclose what is not")
D.p("The platform is built so that an absent answer is never presented as a "
    "clean answer. A source that is unreachable is recorded as 'unavailable'; a "
    "source that answered 'no record' is recorded as 'not_found'. These are "
    "different claims and the platform never conflates them — an outage is not "
    "a finding, and a finding is not an outage.")
D.p("The same principle governs reporting. Every report carries a limitations "
    "section that cannot be empty, and connectors that are not operational are "
    "disclosed rather than silently omitted from the confidence calculation.")
D.callout(
    "This is the single most important property of the platform for ONC's "
    "purposes. A verification system that quietly degrades — reporting "
    "'verified' when it simply could not check — is worse than no system, "
    "because it converts an unknown into a false assurance that no downstream "
    "reader can detect.", "DESIGN PRINCIPLE")

D.h2("1.4 Who it serves")
D.table(["Audience", "Use"],
        [("ASTP/ONC programme staff", "Weekly and quarterly registry accuracy "
                                      "reports; priority review findings"),
         ("ONC methodology reviewers", "The documented review methodology, "
                                       "sampling design and control framework"),
         ("QHINs (indirectly)", "Corrective feedback arising from identified "
                                "discrepancies"),
         ("AGT programme staff", "Operational dashboards, review queues and "
                                 "audit trail"),
         ("Contracting officials", "Deliverable status, closeout artefacts, "
                                   "CPARS-relevant performance evidence")],
        widths=(2.0, 4.5))

D.h2("1.5 Contract context")
D.table(["Field", "Value"],
        [("Contract number", "7571MN26F80064"),
         ("Contracting activity", "HHS Office of Management and Acquisition "
                                  "Services (OMAS)"),
         ("Contracting Officer", "Lydina M. Battle"),
         ("Contracting Officer's Representative", "Jeff Riordan, ONC"),
         ("Contractor", "Alliance Global Tech, Inc."),
         ("CAGE / UEI", "8ERE8 / MP2FLV1MAW93"),
         ("Base period", "25 June 2026 – 24 June 2027"),
         ("Year 1 value", "$1,258,524"),
         ("Five-year ceiling", "$5,620,291"),
         ("Vehicle", "GSA MAS 47QTCA21D003M"),
         ("Tasks", "1 — Kick-off & onboarding; 2 — Review methodology & control "
                   "framework; 3 — Retrospective review; 4 — Ongoing review; "
                   "5 — Priority reviews; 6 — Contract closeout")],
        widths=(2.4, 4.1))
D.page_break()

# ── 2. Business Case ─────────────────────────────────────────────────────────
D.h1("2. Business Case")
D.h2("2.1 The gap AGT set out to fill")
D.p("TEFCA registry accuracy is a precondition for trust in the exchange "
    "framework. If a registry entry does not correspond to a real, currently "
    "operating and non-excluded organisation, then the network of trust built on "
    "that registry inherits the error. The gap is not one of policy — the "
    "Common Agreement is clear on what participants must be — but of "
    "verification capacity at scale.")

D.h2("2.2 Manual versus automated verification")
D.p("The comparison below is presented as a model, not a measurement. The "
    "automated figures derive from measured platform performance; the manual "
    "figures are an estimate of skilled analyst effort and are labelled as such.")
D.table(["Dimension", "Manual review", "DocuAction"],
        [("Sources checked per entity", "Analyst opens each portal separately",
          "Queried in parallel per entity"),
         ("Measured verification latency", "Estimated several minutes per entity",
          "1.84 s mean (n=10, measured, includes live third-party latency)"),
         ("Consistency", "Varies by analyst and by day",
          "Same versioned rule set applied to every entity"),
         ("Auditability", "Notes and spreadsheets",
          "Immutable audit trail; response hash and timestamp per source"),
         ("Reproducibility of a sample", "Not reproducible",
          "Same random seed reproduces the same sample"),
         ("Rule change", "Retraining and re-communication",
          "New rule version with an effective date; prior version retired, "
          "not deleted")],
        widths=(1.7, 2.3, 2.5))
D.callout(
    "The estimated manual figures above have not been measured under this "
    "contract and must not be cited as a benchmark. They are included to frame "
    "the order-of-magnitude difference, not to quantify a saving.", "CAUTION")

D.h2("2.3 Return to ONC")
D.bullets([
    "Coverage: the full registry population becomes reviewable rather than only "
    "a hand-sampled fraction.",
    "Defensibility: every classification cites the rule version and the source "
    "responses that produced it, so a challenged finding can be reconstructed.",
    "Statistical rigour: sample sizes and confidence intervals are computed, "
    "not asserted.",
    "Reusability: the same engine serves the ongoing review task without "
    "rebuild, and the methodology transfers to ONC at closeout under Task 6.",
])

D.h2("2.4 Why AGT built it rather than buying it")
D.p("No commercial product performs TEFCA-specific entity verification. The "
    "authoritative sources are federal and free at the point of use (NPPES, "
    "PECOS, OIG LEIE); the classification logic is specific to the review "
    "methodology agreed with ONC; and the deliverable at closeout includes the "
    "methodology, framework and source code themselves. A commercial black box "
    "could not satisfy the government rights obligations in Task 6.")
D.page_break()

# ── 3. Vision ────────────────────────────────────────────────────────────────
D.h1("3. Vision")
D.p("DocuAction is designed as a configurable verification engine, not as a "
    "single-purpose TEFCA tool. The classification rules live in the database "
    "with versions and effective dates; the authoritative sources are pluggable "
    "connectors behind a common result contract; the sampling and reporting "
    "layers are agnostic to what is being sampled.")

D.h2("3.1 Today — ASTP/ONC")
D.p("TEFCA registry accuracy review under Contract 7571MN26F80064: retrospective "
    "review, ongoing review, priority reviews and closeout.")

D.h2("3.2 Adjacent applications (illustrative, not committed)")
D.table(
    ["Agency", "Candidate application", "Reuse"],
    [("CMS", "Provider enrolment and directory accuracy",
      "NPPES, PECOS and OIG LEIE connectors already built"),
     ("SBA", "Small business certification verification",
      "SAM.gov entity and exclusions connectors; sampling and reporting"),
     ("VA", "Community care network provider verification",
      "Same verification and classification core"),
     ("Any federal registry owner", "Registry accuracy assurance",
      "Rules engine, sampling engine, reporting engine")],
    widths=(1.1, 2.7, 2.7))
D.callout("The applications in section 3.2 are illustrative of the "
          "architecture's reusability. They are not proposed, funded or "
          "committed work under this contract.", "SCOPE NOTE")
D.page_break()

# ── 4. Scope ─────────────────────────────────────────────────────────────────
D.h1("4. Scope")
D.h2("4.1 In scope — Tasks 1 through 6")
D.table(["Task", "Title", "Summary"],
        [("1", "Kick-off & onboarding",
          "Weekly 60-minute meetings for 90 days, transitioning to bi-weekly "
          "30-minute meetings; transition plan; communication protocols"),
         ("2", "Review methodology & control framework",
          "Entity review methodology, accuracy validation, stratification and "
          "prioritisation, B1–B4 criteria, statistical sampling design, control "
          "framework"),
         ("3", "Retrospective review",
          "95% confidence statistical sample of Participants and "
          "Sub-Participants verified against authoritative sources; B1–B4 "
          "classification; weekly reporting"),
         ("4", "Ongoing review",
          "Bi-weekly review cadence; quarterly reporting with per-week trend "
          "analysis; methodology improvement tracking; new QHIN submissions"),
         ("5", "Priority reviews",
          "Ad hoc reviews on ONC request; root cause analysis; severity "
          "assessment; corrective recommendations"),
         ("6", "Contract closeout",
          "Closeout report; transfer of methodology, framework, tools and source "
          "code; educational presentation; government rights materials")],
        widths=(0.5, 1.7, 4.3))

D.h2("4.2 Out of scope")
D.p("The following exist in the wider DocuAction product roadmap and are "
    "explicitly outside this contract. They are named here so that their "
    "presence in the codebase is not mistaken for contract work.")
D.bullets([
    "Case management workflow.",
    "Identity resolution graph across registries.",
    "Event-driven architecture and streaming ingestion.",
    "Bulletin Intelligence (regulatory news monitoring) — a separate AGT "
    "capability sharing the platform, not a TEFCA ARC deliverable.",
])
D.page_break()

# ── 5. Assumptions ───────────────────────────────────────────────────────────
D.h1("5. Assumptions")
D.p("Each assumption below carries the consequence if it proves false, because "
    "an assumption recorded without its consequence gives a reader no way to "
    "judge the exposure.")
D.table(["ID", "Assumption", "If it does not hold"],
        [("A-01", "ONC provides entity data via CSV or Box transfer",
          "Ingestion requires a new integration; schedule impact to Task 3"),
         ("A-02", "SAM.gov and RCE Directory data are provided by ONC",
          "AGT cannot verify against these sources directly; coverage stays at "
          "3 of 7 possible sources"),
         ("A-03", "FIPS 199 Moderate categorisation applies",
          "A High categorisation would require additional controls and a "
          "re-baselined SSP"),
         ("A-04", "Internet access to NPPES, PECOS and OIG LEIE is available "
                  "from the hosting environment",
          "Verification cannot run; the platform reports 'unavailable' rather "
          "than degrading silently"),
         ("A-05", "Azure App Service is the accepted hosting environment",
          "Re-platforming effort and a new authorisation boundary"),
         ("A-06", "No independent 3PAO assessment is required for initial "
                  "operations",
          "A 3PAO engagement would be required before ATO, with cost and "
          "schedule impact"),
         ("A-07", "ONC accepts the B1–B4 classification scheme as documented "
                  "in Task 2",
          "Rule set requires re-versioning; prior classifications remain valid "
          "under their recorded version"),
         ("A-08", "The registry population is approximately 96,000 entities",
          "Sample sizes change; the Cochran calculation adjusts automatically "
          "via the finite population correction")],
        widths=(0.5, 2.6, 3.4))
D.page_break()

# ── 6. Constraints ───────────────────────────────────────────────────────────
D.h1("6. Constraints")
D.p("These are current, verified limitations of the operating environment. Each "
    "was confirmed by direct test rather than assumed.")

D.h2("6.1 SAM.gov API key not provisioned")
D.p("The SAM.gov Entity Management API (v3) and Exclusions API (v4) both return "
    "HTTP 404 when called with the public DEMO_KEY and when called with no key "
    "at all. SAM returns 404 rather than 401 or 403 for an unauthorised key, "
    "which is why this presented as a wrong-URL problem in early testing. The "
    "URLs are correct; a registered key is required.")
D.p("A key alone is necessary but not sufficient: SAM is keyed on Unique Entity "
    "Identifier (UEI), and the TEFCA registry does not currently capture UEI for "
    "its entities. Exact matching therefore requires both a key and a UEI "
    "source. The connector implements a legal-name fallback that flags "
    "multi-match results as ambiguous rather than guessing.")

D.h2("6.2 State registries — no standardised interface")
D.p("There is no common API across the fifty state licensure registries. Most "
    "states expose no machine interface at all. State registry verification is "
    "therefore not implemented, is reported as 'not checked — connector not "
    "implemented', and is excluded from confidence scoring rather than counted "
    "as a missing source.")

D.h2("6.3 RCE Directory — no direct access")
D.p("The Recognized Coordinating Entity directory is not directly accessible to "
    "AGT. Data must be provided by ONC. A support case with The Sequoia Project "
    "remains open.")

D.h2("6.4 Azure Key Vault — firewalled to private link")
D.p("Both vaults exist but reject requests that do not originate from an "
    "approved private link, so secret migration cannot be completed from a "
    "developer workstation. Secrets remain in App Service configuration, which "
    "is encrypted at rest but does not provide the rotation and access-audit "
    "properties of Key Vault.")

D.h2("6.5 Next.js advisories with no stable fix")
D.p("Two High-severity advisories affect the frontend framework. The advisory "
    "range extends through a pre-release version and the latest stable release "
    "is the one in use, so no upgrade resolves them. The automated remediation "
    "path downgrades the framework by two major versions and breaks the "
    "application build. Both are recorded as accepted risks with compensating "
    "controls in AGT-EX-001 §7 and the risk acceptance register.")

D.h2("6.6 Coverage summary")
D.table(["Source", "Status", "In confidence scoring"],
        [("NPPES", "Operational — 5/5 reachable, 391 ms mean", "Yes"),
         ("PECOS", "Operational — 5/5 reachable, 242 ms mean", "Yes"),
         ("OIG LEIE", "Operational — 5/5 reachable, 428 ms mean", "Yes"),
         ("SAM.gov", "Built, not operational — no API key", "No"),
         ("RCE Directory", "Not accessible — ONC-provided", "No"),
         ("State registries", "Not implemented — no standard interface", "No"),
         ("IRS", "Not implemented — keyed on EIN, not captured", "No")],
        widths=(1.5, 3.4, 1.6))
D.callout(
    "Coverage is measured against connectors that exist, not against every "
    "source that could theoretically be consulted. Counting an unbuilt "
    "connector as a missing source would report permanently degraded coverage "
    "for work that was never scheduled, making the platform appear broken "
    "rather than incomplete. Unimplemented sources are disclosed separately in "
    "every verification response and in every report.", "METHOD NOTE")
D.page_break()

# ── 7. Risk Register ─────────────────────────────────────────────────────────
D.h1("7. Risk Register")
D.p("Probability and impact are assessed on a three-point scale (Low, Medium, "
    "High). Owner names the party who can actually act, which for several "
    "entries is ONC rather than AGT.")
RISKS = [
    ("R-01", "Technical", "SAM.gov API key never provisioned, leaving federal "
     "registration and debarment unverified", "Medium", "High",
     "Connector built and tested; activates on key provision with no code "
     "change. Documented request procedure. OIG LEIE independently covers "
     "healthcare exclusion.", "AGT / ONC"),
    ("R-02", "Technical", "SAM.gov key obtained but UEI absent from registry, "
     "so matching stays name-based and ambiguous", "High", "Medium",
     "Ambiguous matches flagged for manual review rather than guessed. Request "
     "UEI in ONC-supplied data.", "ONC"),
    ("R-03", "Technical", "Next.js advisories remain unfixed", "High", "Low",
     "Accepted risk RA-001/RA-002. Static export has no server-side rendering, "
     "excluding the affected request paths. Re-check on next stable release.",
     "AGT"),
    ("R-04", "Technical", "Key Vault migration blocked by private link",
     "High", "Medium",
     "Secrets remain in App Service configuration, encrypted at rest. Migration "
     "to be performed from an approved network.", "AGT"),
    ("R-05", "Technical", "Authoritative source outage during a review cycle",
     "Medium", "Medium",
     "Outages recorded as 'unavailable' and excluded from scoring, never "
     "collapsed into a clean result. Partial-pass rule prevents an outage from "
     "demoting a clean entity.", "AGT"),
    ("R-06", "Technical", "Third-party API contract change breaks a connector",
     "Medium", "Medium",
     "Each connector returns a common result object; failures fail closed to "
     "'unavailable'. Connector health surfaced on the health endpoint.", "AGT"),
    ("R-07", "Contract", "ONC does not accept the Task 2 methodology as "
     "submitted", "Medium", "High",
     "Rules are versioned with effective dates; a methodology change creates a "
     "new version and retires the prior one without invalidating completed "
     "classifications.", "AGT / ONC"),
    ("R-08", "Contract", "Methodology changes mid-period require "
     "re-classification of already-reported entities", "Medium", "Medium",
     "Every classification records its rule version, so the scope of any "
     "re-run is exactly determinable.", "AGT"),
    ("R-09", "Operational", "ONC-supplied entity data is incomplete or "
     "malformed", "Medium", "Medium",
     "Import records per-row errors in the batch record; invalid NPIs are "
     "flagged, never silently rejected.", "ONC / AGT"),
    ("R-10", "Operational", "Registry population grows faster than review "
     "capacity", "Medium", "Medium",
     "Sample size recalculated per cycle with finite population correction; "
     "prioritisation methodology targets higher-risk strata first.", "AGT"),
    ("R-11", "Operational", "B3 review queue exceeds human review capacity",
     "Medium", "Medium",
     "Prioritisation and severity assessment order the queue; B3 volume is "
     "reported per cycle so the trend is visible before it becomes a backlog.",
     "AGT / ONC"),
    ("R-12", "Security", "Three High-severity findings remain open", "High",
     "Low",
     "All three formally accepted with rationale and compensating controls; "
     "review date 31 October 2026.", "AGT"),
    ("R-13", "Security", "No independent 3PAO assessment performed", "High",
     "Medium",
     "Automated Security Assessment performed and documented (AGT-SA-001). "
     "3PAO engagement required before ATO if the government requires one.",
     "ONC"),
    ("R-14", "Security", "Dynamic Application Security Testing not yet executed",
     "Medium", "Medium",
     "Two CI pipelines built (unauthenticated and authenticated), scheduled "
     "weekly, dev-only. Results reported as Not Executed until they run.",
     "AGT"),
    ("R-15", "Operational", "Database restore procedure never rehearsed, so "
     "recovery time objective is unmeasured", "Medium", "High",
     "Documented procedure with 14-day point-in-time restore. Rehearsal "
     "required before ATO.", "AGT"),
    ("R-16", "Technical", "Production traffic still served by the legacy "
     "hosting platform, so fixes deployed to the current environment are not "
     "live on the customer-facing host", "High", "High",
     "Cutover plan documented with ordering, TLS provisioning and rollback. "
     "Requires DNS registrar access.", "AGT"),
]
D.table(["ID", "Category", "Risk", "Prob.", "Impact", "Mitigation", "Owner"],
        RISKS, widths=(0.42, 0.72, 1.75, 0.42, 0.45, 2.3, 0.55), font_size=7.5)
D.page_break()

# ── 8. Stakeholder Register ──────────────────────────────────────────────────
D.h1("8. Stakeholder Register")
D.table(["Name", "Organisation", "Role", "Interest", "Influence"],
        [("Jawanna Henry", "ASTP/ONC", "Technical Lead", "High", "High"),
         ("Kimberly Tavernia", "ASTP/ONC", "Technical Monitor", "High", "High"),
         ("Meley Gebresellassie", "ASTP/ONC", "Methodology Reviewer", "High",
          "High"),
         ("Maggie Gaddis", "ASTP/ONC", "Programme Support", "Medium", "Medium"),
         ("Jeff Riordan", "ASTP/ONC", "Contracting Officer's Representative",
          "High", "High"),
         ("Lydina M. Battle", "HHS OMAS", "Contracting Officer", "High", "High"),
         ("Imran Siddiqui", "AGT", "Chief Executive Officer / Technical Lead",
          "High", "High"),
         ("Nabeel Ashraf", "AGT", "Programme Manager", "High", "High"),
         ("Bilal Naveed", "AGT", "Data Analyst", "Medium", "High"),
         ("Tariq Thangalvadi", "AGT", "Technical", "Medium", "High"),
         ("Nidhin Kadavil", "AGT", "Senior Healthcare IT Advisor", "Medium",
          "Medium")],
        widths=(1.5, 1.1, 2.2, 0.85, 0.85))

D.h2("8.1 Engagement approach")
D.table(
    ["Influence / Interest", "Approach", "Stakeholders"],
    [("High / High", "Manage closely — weekly then bi-weekly meetings, direct "
                     "review of deliverables",
      "Henry, Tavernia, Gebresellassie, Riordan, Battle, Siddiqui, Ashraf"),
     ("High influence / Medium interest",
      "Keep satisfied — consulted on technical and data decisions",
      "Naveed, Thangalvadi"),
     ("Medium / Medium", "Keep informed — briefed on deliverables and findings",
      "Gaddis, Kadavil")],
    widths=(1.6, 2.6, 2.3))
D.page_break()

# ── 9. Acronyms and Glossary ─────────────────────────────────────────────────
D.h1("9. Acronyms and Glossary")
GLOSS = [
    ("3PAO", "Third Party Assessment Organization — an accredited independent "
             "assessor of security controls"),
    ("ARC", "Accuracy Review Contract — the TEFCA registry accuracy review work "
            "under this contract"),
    ("ASTP", "Assistant Secretary for Technology Policy"),
    ("ATO", "Authority to Operate — the government's formal authorisation to "
            "run a system"),
    ("B1", "No Discrepancy — every required source was reached and confirmed "
           "the entity, and the NPI passed its check digit"),
    ("B2", "Minor / Administrative — name, address or taxonomy differs in form "
           "but not in identity"),
    ("B3", "Inexplicable — sources were reached and disagreed, or the primary "
           "source has no record; requires human resolution"),
    ("B4", "Non-Compliant — exclusion, debarment or an invalid identifier; "
           "disqualifying regardless of what other sources say"),
    ("CAGE", "Commercial and Government Entity code — AGT's is 8ERE8"),
    ("CCN", "CMS Certification Number"),
    ("CLIA", "Clinical Laboratory Improvement Amendments — laboratory "
             "certification identifier"),
    ("Cochran", "Cochran's sample size formula, used with a finite population "
                "correction to size statistically valid samples"),
    ("Common Agreement", "The TEFCA legal agreement binding QHINs and their "
                         "participants"),
    ("CPARS", "Contractor Performance Assessment Reporting System"),
    ("CUI", "Controlled Unclassified Information"),
    ("DAST", "Dynamic Application Security Testing — testing a running "
             "application from the outside"),
    ("FAR", "Federal Acquisition Regulation"),
    ("FedRAMP", "Federal Risk and Authorization Management Program"),
    ("FIPS 199", "Federal Information Processing Standard 199 — security "
                 "categorisation of information systems"),
    ("FISMA", "Federal Information Security Modernization Act"),
    ("FPC", "Finite Population Correction — adjusts sample size when the "
            "sample is a material fraction of the population"),
    ("HCID", "Health Care Identifier used in the TEFCA registry"),
    ("HHSAR", "Health and Human Services Acquisition Regulation"),
    ("IPP", "Invoice Processing Platform"),
    ("JWT", "JSON Web Token — the bearer token format used for authentication"),
    ("MAS", "Multiple Award Schedule"),
    ("NIST", "National Institute of Standards and Technology"),
    ("NPI", "National Provider Identifier — a ten-digit identifier with a CMS "
            "check digit"),
    ("NPPES", "National Plan and Provider Enumeration System — the CMS NPI "
              "registry"),
    ("OIG LEIE", "HHS Office of Inspector General List of Excluded Individuals "
                 "and Entities"),
    ("ONC", "Office of the National Coordinator for Health Information "
            "Technology"),
    ("PECOS", "Provider Enrollment, Chain and Ownership System"),
    ("PHI", "Protected Health Information"),
    ("PII", "Personally Identifiable Information"),
    ("POA&M", "Plan of Action and Milestones"),
    ("QHIN", "Qualified Health Information Network"),
    ("QTF", "QHIN Technical Framework"),
    ("RBAC", "Role-Based Access Control"),
    ("RCE", "Recognized Coordinating Entity — The Sequoia Project"),
    ("SAM", "System for Award Management — federal registration and exclusions"),
    ("SAST", "Static Application Security Testing — analysis of source code"),
    ("SCA", "Software Composition Analysis — dependency vulnerability scanning"),
    ("SSP", "System Security Plan"),
    ("TEFCA", "Trusted Exchange Framework and Common Agreement"),
    ("TEFCAID", "The registry's primary entity identifier"),
    ("ToP", "Terms of Participation"),
    ("UEI", "Unique Entity Identifier — AGT's is MP2FLV1MAW93"),
    ("Wilson score interval", "A confidence interval for a proportion that "
                              "remains valid at small counts and near 0 or 1, "
                              "unlike the normal approximation"),
]
D.table(["Term", "Definition"], GLOSS, widths=(1.3, 5.2))
D.page_break()

# ── 10. References ───────────────────────────────────────────────────────────
D.h1("10. References")
D.table(["#", "Reference", "Relevance"],
        [("1", "45 CFR Part 172", "TEFCA regulatory basis"),
         ("2", "TEFCA Common Agreement v2.x",
          "Participant and Sub-Participant obligations"),
         ("3", "QHIN Technical Framework (QTF) v2.1",
          "Technical requirements for QHINs"),
         ("4", "NIST SP 800-53 Rev 5",
          "Security and privacy control baseline"),
         ("5", "NIST SP 800-171",
          "Protecting CUI in nonfederal systems"),
         ("6", "FIPS 199", "Security categorisation — Moderate assumed"),
         ("7", "HIPAA Security Rule (45 CFR Part 164 Subpart C)",
          "Safeguards where PHI is in scope"),
         ("8", "FAR Part 8", "Acquisition via Federal Supply Schedules"),
         ("9", "GSA MAS 47QTCA21D003M", "Contract vehicle"),
         ("10", "NIST SP 800-63B", "Authentication and session management"),
         ("11", "Section 508 / WCAG 2.2 AA", "Accessibility conformance"),
         ("12", "AGT-REQ-001", "Volume II — Requirements Specification"),
         ("13", "AGT-EA-001", "Volume III — Enterprise Architecture"),
         ("14", "AGT-SD-001", "Volume IV — System and Module Design"),
         ("15", "AGT-DA-001", "Volume VI — Enterprise Data Architecture"),
         ("16", "AGT-SA-001", "Automated Security Assessment"),
         ("17", "AGT-TE-005", "TEFCA Operational Validation"),
         ("18", "AGT-TE-006",
          "Performance Baseline, Access Control and API Contract")],
        widths=(0.4, 3.0, 3.1))
D.page_break()

# ── 11. Revision History ─────────────────────────────────────────────────────
D.h1("11. Revision History")
D.table(["Version", "Date", "Author", "Description"],
        [("1.0", "August 2026", "Imran Siddiqui", "Initial release")],
        widths=(0.9, 1.3, 1.7, 2.6))

path = OUT / "AGT-EX-001_Executive_Documentation.docx"
D.save(path)
print(f"saved {path.name}: {path.stat().st_size:,} bytes")
