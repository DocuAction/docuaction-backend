"""TEFCA ARC learning path content: the modules that complete the 16-step
path, the seven-part guide for every module, the role paths, the feature-to-
training traceability rows and the reference library.

WHY THIS IS A SEPARATE FILE
    `learning_content.py` holds the evidence-vocabulary modules whose terms are
    imported from the enums that define them. This file holds the operational
    path — deliveries, relationships, sampling, assignment, determination,
    maker/checker, PM delivery, data handling — and the cross-cutting LMS
    structures. `learning_content.py` assembles both into one registry.

THE SYNCHRONISATION RULE
    When a feature on a screen listed in FEATURES changes, the module that row
    points at changes in the same pull request, and `last_verified_version` on
    the row is set to the knowledge version that shipped with it. The
    traceability endpoint lists stale rows so the gap is visible.

AUTHORITY
    Statements that bind the programme cite the contract. Everything about how
    DocuAction does the work is AGT_IMPLEMENTATION. Nothing in the reference
    library is presented as a contract requirement unless it IS the contract.
"""
from __future__ import annotations

from app.core.learning import (
    Authority, Classification, ContextualHelp, FeatureLink, KnowledgeCheck,
    LearningPath, Lesson, LibraryItem, Module, ModuleGuide, ModuleRevision,
    ProhibitedConclusion, Role, Statement)

CONTRACT = "Contract 7571MN26F80064"
EFFECTIVE = "2026-09-11"
CONTENT_VERSION = "1.2.0"
OWNER = "AGT product owner (TEFCA ARC)"

_SOW_TASK3 = f"{CONTRACT}, Section C, Task 3"
_SOW_TASK4 = f"{CONTRACT}, Section C, Task 4"
_SOW_TASK5 = f"{CONTRACT}, Section C, Task 5"
_SOW_SECTION_F = f"{CONTRACT}, Section F (reports cite the contract number)"


def _impl(text: str) -> Statement:
    return Statement(text, Classification.AGT_IMPLEMENTATION)


def _gov(text: str, source: str) -> Statement:
    return Statement(text, Classification.GOVERNMENT_REQUIREMENT, source=source)


def _open(text: str) -> Statement:
    return Statement(text, Classification.PROGRAM_GUIDANCE_REQUESTED)


# ── new modules ─────────────────────────────────────────────────────────────

DELIVERY = Module(
    slug="delivery-and-ingestion",
    title="ONC/RCE Delivery and Ingestion",
    keywords=["ingestion", "delivery", "ONC", "RCE", "register", "intake", "Area 1", "source file"],
    audience=[Role.ANY],
    objective="Register an official delivery correctly and understand what the system does with it before any review exists.",
    version=CONTENT_VERSION, effective_date=EFFECTIVE,
    lessons=[Lesson(
        slug="register-a-delivery", title="Registering the official delivery",
        objective="Know what a delivery is, who may register one, and what is immutable afterwards.",
        body=("A delivery is the file ONC/RCE sent — nothing else. It is registered on the "
              "ONC/RCE Deliveries screen with its delivery label and the file exactly as "
              "received. DocuAction stores the file byte for byte (Area 1) and records the "
              "hash, the receiver and the time. That layer is never edited.\n\n"
              "Registering a delivery is a Data Operations function above the analyst "
              "role. An analyst who opens the screen is told so plainly rather than "
              "shown a blank denial.\n\n"
              "The Data Import (Non-Official) screen is not the same thing: it exists "
              "for exercises and demonstrations and its data is never an ONC delivery."),
        example=("The synthetic end-to-end proof registered a two-record delivery under "
                 "a DOCUACTION E2E label; every later artefact traced back to that job id."),
        common_mistakes=[
            "Registering an edited copy of the file instead of the file as received.",
            "Using Data Import (Non-Official) for real ONC/RCE material.",
            "Registering the same delivery twice under two labels.",
        ],
        prohibited=[ProhibitedConclusion(
            "The delivery has been reviewed.",
            "Registration establishes the source record. Review starts only when work "
            "is created from a drawn sample or a priority request.")],
        statements=[
            _impl("Area 1 stores the delivered file and one row per delivered line; it is never updated."),
            _impl("Registering a delivery requires a role above analyst; reads are viewer-gated."),
        ])],
    checks=[KnowledgeCheck(
        "A column in the delivered file has a trailing space in every value. Where is it corrected?",
        ["In Area 1, by editing the delivered rows",
         "In Area 2, as a deterministic curation step, leaving Area 1 untouched",
         "Nowhere — the file is re-requested from ONC",
         "By the analyst during review"], 1,
        "Non-substantive corrections are applied in the curated layer. The delivered "
        "layer stays exactly as received so any figure can be traced to it.")],
    guide=ModuleGuide(
        what_is_this="The controlled entry point for official ONC/RCE directory data.",
        why_it_matters="Every report cites the delivery it came from. If the source record is wrong or ambiguous, nothing downstream can be defended.",
        what_automation_does="Stores the file immutably, hashes it, parses one row per line, promotes canonical entities, writes the QHIN relationship edges, and records the processing trace.",
        what_human_does="Confirms the file is the one received, chooses the delivery label, registers it, and reads the processing summary for rejected or held rows.",
        steps=[
            "Open ONC/RCE Deliveries.",
            "Choose the file exactly as received from ONC/RCE.",
            "Enter the delivery label used in correspondence with the COR.",
            "Register the delivery and wait for the processing job to complete.",
            "Read the intake summary: promoted, held and rejected counts, and the reasons.",
            "Open the QHIN view for the delivery to confirm the population resolved.",
        ],
        what_not_to_do=[
            "Do not edit the file before registering it.",
            "Do not use Data Import (Non-Official) for Government material.",
            "Do not treat held or unresolved rows as reviewed or as findings.",
        ],
        what_happens_next="A sampling plan is drawn from the promoted population and review work is created from it (Stratification and Sampling; Review Work Creation and Assignment)."))

RELATIONSHIPS = Module(
    slug="qhin-relationships",
    title="QHIN, Participant and Subparticipant Relationships",
    keywords=["QHIN", "Participant", "Subparticipant", "relationship", "unresolved", "hierarchy"],
    audience=[Role.ANY],
    objective="Read the delivered relationship correctly and never invent one.",
    version=CONTENT_VERSION, effective_date=EFFECTIVE,
    lessons=[Lesson(
        slug="delivered-edges", title="Where the relationship comes from",
        objective="Name the two canonical edges and what an unresolved record means.",
        body=("The QHIN of a record is the canonical managed_by_qhin edge written at "
              "promotion. The parent of a Subparticipant is the sub_participant_of edge. "
              "Neither is inferred from a name, an OID prefix or an address.\n\n"
              "A record with no single canonical QHIN edge is reported as UNRESOLVED with "
              "the reason. It is never placed under a plausible QHIN. The stratified lists "
              "in a contract report name the QHIN from the same edge, so the report and the "
              "operations screens always agree."),
        common_mistakes=[
            "Reading the OID prefix as the QHIN.",
            "Assuming a Subparticipant's QHIN is its parent's without the delivered edge.",
        ],
        prohibited=[ProhibitedConclusion(
            "This unresolved record belongs to QHIN X.",
            "An invented relationship is worse than a visible gap. Unresolved is a "
            "reportable condition of the delivery, not a value to be guessed.")],
        statements=[
            _impl("QHIN is the canonical managed_by_qhin edge; Participant and Subparticipant are the delivered entity level."),
            _gov("Reviews are stratified by QHIN, so the QHIN relationship is the basis of the reported lists.", _SOW_TASK3),
        ])],
    checks=[KnowledgeCheck(
        "A Subparticipant record has an OID that begins with a QHIN's OID but no managed_by_qhin edge. Under which QHIN is it listed?",
        ["The QHIN whose OID prefix matches", "Its parent's QHIN", "None — it is reported as unresolved with the reason", "The largest QHIN"], 2,
        "No relationship is inferred from an OID. The record is reported as unresolved.")],
    guide=ModuleGuide(
        what_is_this="The delivered hierarchy QHIN → Participant → Subparticipant, as DocuAction records it.",
        why_it_matters="Contract reports list Participants and Subparticipants stratified by QHIN; a wrong QHIN misplaces a finding in the Government's report.",
        what_automation_does="Writes managed_by_qhin and sub_participant_of edges at promotion from the delivered fields; resolves strata from those edges only; reports unresolved records with reasons.",
        what_human_does="Reads the per-QHIN view, notes unresolved counts, and raises a delivery question to the COR when unresolved records are material.",
        steps=[
            "Open the delivery's QHIN view (QHIN Assignment or the delivery detail).",
            "Read population, Participant and Subparticipant counts per QHIN.",
            "Open the unresolved list and read each reason.",
            "Record any question about the delivery for the COR; do not resolve it by hand.",
        ],
        what_not_to_do=[
            "Do not assign a QHIN by name, OID or address.",
            "Do not edit a relationship edge to make a report tidy.",
        ],
        what_happens_next="Sampling plans stratify by these QHINs, and assignment distributes work QHIN by QHIN."))

SAMPLING = Module(
    slug="stratification-and-sampling",
    title="Stratification and Sampling",
    keywords=["sampling", "sample", "stratification", "95%", "confidence", "plan", "population"],
    audience=[Role.ANY],
    objective="Know what the contract requires of the sample, what AGT has proposed, and what is still open.",
    version=CONTENT_VERSION, effective_date=EFFECTIVE,
    lessons=[Lesson(
        slug="requirement-and-proposal", title="What is required, what is proposed",
        objective="Separate the contractual floor from AGT's proposed parameters.",
        body=("The contract requires a statistically representative sample at or above "
              "95% confidence from each QHIN. That sentence is the whole of the "
              "requirement.\n\n"
              "AGT's proposed parameters (95% confidence, ±5% margin, finite population "
              "correction, stratified by QHIN) are a methodology submission awaiting COR "
              "confirmation. A plan drawn with them is an official plan in DocuAction; "
              "its parameters are still AGT's until the COR confirms them.\n\n"
              "A drawn plan freezes its membership. Work created from it carries the "
              "plan id, and contract reports show the plan alongside the lists."),
        common_mistakes=[
            "Describing the ±5% margin as a contract requirement.",
            "Showing 0% complete when no plan has been drawn.",
        ],
        prohibited=[ProhibitedConclusion(
            "The sample parameters are contractually approved.",
            "Only the confidence floor is in the contract. The parameters are AGT's proposal.",
            unblocked_by="written COR confirmation of the sampling methodology")],
        statements=[
            _gov("The review sample must be statistically representative at or above 95% confidence from each QHIN.", _SOW_TASK3 + " and Task 4"),
            _open("Confirmation of AGT's proposed margin, correction and stratification parameters."),
            _impl("A drawn plan freezes its membership; review work carries the plan id."),
        ])],
    checks=[KnowledgeCheck(
        "Which of these is in the contract?",
        ["±5% margin of error", "Finite population correction", "At or above 95% confidence from each QHIN", "383 entities"], 2,
        "The confidence floor per QHIN is the requirement. The rest is AGT's proposal.")],
    guide=ModuleGuide(
        what_is_this="How the review population becomes a drawn, frozen, QHIN-stratified sample.",
        why_it_matters="The Government's lists are only defensible if the sample they come from meets the contractual floor and its parameters are stated honestly.",
        what_automation_does="Computes the sample size from the recorded parameters, draws membership per QHIN stratum, freezes it under a plan id, and reports progress against it.",
        what_human_does="Chooses the plan parameters, records their status (proposed or confirmed), draws the plan, and reads progress.",
        steps=[
            "Confirm the delivery's population resolved by QHIN.",
            "Open the sampling plan form and enter the parameters and their status.",
            "Draw the plan and record the plan id.",
            "Create review work from the plan (next module).",
        ],
        what_not_to_do=[
            "Do not present AGT's parameters as approved.",
            "Do not redraw a plan to change who is in it.",
            "Do not show a completion percentage when no plan exists.",
        ],
        what_happens_next="Review work is created from the plan membership and distributed by QHIN."))

WORK = Module(
    slug="work-creation-and-assignment",
    title="Review Work Creation and Assignment",
    keywords=["assignment", "assign", "work queue", "operations", "supervisor", "workload",
              "case", "claim", "unassigned", "deadline"],
    audience=[Role.ANY],
    objective="Create review work from a plan or a priority request and distribute it visibly.",
    version=CONTENT_VERSION, effective_date=EFFECTIVE,
    lessons=[Lesson(
        slug="work-reasons-and-states", title="Why a case exists and where it stands",
        objective="Read the work reason and the derived state of any case.",
        body=("Every case has a reason it exists: HUMAN_REQUIRED (an automated exception), "
              "STATISTICAL_SAMPLE (plan membership), PRIORITY_REQUEST (a COR request under "
              "Task 5), QA_RETURN or QA_ESCALATION. The reason never changes.\n\n"
              "Its state is derived from the recorded events, not stored: AVAILABLE "
              "(unassigned), CLAIMED (in progress), SUBMITTED_FOR_QA (awaiting QA), "
              "RETURNED, ESCALATED, APPROVED. Supervisor Operations shows the queue with "
              "holder, reason, state, age and QA position; QHIN Assignment distributes "
              "cases to analysts QHIN by QHIN.\n\n"
              "A deadline exists only when the COR gave one for a priority request. "
              "No other case has a deadline, and the contract sets no standing turnaround."),
        common_mistakes=[
            "Reading a case with no deadline as late.",
            "Treating 'oldest work' as a compliance measure.",
        ],
        prohibited=[ProhibitedConclusion(
            "A case past its internal age band is non-compliant.",
            "Age is arithmetic. Only a COR-supplied priority deadline is a deadline, and "
            "even that is a date, not a determination.")],
        statements=[
            _gov("Priority reviews are performed on request from ONC with a deadline communicated per request.", _SOW_TASK5),
            _impl("Case state is derived from append-only events; the work reason is fixed at creation."),
        ])],
    checks=[KnowledgeCheck(
        "A STATISTICAL_SAMPLE case has been open 20 days. What does the operations view say about its deadline?",
        ["Past deadline", "Due soon", "No deadline set", "Escalated"], 2,
        "Only a COR-supplied priority deadline is a deadline. Age is shown; no deadline is invented.")],
    guide=ModuleGuide(
        what_is_this="The supervisor's control plane: work creation, distribution and the live queue.",
        why_it_matters="Visibility of who holds what, for how long, and where QA stands is how a PM keeps the reporting period defensible without inventing service levels.",
        what_automation_does="Creates one case per plan member or priority request, derives state from events, computes age and QA position, and rolls workload up by QHIN and by holder.",
        what_human_does="Distributes cases by QHIN, rebalances holders, watches unassigned and returned work, and records COR deadlines for priority requests.",
        steps=[
            "Open QHIN Assignment for the delivery and plan.",
            "Plan the distribution across analysts and apply it.",
            "Open Supervisor Operations; filter by state, reason or holder.",
            "Open a case to see its full lineage before intervening.",
            "Reassign only through the workflow actions, which record who did it.",
        ],
        what_not_to_do=[
            "Do not set or imply a service level the contract does not contain.",
            "Do not reassign by editing data; use the recorded actions.",
        ],
        what_happens_next="The analyst claims the case and works it in the Review Workbench."))

DETERMINATION = Module(
    slug="determination-and-rationale",
    title="Analyst Determination and Rationale",
    keywords=["determination", "rationale", "analyst", "workbench", "workspace", "category"],
    audience=[Role.ANALYST, Role.QA, Role.PROGRAM_MANAGER, Role.ADMIN],
    objective="Record a determination that a different person can approve and the Government can read.",
    version=CONTENT_VERSION, effective_date=EFFECTIVE,
    lessons=[Lesson(
        slug="writing-the-rationale", title="The rationale is the deliverable",
        objective="Write a rationale that cites evidence and names the category correctly.",
        body=("The system proposes a category from the automated observations; the "
              "analyst decides. The determination names one of the four Government "
              "categories and carries a written rationale that cites the observations "
              "and sources relied on, including any source that could not answer.\n\n"
              "The rationale is what the QA reviewer approves and what the audit trail "
              "preserves. 'Agree with system' is not a rationale. A category chosen "
              "without an evidence citation will be returned.\n\n"
              "B1–B4 is AGT's internal shorthand. It may appear in provenance; it never "
              "appears as the determination's category."),
        common_mistakes=[
            "Confirming the proposed category without reading the evidence.",
            "Using B1–B4 as the category name in the rationale.",
            "Concluding from a source that was unavailable.",
        ],
        prohibited=[ProhibitedConclusion(
            "The entity is non-compliant because the system said so.",
            "Automation observes; the analyst determines; QA approves. The rationale must stand on evidence.")],
        statements=[
            _gov("Discrepancies are reported under the four contractual categories using the Government's wording.", _SOW_TASK3),
            _impl("A determination requires a written rationale; the category is chosen by the analyst, never assigned by the system."),
        ])],
    checks=[KnowledgeCheck(
        "The proposed category is 'inexplicable discrepancies'. NPPES matched, PECOS could not answer. What must the rationale do?",
        ["Repeat the proposed category", "Cite NPPES, record that PECOS was unavailable, and state the category on that basis",
         "Mark the entity non-compliant", "Leave the rationale blank and submit to QA"], 1,
        "The rationale cites what answered and what did not. An unavailable source supports no conclusion either way.")],
    guide=ModuleGuide(
        what_is_this="The analyst's recorded decision about one Participant or Subparticipant.",
        why_it_matters="It is the only thing in the report that a human asserted. Everything before it is observation; everything after it is control.",
        what_automation_does="Assembles the evidence matrix, proposes a category with its rule version, and blocks submission without a rationale.",
        what_human_does="Reads the evidence, decides the category, writes the rationale with citations, and submits for independent QA.",
        steps=[
            "Claim the case in the Review Workbench.",
            "Read every source panel, including unavailable and not-applicable ones.",
            "Choose the Government category.",
            "Write the rationale citing the observations relied on.",
            "Submit to QA.",
        ],
        what_not_to_do=[
            "Do not approve your own determination.",
            "Do not use internal shorthand as the category.",
            "Do not draw a conclusion from a source that did not answer.",
        ],
        what_happens_next="A different person performs independent QA (Maker/Checker; Independent QA)."))

MAKER_CHECKER = Module(
    slug="maker-checker",
    title="Maker / Checker Control",
    keywords=["maker checker", "maker/checker", "segregation of duties", "same person", "reportable"],
    audience=[Role.ANY],
    objective="Understand the segregation of duties that makes a determination reportable.",
    version=CONTENT_VERSION, effective_date=EFFECTIVE,
    lessons=[Lesson(
        slug="two-people", title="Two people, two roles, one record",
        objective="Explain why the same person cannot make and check a determination.",
        body=("The analyst who records a determination is the maker. The QA lead who "
              "approves, returns or escalates it is the checker. DocuAction refuses a QA "
              "action by the same user who made the determination — the request is "
              "rejected, not silently accepted.\n\n"
              "A determination becomes reportable only with a standing QA APPROVE. A later "
              "RETURN or ESCALATE revokes reportability. The contract report lists a "
              "Participant or Subparticipant only while its approval stands.\n\n"
              "The PM release of a report is a third, separate control: it does not "
              "approve determinations and cannot make an unapproved one reportable."),
        common_mistakes=[
            "Assuming an admin can QA their own case because they can see the button.",
            "Reading PM release as approval of the findings.",
        ],
        prohibited=[ProhibitedConclusion(
            "The PM released the report, so every listed determination is approved.",
            "PM release controls the document. Reportability comes from QA, per case.")],
        statements=[
            _impl("Same-person QA is refused by the server regardless of role."),
            _impl("Reportability requires a standing QA APPROVE; RETURN or ESCALATE revokes it."),
        ])],
    checks=[KnowledgeCheck(
        "An admin records a determination and then tries to approve it in QA. What happens?",
        ["It is approved because admins can do everything", "The server refuses it: the checker must be a different person",
         "It is approved but flagged", "It is queued for the PM"], 1,
        "Segregation of duties is enforced server-side for every role.")],
    guide=ModuleGuide(
        what_is_this="The segregation-of-duties control between the person who decides and the person who checks.",
        why_it_matters="The Government receives a list of determinations. Each one must have been checked by someone other than its author for the list to be defensible.",
        what_automation_does="Records maker and checker identities on append-only events and rejects same-person QA.",
        what_human_does="Makers submit; checkers approve, return with reasons, or escalate to a named individual.",
        steps=[
            "Analyst submits a determination for QA.",
            "A different person opens the QA queue and reviews evidence and rationale.",
            "The checker records APPROVE, RETURN (with reason) or ESCALATE (to a person).",
            "The case's reportability updates from that event.",
        ],
        what_not_to_do=[
            "Do not share credentials to get around the control.",
            "Do not treat a returned case as a finding.",
        ],
        what_happens_next="Approved determinations become eligible for the stratified lists in the contract report."))

PM_DELIVERY = Module(
    slug="pm-review-and-delivery",
    title="PM Review and Delivery",
    audience=[Role.PROGRAM_MANAGER, Role.ADMIN, Role.QA],
    objective="Take a generated contract report through PM review to a delivery-ready package.",
    version=CONTENT_VERSION, effective_date=EFFECTIVE,
    keywords=["PM release", "release", "package", "delivery package", "DOCX", "PDF",
              "transmittal", "COR", "logo"],
    history=[ModuleRevision("1.0.0", "2026-09-11",
                            "First version: DRAFT → PM_REVIEWED → READY_FOR_DELIVERY, "
                            "package with HTML/CSV/PDF.", status="superseded")],
    lessons=[Lesson(
        slug="release-and-package", title="Release states and the delivery package",
        objective="Move a report through release and know what the package contains.",
        body=("A generated report starts as DRAFT. The programme manager records "
              "PM_REVIEWED after reading it, then READY_FOR_DELIVERY; either can be "
              "returned to draft with a note. Every transition is an audit event with "
              "the actor's identity.\n\n"
              "The package is a ZIP named by contract, task, deliverable, cadence, "
              "period and report id — for example "
              "7571MN26F80064_Task3_D3.1_Weekly_2026-09-05_2026-09-11_DA-ARC-2026-016.zip. "
              "It contains the editable DOCX, the PDF where the engine is available, the "
              "HTML archive copy, the CSV of the stratified list, a README and a manifest "
              "with the SHA-256 of every member.\n\n"
              "The report identifies the recipient in text. No HHS, ASTP or ONC mark is "
              "placed unless written authorization is on file and the deployment flag is "
              "set; by default it is off. Transmission to the COR happens outside "
              "DocuAction, by the programme manager."),
        common_mistakes=[
            "Sending a DRAFT.",
            "Editing the DOCX and sending it without regenerating the record it came from.",
            "Adding a Government seal to the cover because it looks official.",
        ],
        prohibited=[ProhibitedConclusion(
            "The report is delivered because it is READY_FOR_DELIVERY.",
            "READY_FOR_DELIVERY is a state in DocuAction. Delivery is the act of sending it to the COR, recorded outside the system.")],
        statements=[
            _gov("All reports shall reference and cite the contract number.", _SOW_SECTION_F),
            _impl("PM release is DRAFT → PM_REVIEWED → READY_FOR_DELIVERY, returnable to draft; every step is an audit event."),
            _impl("Government marks are off by default and require written authorization plus a deployment setting; the recipient is named in text."),
        ])],
    checks=[KnowledgeCheck(
        "Which file in the package is the editable electronic copy?",
        ["The PDF", "The HTML", "The DOCX", "The CSV"], 2,
        "DOCX is editable; PDF is the customer-ready rendering; HTML is the archive copy; CSV is the data.")],
    guide=ModuleGuide(
        what_is_this="The programme manager's control over what leaves DocuAction as a contract deliverable.",
        why_it_matters="The Government receives one document per period. Its identity, version, status and provenance must be unambiguous.",
        what_automation_does="Generates the report from QA-approved data, freezes the dataset, builds DOCX/PDF/HTML/CSV from the same record, names the files traceably, and records release transitions.",
        what_human_does="Reads the draft, records PM review, marks it ready, downloads the package, and transmits it to the COR outside the system.",
        steps=[
            "Open Contract Reports and generate the deliverable for the period.",
            "Read the draft (DOCX or PDF) end to end.",
            "Record PM_REVIEWED; return to draft with a note if anything is wrong.",
            "Record READY_FOR_DELIVERY.",
            "Download the package and verify the manifest hashes if required.",
            "Transmit to the COR through the agreed channel and record the transmittal.",
        ],
        what_not_to_do=[
            "Do not place an HHS/ASTP/ONC mark without written authorization on file.",
            "Do not edit generated files and send them as the deliverable.",
            "Do not describe pending-QA records as findings.",
        ],
        what_happens_next="The COR reviews the deliverable; questions come back as COR decisions recorded in the methodology register."))

SECURITY = Module(
    slug="security-and-data-handling",
    title="Security and Government Data Handling",
    keywords=["security", "credentials", "password", "DEV", "PROD", "classification", "PII", "roles", "RBAC"],
    audience=[Role.ANY],
    objective="Handle Government data, credentials and environments correctly.",
    version=CONTENT_VERSION, effective_date=EFFECTIVE,
    lessons=[Lesson(
        slug="environments-and-classification", title="Environments, classification and access",
        objective="Tell DEV from PROD, test data from Government data, and know what your role may do.",
        body=("DocuAction runs as separate DEV and PROD environments with separate "
              "databases. Every report carries a data classification: GOVERNMENT for "
              "official ONC/RCE deliveries, DEVELOPMENT_TEST otherwise. A report over "
              "development data is watermarked NOT FOR GOVERNMENT DELIVERY.\n\n"
              "Roles are a ladder: viewer, reviewer (analyst), qalead, program_manager, "
              "admin. Every read and write is authorised on the server; the navigation "
              "only decides what is offered. Aggregate dashboards carry no entity PII; "
              "entity detail is role-gated.\n\n"
              "Credentials are personal. They are never shared, never typed into a "
              "document, and never pasted into a support request. Audit rows record who "
              "did what; a shared credential makes that record false."),
        common_mistakes=[
            "Testing a workflow against Government data in PROD.",
            "Sharing a login so a colleague can 'just check' a case.",
            "Emailing a report outside the agreed channel.",
        ],
        prohibited=[ProhibitedConclusion(
            "A DEV report can be sent to the COR if the numbers look right.",
            "DEV data is development data. Only a GOVERNMENT-classified report from PROD is a deliverable.")],
        statements=[
            _impl("Reports over non-Government data are watermarked and their package README says NOT FOR GOVERNMENT DELIVERY."),
            _impl("Authorization is enforced server-side for every read and write; navigation is not a control."),
        ])],
    checks=[KnowledgeCheck(
        "A colleague asks for your password to approve a QA item while you are out. What do you do?",
        ["Share it, it is urgent", "Decline; ask the PM to assign the item to a QA lead who can act", "Approve it yourself from home first", "Post the password in the team chat"], 1,
        "Credentials are personal and the audit record depends on it. Reassignment is the recorded path.")],
    guide=ModuleGuide(
        what_is_this="The rules for environments, data classification, roles and credentials.",
        why_it_matters="The programme handles Government data under contract. A single mis-sent file or shared login undermines every control above it.",
        what_automation_does="Separates environments, classifies every report, watermarks non-Government output, enforces roles on every endpoint, and writes append-only audit rows with user identity.",
        what_human_does="Works in the right environment, keeps credentials personal, uses the agreed transmission channel, and reports incidents immediately.",
        steps=[
            "Check the environment banner before any write.",
            "Confirm the data classification on any report before it leaves the system.",
            "Use only your own credentials; sign out on shared machines.",
            "Report a suspected exposure to the programme manager the same day.",
        ],
        what_not_to_do=[
            "Do not use PROD for testing or demonstrations.",
            "Do not share credentials or tokens.",
            "Do not transmit deliverables outside the agreed channel.",
        ],
        what_happens_next="Incidents and access changes are handled by the administrator and recorded in the audit history."))

NEW_MODULES = [DELIVERY, RELATIONSHIPS, SAMPLING, WORK, DETERMINATION,
               MAKER_CHECKER, PM_DELIVERY, SECURITY]

#: Search words for the evidence-vocabulary modules defined elsewhere.
KEYWORDS = {
    "tefca-arc-overview": ["overview", "TEFCA", "ARC", "DocuAction", "programme", "program", "start here"],
    "automated-observations": ["automated processing", "automation", "observation", "processing",
                               "background", "address", "conflict", "source unavailable"],
    "analyst-review": ["analyst", "BA", "review", "triage", "exception", "My Reviews"],
    "evidence-and-sources": ["evidence", "sources", "NPPES", "PECOS", "PPEF", "LEIE", "SAM.gov",
                             "applicability", "external"],
    "qa-review": ["QA", "independent QA", "approve", "return", "escalate", "quality assurance"],
    "discrepancies-and-methodology": ["categories", "discrepancy", "contractual", "B1-B4",
                                      "methodology", "COR decision", "non-compliant", "inexplicable"],
    "auditability": ["audit", "lineage", "provenance", "decision history", "reconstruct"],
    "reports": ["reports", "contract reports", "D3.1", "D3.2", "D4.1", "D5.1", "weekly", "CSV",
                "HTML", "PDF", "DOCX", "stratified list", "draft"],
}

#: Versions of the evidence-vocabulary modules. A module whose text changed in
#: the LMS hardening (2026-09-11) carries the change in its history.
BASE_VERSIONS = {
    "analyst-review": {"version": "1.0.1", "history": [ModuleRevision(
        "1.0.0", "2026-08-24", "Triage counts described as 'current'.", status="superseded")]},
    "qa-review": {"version": "1.1.0", "history": [ModuleRevision(
        "1.0.0", "2026-08-24", "Example described a zero-approval state as current.",
        status="superseded")]},
    "reports": {"version": "1.2.0", "history": [
        ModuleRevision("1.0.0", "2026-08-24", "Five-gate release lesson (superseded control).",
                       status="superseded"),
        ModuleRevision("1.1.0", "2026-09-11", "Guide added; stratified lists, DOCX/PDF/HTML/CSV.",
                       status="superseded")]},
}

# ── the 16-step programme path, in order ────────────────────────────────────

PATH_ORDER = [
    "tefca-arc-overview",              # 1  Overview
    "delivery-and-ingestion",          # 2  ONC/RCE Delivery & Ingestion
    "automated-observations",          # 3  Automated Processing
    "qhin-relationships",              # 4  QHIN / Participant / Subparticipant
    "stratification-and-sampling",     # 5  Stratification & Sampling
    "work-creation-and-assignment",    # 6  Review Work Creation & Assignment
    "analyst-review",                  # 7  BA / Analyst Review
    "evidence-and-sources",            # 8  Evidence and External Sources
    "determination-and-rationale",     # 9  Analyst Determination & Rationale
    "maker-checker",                   # 10 Maker / Checker
    "qa-review",                       # 11 Independent QA
    "discrepancies-and-methodology",   # 12 Four Contractual Discrepancy Categories
    "auditability",                    # 13 Audit & Decision Lineage
    "reports",                         # 14 Contract Reports
    "pm-review-and-delivery",          # 15 PM Review & Delivery
    "security-and-data-handling",      # 16 Security / Government Data Handling
]

# ── guides for the modules defined in learning_content / learning_methodology ─

GUIDES = {
    "tefca-arc-overview": ModuleGuide(
        what_is_this="The TEFCA ARC programme and DocuAction's place in it.",
        why_it_matters="Every operator must be able to say what the system decides (nothing) and what people decide (everything reportable).",
        what_automation_does="Collects, evidences and observes; proposes categories with rule versions; never records a finding.",
        what_human_does="Determines, checks, releases.",
        steps=["Read the programme overview.", "Learn the four Government categories by name.", "Locate your role's path in the Learning Center."],
        what_not_to_do=["Do not call an observation a finding.", "Do not use B1–B4 outside AGT."],
        what_happens_next="Follow your role's learning path."),
    "automated-observations": ModuleGuide(
        what_is_this="What the automated processing records for every applicable source and every record.",
        why_it_matters="Analysts must read the eight observation states correctly; four of them are never adverse.",
        what_automation_does="Runs every applicable source lookup, records the observation with provenance, triages exceptions, and proposes a category.",
        what_human_does="Reads observations as evidence, not as conclusions, and adjudicates exceptions.",
        steps=["Open a case's evidence matrix.", "Read each source's observation state and provenance.", "Note any unavailable or not-applicable source."],
        what_not_to_do=["Do not treat SOURCE_UNAVAILABLE or NOT_APPLICABLE as adverse."],
        what_happens_next="The analyst review begins from these observations."),
    "analyst-review": ModuleGuide(
        what_is_this="The analyst's work on a case from claim to submission.",
        why_it_matters="This is where the Government's list is actually decided, one Participant or Subparticipant at a time.",
        what_automation_does="Routes triaged cases, presents the evidence matrix and the proposed category, and enforces the rationale requirement.",
        what_human_does="Reviews evidence, records the determination with rationale, submits for QA.",
        steps=["Claim the case.", "Read every source panel.", "Record determination and rationale.", "Submit to QA."],
        what_not_to_do=["Do not skip a source panel.", "Do not approve your own work."],
        what_happens_next="Independent QA."),
    "evidence-and-sources": ModuleGuide(
        what_is_this="The external sources DocuAction consults and what each can and cannot establish.",
        why_it_matters="A conclusion is only as strong as the source it cites and the source's own limits.",
        what_automation_does="Decides applicability before any lookup, records source edition and hash, and records limitations rather than guessing.",
        what_human_does="Reads the source guide before relying on a source; records a limitation in the rationale.",
        steps=["Open the source guide for each source in the matrix.", "Check applicability and edition.", "Cite the source in the rationale."],
        what_not_to_do=["Do not infer licensing from NPPES.", "Do not conclude from an unavailable source."],
        what_happens_next="Determination and rationale."),
    "qa-review": ModuleGuide(
        what_is_this="Independent QA of an analyst's determination.",
        why_it_matters="Only a standing QA APPROVE makes a determination reportable.",
        what_automation_does="Presents the case, evidence and rationale to a different person; refuses same-person QA; records the decision event.",
        what_human_does="Approves, returns with a reason, or escalates to a named individual.",
        steps=["Open the QA queue.", "Read evidence and rationale.", "Record APPROVE, RETURN or ESCALATE."],
        what_not_to_do=["Do not approve on the analyst's reputation.", "Do not return without a reason."],
        what_happens_next="Approved cases become eligible for the contract report."),
    "discrepancies-and-methodology": ModuleGuide(
        what_is_this="The four contractual discrepancy categories and the open methodology decisions.",
        why_it_matters="The Government's wording is the report's wording; an open decision must be reported as open.",
        what_automation_does="Labels categories with the Government's wording, shows internal shorthand only as provenance, and flags methodology-pending conditions.",
        what_human_does="Names the category correctly and never resolves an open decision by default.",
        steps=["Learn the four category names verbatim.", "Read the COR decision register.", "Record pending conditions as pending."],
        what_not_to_do=["Do not use B1–B4 as a category name.", "Do not treat pending as no problem."],
        what_happens_next="Audit and decision lineage preserve every determination and its basis."),
    "auditability": ModuleGuide(
        what_is_this="How any number in a report is reconstructed from recorded events.",
        why_it_matters="A COR question about a figure must be answerable from the record, not from memory.",
        what_automation_does="Writes append-only decision events and audit rows with identity, freezes report datasets, and hashes every artefact.",
        what_human_does="Reads the audit history and reconstructs a figure when asked.",
        steps=["Open Audit & Decision History.", "Trace a case from creation to QA.", "Match the report's provenance record to the stored dataset."],
        what_not_to_do=["Do not edit history.", "Do not regenerate a delivered report to 'fix' a number."],
        what_happens_next="Contract reports are generated from this record."),
    "reports": ModuleGuide(
        what_is_this="The contract report families and what each contains.",
        why_it_matters="Task 3 weekly and final reports are stratified lists of Participants and Subparticipants under the four categories; a count is not a list.",
        what_automation_does="Builds the stratified lists from QA-approved determinations, renders cover, document control and numbered sections, and produces DOCX, PDF, HTML and CSV from one frozen dataset.",
        what_human_does="Chooses the period and report family, adds suggested and implemented methodology changes, and reads the draft.",
        steps=["Open Contract Reports.", "Choose the family and period.", "Enter suggested (and, for the final, implemented) changes.", "Generate and read the draft."],
        what_not_to_do=["Do not report pending-QA records as findings.", "Do not change the Government's category wording."],
        what_happens_next="PM review and delivery."),
}

# ── role paths ──────────────────────────────────────────────────────────────

PATHS = [
    LearningPath(
        slug="program-manager", title="Program Manager path", role=Role.PROGRAM_MANAGER,
        description="From delivery to delivered report: population, sampling, distribution, controls, reporting and release.",
        module_slugs=PATH_ORDER),
    LearningPath(
        slug="analyst", title="BA / Analyst path", role=Role.ANALYST,
        description="What reaches you, how to read evidence, how to record a determination a checker can approve.",
        module_slugs=["tefca-arc-overview", "automated-observations", "qhin-relationships",
                      "work-creation-and-assignment", "analyst-review", "evidence-and-sources",
                      "determination-and-rationale", "maker-checker",
                      "discrepancies-and-methodology", "auditability",
                      "security-and-data-handling"]),
    LearningPath(
        slug="qa-lead", title="Independent QA path", role=Role.QA,
        description="The checker's view: evidence, rationale, the four categories, and what approval does and does not mean.",
        module_slugs=["tefca-arc-overview", "automated-observations", "evidence-and-sources",
                      "determination-and-rationale", "maker-checker", "qa-review",
                      "discrepancies-and-methodology", "auditability", "reports",
                      "security-and-data-handling"]),
    LearningPath(
        slug="administrator", title="Administrator path", role=Role.ADMIN,
        description="Environments, roles, deliveries, and every control the platform enforces.",
        module_slugs=["tefca-arc-overview", "security-and-data-handling", "delivery-and-ingestion",
                      "qhin-relationships", "work-creation-and-assignment", "maker-checker",
                      "auditability", "pm-review-and-delivery"]),
    LearningPath(
        slug="viewer", title="Viewer / COR read-only path", role=Role.ANY, read_only=True,
        description="What the screens and reports mean, without operating them.",
        module_slugs=["tefca-arc-overview", "qhin-relationships", "stratification-and-sampling",
                      "discrepancies-and-methodology", "reports", "auditability"]),
]

# ── feature → training traceability ─────────────────────────────────────────

_ALL = [Role.ANY]
_OPS = [Role.PROGRAM_MANAGER, Role.ADMIN]
_AN = [Role.ANALYST, Role.QA, Role.PROGRAM_MANAGER, Role.ADMIN]


def _link(feature, screen, route, roles, module_slug, lesson_slug=None):
    return FeatureLink(feature=feature, screen=screen, route=route, roles=roles,
                       module_slug=module_slug, lesson_slug=lesson_slug,
                       last_verified_version=CONTENT_VERSION,
                       last_updated=EFFECTIVE, owner=OWNER)


FEATURES = [
    _link("Register an ONC/RCE delivery", "ONC/RCE Deliveries", "/tefca-arc/deliveries", _OPS,
          "delivery-and-ingestion", "register-a-delivery"),
    _link("Delivery population by QHIN", "QHIN Assignment", "/tefca-arc/assignment", _OPS,
          "qhin-relationships", "delivered-edges"),
    _link("Draw a sampling plan", "QHIN Assignment", "/tefca-arc/assignment", _OPS,
          "stratification-and-sampling", "requirement-and-proposal"),
    _link("Distribute review work", "QHIN Assignment", "/tefca-arc/assignment", _OPS,
          "work-creation-and-assignment", "work-reasons-and-states"),
    _link("Work queue, holders, age and QA position", "Supervisor Operations", "/tefca-arc/operations", _OPS,
          "work-creation-and-assignment", "work-reasons-and-states"),
    _link("Claim and work a case", "My Reviews", "/tefca-arc/my-reviews", _AN,
          "analyst-review"),
    _link("Evidence matrix and source panels", "Review Workbench", "/tefca-arc/workspace", _AN,
          "evidence-and-sources"),
    _link("Record determination and rationale", "Review Workbench", "/tefca-arc/workspace", _AN,
          "determination-and-rationale", "writing-the-rationale"),
    _link("Independent QA decision (APPROVE / RETURN / ESCALATE)", "Review Workbench, section H", "/tefca-arc/workspace",
          [Role.QA, Role.PROGRAM_MANAGER, Role.ADMIN], "qa-review"),
    _link("Platform health checks (technical QA, not review QA)", "Platform Health & Technical QA", "/tefca-arc/qa",
          [Role.QA, Role.PROGRAM_MANAGER, Role.ADMIN], "security-and-data-handling"),
    _link("System and data-quality issues (observations, not findings)", "Data-Quality Issues", "/tefca-registry/issues",
          _ALL, "automated-observations"),
    _link("Findings by Government category", "Findings", "/tefca-arc/findings", _ALL,
          "discrepancies-and-methodology"),
    _link("Generate a contract report", "Contract Reports", "/tefca-arc/reports", _OPS,
          "reports"),
    _link("PM release and delivery package (DOCX/PDF/HTML/CSV)", "Contract Reports", "/tefca-arc/reports", _OPS,
          "pm-review-and-delivery", "release-and-package"),
    _link("Audit and decision history", "Audit & Decision History", "/tefca-arc/audit", [Role.QA, Role.PROGRAM_MANAGER, Role.ADMIN],
          "auditability"),
    _link("Learning Center", "Learning Center", "/tefca-arc/help", _ALL,
          "tefca-arc-overview"),
]

# ── reference library ───────────────────────────────────────────────────────

LIBRARY = [
    LibraryItem(
        title="Contract 7571MN26F80064 — Statement of Work (Section C) and reporting clauses (Section F)",
        authority=Authority.CONTRACT_SOW,
        summary="The executed contract: Tasks 3, 4 and 5, the four discrepancy categories, the sampling confidence floor, deliverable families and the requirement to cite the contract number.",
        reference="Executed contract on file (solicitation 7571MN26Q00038).",
        module_slugs=["tefca-arc-overview", "stratification-and-sampling", "discrepancies-and-methodology", "reports"]),
    LibraryItem(
        title="AGT Review Methodology (Deliverable D2)",
        authority=Authority.PROPOSED_METHODOLOGY,
        summary="AGT's methodology submission: sampling parameters, source applicability, address materiality, category mapping.",
        reference="Deliverable D2 on file.",
        module_slugs=["stratification-and-sampling", "discrepancies-and-methodology"],
        note="Submitted, not accepted: no written COR acceptance is on file. It binds AGT's own practice only and is never cited as a Government requirement."),
    LibraryItem(
        title="COR Decision Register (open methodology decisions)",
        authority=Authority.PROGRAM_GUIDANCE,
        summary="The list of methodology questions put to the COR and their status. An open item is reported as open.",
        reference="/api/tefca/methodology/status",
        module_slugs=["discrepancies-and-methodology"]),
    LibraryItem(
        title="TEFCA Common Agreement and QHIN Technical Framework (RCE)",
        authority=Authority.RCE_GOVERNING_MATERIAL,
        summary="The Recognized Coordinating Entity's governing material for QHINs, Participants and Subparticipants.",
        reference="TEFCA governing documents as published by the RCE (copies on file with the programme).",
        module_slugs=["qhin-relationships"],
        note="Governs TEFCA participation. It is not the ARC contract and imposes no reporting requirement on AGT."),
    LibraryItem(
        title="NPPES NPI Registry",
        authority=Authority.EVIDENCE_SOURCE_GUIDE,
        summary="CMS registry of NPI identity, practice location and taxonomy. Enumeration only; not licensing or credentialing.",
        reference="https://npiregistry.cms.hhs.gov/",
        module_slugs=["evidence-and-sources"]),
    LibraryItem(
        title="CMS Medicare Fee-For-Service Public Provider Enrollment (PECOS / PPEF)",
        authority=Authority.EVIDENCE_SOURCE_GUIDE,
        summary="CMS's published Medicare enrolment extract and sub-files.",
        reference="https://data.cms.gov/",
        module_slugs=["evidence-and-sources"]),
    LibraryItem(
        title="HHS OIG List of Excluded Individuals and Entities (LEIE)",
        authority=Authority.EVIDENCE_SOURCE_GUIDE,
        summary="Exclusions from federal health care programmes.",
        reference="https://oig.hhs.gov/exclusions/",
        module_slugs=["evidence-and-sources"]),
    LibraryItem(
        title="SAM.gov entity registration and exclusions",
        authority=Authority.EVIDENCE_SOURCE_GUIDE,
        summary="Federal registration and exclusion records. Not evaluated in the current run: no credential is configured.",
        reference="https://sam.gov/",
        module_slugs=["evidence-and-sources"],
        note="Source limitation: unavailable in the current configuration."),
    LibraryItem(
        title="HHS logo policy for contractors",
        authority=Authority.FEDERAL_GUIDANCE,
        summary="Contractors may not use the HHS logo, seal or symbol on proposals or consulting deliverables; the exception is an HHS publication produced under the project officer's direction with ASPA approval.",
        reference="hhs.gov › Web policies › Logo policies: contractors",
        module_slugs=["pm-review-and-delivery"],
        note="Why DocuAction reports name the recipient in text and place no Government mark by default."),
    LibraryItem(
        title="Section 508 — creating accessible documents",
        authority=Authority.FEDERAL_GUIDANCE,
        summary="Federal guidance on accessible Word and PDF documents: headings, reading order, table headers, document title and language.",
        reference="https://www.section508.gov/create/documents/",
        module_slugs=["reports", "pm-review-and-delivery"],
        note="DocuAction designs for these criteria. Full Section 508 conformance is not claimed from automated checks alone."),
    LibraryItem(
        title="U.S. Web Design System (USWDS)",
        authority=Authority.FEDERAL_GUIDANCE,
        summary="Design system for federal websites; the report stylesheet borrows its typographic and colour conventions.",
        reference="https://designsystem.digital.gov/",
        module_slugs=["reports"],
        note="A design reference, not a contractual requirement for ARC deliverables."),
    LibraryItem(
        title="WCAG 2.2",
        authority=Authority.FEDERAL_GUIDANCE,
        summary="Web Content Accessibility Guidelines; the standard the application and report HTML are checked against.",
        reference="https://www.w3.org/TR/WCAG22/",
        module_slugs=["reports"]),
    LibraryItem(
        title="DocuAction TEFCA ARC Learning Center",
        authority=Authority.TRAINING,
        summary="This content: modules, role paths, contextual help, glossary and prohibited conclusions, versioned with the software.",
        reference="/tefca-arc/help",
        module_slugs=["tefca-arc-overview"]),
    LibraryItem(
        title="TEFCA User Operations Guide",
        authority=Authority.TRAINING,
        summary="Screen-by-screen operations guide (signing in, dashboards, the daily procedure, troubleshooting).",
        reference="docs/TEFCA_USER_OPERATIONS_GUIDE.md (backend repository)",
        module_slugs=["tefca-arc-overview"]),
    LibraryItem(
        title="Cochran (1977) sample size for proportions with finite population correction",
        authority=Authority.RESEARCH_INDUSTRY,
        summary="The statistical basis AGT used to propose its sample sizes.",
        reference="Cochran, W. G. Sampling Techniques, 3rd ed.",
        module_slugs=["stratification-and-sampling"],
        note="Industry reference. It is not an ONC requirement and does not decide the programme's parameters."),
]

# ── contextual help for the screens that gained 'Learn more' links ──────────

EXTRA_HELP = [
    ContextualHelp(
        key="delivery.register",
        what_is_this="The official ONC/RCE delivery, registered exactly as received.",
        why_am_i_seeing_it="Every review and every report traces to a registered delivery.",
        allowed_actions=["Register the file as received", "Read the processing summary", "Open the QHIN view"],
        prohibited_conclusions=[ProhibitedConclusion(
            "Registering the delivery reviews it.",
            "Review work exists only after a plan is drawn or a priority request is recorded.")],
        evidence_location="rce_source_intakes and the immutable Area 1 rows",
        audience=[Role.ANY],
        learn_more="delivery-and-ingestion/register-a-delivery",
        statements=[_impl("The delivered file is stored byte for byte and never edited.")]),
    ContextualHelp(
        key="workbench.determination",
        what_is_this="Your recorded decision about this Participant or Subparticipant, with its rationale.",
        why_am_i_seeing_it="The case reached you through a plan, an exception or a priority request and needs a human determination.",
        allowed_actions=["Read every source panel", "Choose the Government category", "Write a rationale citing evidence", "Submit to QA"],
        prohibited_conclusions=[ProhibitedConclusion(
            "The system's proposed category is the determination.",
            "The proposal is provenance. The determination is yours and needs a rationale.")],
        evidence_location="review_decision_events (append-only)",
        audience=[Role.ANALYST, Role.QA, Role.PROGRAM_MANAGER, Role.ADMIN],
        learn_more="determination-and-rationale/writing-the-rationale"),
    ContextualHelp(
        key="assignment.distribution",
        what_is_this="Distribution of review cases across analysts, QHIN by QHIN.",
        why_am_i_seeing_it="A drawn plan created cases that need holders.",
        allowed_actions=["Plan a distribution", "Apply it", "Reassign through the recorded action"],
        prohibited_conclusions=[ProhibitedConclusion(
            "An unassigned case is late.",
            "No standing turnaround exists. Only a COR priority deadline is a deadline.")],
        evidence_location="case assignment events on each review record",
        audience=[Role.PROGRAM_MANAGER, Role.ADMIN],
        learn_more="work-creation-and-assignment/work-reasons-and-states"),
    ContextualHelp(
        key="report.package",
        what_is_this="The delivery package: DOCX, PDF where available, HTML, CSV, README and manifest, named by contract, task, deliverable, period and report id.",
        why_am_i_seeing_it="A report exists for this period and can be packaged for the COR once PM review is complete.",
        allowed_actions=["Download the package", "Verify manifest hashes", "Transmit through the agreed channel"],
        prohibited_conclusions=[ProhibitedConclusion(
            "A downloaded package has been delivered.",
            "Delivery is the transmittal to the COR, recorded outside DocuAction.")],
        evidence_location="manifest.json inside the package; release history on the report",
        audience=[Role.PROGRAM_MANAGER, Role.ADMIN, Role.QA],
        learn_more="pm-review-and-delivery/release-and-package",
        statements=[_impl("No Government mark is placed unless written authorization is on file and the deployment flag is set.")]),
]
