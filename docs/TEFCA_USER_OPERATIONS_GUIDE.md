# DocuAction TEFCA — User & Operations Guide

**TEFCA Audit, Review & Compliance**  
**Contract 7571MN26F80064**  
**Alliance Global Tech, Inc.**  
**Document Version 1.0 — 10 August 2026**  
**Classification: CONFIDENTIAL — Internal & Government Use**  

---

## Table of Contents

- [1. Document Purpose](#1-document-purpose)
- [2. One-Page Daily Quick Start](#2-one-page-daily-quick-start)
- [3. What DocuAction TEFCA Does](#3-what-docuaction-tefca-does)
- [4. Understanding the End-to-End Workflow](#4-understanding-the-end-to-end-workflow)
- [5. Where the Data Comes From (Data Lineage)](#5-where-the-data-comes-from-data-lineage)
- [6. User Roles and Responsibilities](#6-user-roles-and-responsibilities)
- [7. Logging In and Getting Started](#7-logging-in-and-getting-started)
- [8. Dashboard Guide](#8-dashboard-guide)
- [9. Daily Operating Procedure](#9-daily-operating-procedure)
- [10. Entity Review — Step-by-Step](#10-entity-review--step-by-step)
- [11. Verification Services and Data Sources](#11-verification-services-and-data-sources)
- [12. Understanding Results and Statuses](#12-understanding-results-and-statuses)
- [13. Exceptions and Human Review](#13-exceptions-and-human-review)
- [14. AI-Assisted Analysis (What It Does and Does Not Do)](#14-ai-assisted-analysis-what-it-does-and-does-not-do)
- [15. QA and Approval](#15-qa-and-approval)
- [16. Reports and Outputs](#16-reports-and-outputs)
- [17. Development vs Production](#17-development-vs-production)
- [18. Administrator Operations](#18-administrator-operations)
- [19. Audit and Traceability](#19-audit-and-traceability)
- [20. Troubleshooting](#20-troubleshooting)
- [21. User Security Responsibilities](#21-user-security-responsibilities)
- [22. What Users Must Not Do](#22-what-users-must-not-do)
- [23. Daily, Weekly, and Monthly Checklists](#23-daily-weekly-and-monthly-checklists)
- [24. End-to-End Example Walkthrough](#24-end-to-end-example-walkthrough)
- [25. Frequently Asked Questions](#25-frequently-asked-questions)
- [26. Glossary](#26-glossary)

---

# 1. Document Purpose

> ⚠️ **WARNING — PRE-PRODUCTION DEMONSTRATION MODE**
> DocuAction TEFCA is currently operating in pre-production demonstration mode. Both development and production environments serve synthetic demonstration data. No ONC entity population data has been received. All verification results shown are operational readiness demonstrations and must NOT be treated as official production verification determinations.
> The system stamps every report with: "MOCK — demonstration data only. Do not use for operational decisions."
> When ONC provides the initial entity population data and the entity-data configuration key is set, the system transitions to production verification mode and this warning is removed automatically.

## 1.1 What this guide is

This is the operating manual for the DocuAction TEFCA platform as it exists today. It was written by inspecting the running application — its code, configuration, database models, API routes, connectors, and live production endpoints — rather than from earlier design documents. Where an older document disagrees with the running system, this guide follows the running system and says so.

## 1.2 Who this guide is for

| Audience | Read first |
|---|---|
| HHS/ONC program staff and new users | Sections 2, 3, 4, 7, 8, 12 |
| Analysts and reviewers | Sections 9, 10, 11, 12, 13, 24 |
| QA leads | Sections 12, 15, 23 |
| Administrators and technical support | Sections 17, 18, 19, 20 |
| Auditors and oversight reviewers | Sections 5, 14, 17, 19 |

The guide is written to be understood by a program user with limited technical background. Deeper technical material is separated into clearly marked **Technical / Administrator Notes** subsections, which ordinary users may skip.

## 1.3 Accuracy commitments

This document follows four rules without exception:

1. A capability is described only if it is implemented and reachable in the current build.
2. Demonstration, disabled, and planned functions are labelled as such and never presented as production capability.
3. No credential, secret, key, token, or connection string appears anywhere in this document.
4. Where a claim could not be substantiated from the system itself, the text says so plainly rather than filling the gap.

## 1.4 What this guide does not do

It is not a compliance attestation, a security assessment, or an authorization document. Statements about security categorization, control implementation, and accessibility appear in Sections 17 and 19 in the precise form the evidence supports, and no stronger.

---

# 2. One-Page Daily Quick Start

**DOCUACTION TEFCA — DAILY QUICK START**

| # | Step | Where |
|---|---|---|
| 1 | **Log in** with your organizational account | `/login` |
| 2 | **Check the dashboard** — open items, overdue reviews, connector state | Mission Control (`/tefca-arc`) |
| 3 | **Review your assigned workload** | Entity Reviews (`/tefca-arc/reviews`) |
| 4 | **Process each entity** — open it, read the evidence | Entity Reviews → open a record |
| 5 | **Review authoritative verification results** — check every source's state, not just the headline | Entity detail → Verification Results |
| 6 | **Investigate exceptions** — anything not cleanly resolved | Validation Queue (`/tefca-arc/validation`) |
| 7 | **Document findings** — record the rationale, always | Entity detail → Resolution |
| 8 | **Submit or escalate** | Entity detail → Resolve / Escalate |
| 9 | **QA and approval** (QA lead) | QA Operations (`/tefca-arc/qa`) |
| 10 | **Confirm completion** — verify nothing is left open or overdue | Mission Control |

## 2.1 Reading the status colours

The interface uses colour together with a text label and an icon, never colour alone. Always read the label.

**GREEN / NORMAL — no intervention needed**

- `Live` — a data source responded and is healthy
- `Verified` — the source confirmed the record
- `Active` — the control or capability is operating
- Bucket **B1 — No Discrepancy** at high confidence

**YELLOW / REVIEW — needs your attention**

- `Partial` — the capability works, but with a stated limitation
- `Unavailable` — a source is not configured or not reachable; this is **not** a pass
- `Pending` — a framework or item is not yet substantiated
- Buckets **B2 — Minor or Administrative** and **B3 — Inexplicable**
- SLA band `at_risk` (2 days or fewer remaining)

**RED / EXCEPTION — escalate**

- `Error` — a source that normally answers has failed
- Bucket **B4 — Non-Compliant**
- Any active exclusion or debarment finding
- SLA band `overdue`

**GREY / INDETERMINATE — cannot be judged**

- `Unable to Verify` — health could not be established
- `Indeterminate — Source Unavailable` — a required source did not answer, so the result cannot be trusted in either direction

> ⚠️ **WARNING**
> `Unavailable` and `Indeterminate` are never a pass. A record whose sources did not answer has not been verified and must not be closed as clean.

---

# 3. What DocuAction TEFCA Does

## 3.1 In one paragraph

DocuAction TEFCA takes a list of healthcare organizations that participate in national health-data exchange, checks each one against authoritative federal registries, and produces a documented, defensible determination about whether the organization's registered details are accurate and whether it is eligible to participate. Where the automated checks cannot settle a question, the record is routed to a trained human reviewer, whose decision — with a written rationale — becomes the record of account.

## 3.2 Why the system exists

TEFCA — the Trusted Exchange Framework and Common Agreement — is the national framework under which health information networks exchange patient data. Participation is organized in a hierarchy: **QHINs** at the top, then **Participants**, then **Subparticipants**. Each entity is registered with identifying details: legal name, address, National Provider Identifier, and TEFCA identifiers.

That registry is only as trustworthy as its contents. An organization may have closed, changed name, moved, let its federal registration lapse, or been excluded from federal health programs — and the registry may not reflect any of it. Under contract 7571MN26F80064, Alliance Global Tech performs independent audit and review of those registered entities on behalf of the government.

Doing that at scale by hand is not feasible. DocuAction automates the mechanical parts — querying federal sources, comparing fields, applying classification rules, retaining evidence — so that trained reviewers spend their time on the cases that actually require judgement, and so that every determination carries an audit trail a federal auditor can follow.

## 3.3 What the system does *not* do

| It does not | Why this matters |
|---|---|
| Make final compliance determinations automatically for anything other than a clean, fully corroborated record | Human accountability is preserved for every case that is not unambiguous |
| Replace an authoritative source lookup with an estimate, an inference, or an AI-generated answer | The evidence in the record must be what a federal source actually returned |
| Treat an unavailable source as a passing result | A source outage is a gap in evidence, never a clean result |
| Modify or delete an entity's history | Records are added to; they are not rewritten |

## 3.4 The two areas of the application

The platform presents two TEFCA areas in the left navigation. **They do different things, and confusing them is the most common new-user error.**

| | **TEFCA ARC** | **TEFCA Registry** |
|---|---|---|
| Navigation section | `TEFCA ARC` | `TEFCA REGISTRY` |
| Purpose | Audit, Review & Compliance — the contract work | Entity registry browsing and structural data quality |
| Queries federal sources? | **Yes** | **No** |
| Produces contract determinations? | **Yes** | No |
| Produces deliverable reports? | **Yes** | No |

> ⚠️ **WARNING — REGISTRY "VERIFY" IS NOT AUTHORITATIVE VERIFICATION**
> The Registry Verify function performs internal structural data-quality checks only. It does NOT query external authoritative sources. External verification is performed through the ARC module.
> A Registry verification result tells you whether an entity's record is internally well-formed — identifiers present and valid, hierarchy intact, no duplicates. It tells you nothing about whether the organization exists, is active, or is excluded. Never present a Registry result as a verification determination.

**Technical / Administrator Note.** The two areas are separate backend modules with separate routers: ARC at `/api/v1/tefca/*` and `/api/tefca/*`; Registry at `/api/tefca/registry/*` and `/api/tefca/arc/*`. The Registry verification engine implements identity and hierarchy rules only; its external-source path is present as plumbing and records a `skipped` result with an explanatory note rather than calling any source.

---

# 4. Understanding the End-to-End Workflow

> ⚠️ **WARNING — PRE-PRODUCTION DEMONSTRATION MODE**
> The workflow below is fully implemented and operating, but it is currently running against synthetic demonstration data on both environments. No ONC entity population data has been received. Results produced today demonstrate operational readiness; they are not official production verification determinations. Every report is stamped "MOCK — demonstration data only. Do not use for operational decisions."

## 4.1 The workflow as implemented

```
  ENTITY POPULATION
  Entity data provided by ONC
  (currently: bundled demonstration dataset)
            |
            v
  IMPORT
  CSV, Excel, JSON, or FHIR R4 bundle upload
  Schema validation - duplicate detection - batch recorded
            |
            v
  SAMPLE SELECTION
  Cochran sample size with finite population correction
  Seeded and reproducible; seed stored with the sample
            |
            v
  SOURCE VERIFICATION (concurrent)
  NPPES  --  OIG LEIE  --  Medicare enrollment indicator  --  SAM.gov
  Each source returns one of five states:
  verified | not_found | not_checked | unavailable | failed
            |
            v
  COMPARISON
  Name matching - address cross-reference - NPI validity
  identifier duplication - entity type
            |
            v
  CLASSIFICATION
  Findings -> Bucket (B1..B4, worst finding wins) -> Confidence
            |
            v
  COVERAGE TEST                     +--> any required source unavailable?
  Were all required sources          |    YES -> INDETERMINATE
  actually answered?                 |    Never auto-classified
            |                        |    Always routed to a human
            v                        |
  TIER ROUTING <---------------------+
  Tier 1  B1 + confidence >= 0.95 + full coverage -> auto-complete
  Tier 2  B2, B3, indeterminate, or low confidence -> analyst review
  Tier 3  B4 -> escalation
            |
            v
  HUMAN REVIEW
  Analyst confirms the classification or reclassifies it
  Written rationale is mandatory
            |
            v
  QA GATES
  Evidence completeness - internal consistency - sampling validity
  inter-rater agreement - SLA - regression - report gate
            |
            v
  REPORTING
  Weekly - Biweekly - Quarterly - Final
  CSV, PDF, DOCX, Excel, HTML
            |
            v
  AUDIT RECORD
  Every step written append-only with actor, timestamp, and detail
```

## 4.2 Why each step exists

| Step | Why it is there |
|---|---|
| **Import with validation** | Bad input silently becomes bad findings. Schema validation and duplicate detection catch the problem at the door, and the import batch is recorded so any later finding can be traced to the file it came from. |
| **Statistical sampling** | The registry population is far too large to review entity by entity. A Cochran sample with finite population correction gives a defensible, quantified confidence level. Seeding the draw means a reviewer can reproduce it — "trust me, it was random" is not evidence. |
| **Concurrent source queries** | Sources are queried in parallel because a sequential run over thousands of entities would not finish inside a review cycle. Each source's result is recorded separately so a later reader can see exactly who said what. |
| **Five source states, not two** | The critical distinction is between *the source says no* and *the source did not answer*. Collapsing those into a single "fail" would count a federal outage against an entity. The system keeps them apart end to end. |
| **Worst-finding-wins bucketing** | One serious problem is not offset by several clean checks. An active exclusion makes the entity B4 regardless of how well everything else matched. |
| **The coverage test** | This is the control that prevents the most dangerous failure mode: a record looking clean because the source that would have flagged it was down. If a required source did not answer, the result is marked Indeterminate and can never be auto-completed. |
| **Tier routing** | Only an unambiguous, fully corroborated, high-confidence clean record completes without a human. Everything else reaches a person. |
| **Mandatory rationale** | A determination without a recorded reason cannot be defended a year later, and cannot be reviewed by QA. |
| **QA gates** | Independent checks on the review process itself — not the entities — so systematic reviewer error is caught before it reaches a deliverable. |
| **Append-only audit** | Every actor, action, and timestamp is retained so the government can reconstruct how any determination was reached. |

---

# 5. Where the Data Comes From (Data Lineage)

This section is the one most often requested by oversight reviewers. It states precisely which external systems the platform contacts, what each provides, and what happens when one is not available.

## 5.1 Summary of authoritative coverage

> ℹ️ **NOTE — EFFECTIVE INDEPENDENT AUTHORITATIVE SOURCES: 2**
> **NPPES** (CMS NPI Registry) — provider identity
> **OIG LEIE** (HHS Exclusion List) — exclusion status
> **Configured but non-functional: 1** — SAM.gov, upstream issue
> The Medicare enrollment indicator is derived from the same NPPES endpoint and is not an independent third source.

| Source | Status | Independent? |
|---|---|---|
| NPPES — CMS NPI Registry | **PRODUCTION** | Yes |
| OIG LEIE — HHS Exclusion List | **PRODUCTION** | Yes |
| Medicare enrollment indicator | **PRODUCTION** | No — same NPPES endpoint |
| SAM.gov — GSA federal registration | **CONFIGURED, NON-FUNCTIONAL** | Would be |
| USPS Address APIs v3 | **PRODUCTION (configured)** | Supporting |
| Address normalizer (code-only) | **PRODUCTION** | Supporting |
| Name matching | **PRODUCTION** | Supporting |
| AI entity resolution | **DISABLED** | Advisory only when enabled |
| RCE Directory (Sequoia) | **NOT CONFIGURED** | Pending |
| IQVIA OneKey | **NOT CONFIGURED** | Pending procurement |
| State licensure registries | **NOT IMPLEMENTED** | Roadmap |
| IRS | **NOT APPLICABLE** | No public API exists for this purpose |

## 5.2 Data flow

```
  EXTERNAL AUTHORITATIVE SOURCES
  +---------------+  +---------------+  +---------------+
  | NPPES         |  | OIG LEIE      |  | SAM.gov       |
  | CMS/HHS       |  | HHS OIG       |  | GSA (down)    |
  | live API      |  | live          |  | 404 upstream  |
  +-------+-------+  +-------+-------+  +-------+-------+
          |                  |                  |
          +--------+---------+------------------+
                   v
          CONNECTOR LAYER
          Timeouts - bounded retries - per-call logging
          Five-state result - never raises into the pipeline
                   |
                   v
          NORMALIZATION
          Address: USPS Publication 28 code normalizer
                   (+ USPS APIs v3 when called)
          Name:    abbreviation expansion, punctuation and
                   filler-word removal, similarity scoring
                   |
                   v
          DOCUACTION DATABASE
          Entities - identifiers - relationships - reviews
          findings - evidence - audit
                   |
                   v
          VERIFICATION ENGINE
          Field-by-field comparison, finding codes, confidence
                   |
                   v
          RULES AND DECISION LOGIC
          Versioned classification rules -> B1..B4 -> Tier
          Coverage test -> Indeterminate where required
                   |
                   v
          USER REVIEW
          Confirm or reclassify, with mandatory rationale
                   |
                   v
          FINAL RESULT
          Effective bucket = reviewer's decision where one was made
                   |
                   v
          AUDIT RECORD
          Append-only, with actor, timestamp, and detail
```

## 5.3 Source detail

### NPPES — National Plan and Provider Enumeration System

| | |
|---|---|
| **Source** | CMS / HHS public NPI Registry API |
| **Status** | **PRODUCTION** — live on both environments |
| **Purpose** | Confirm that a National Provider Identifier exists, is active, and belongs to the organization named in the submission |
| **Data received** | NPI number, enumeration type (Type 1 individual / Type 2 organization), legal organization name, status, enumeration date, primary taxonomy and code, practice location address |
| **How it enters** | Real-time API query per entity at review time |
| **Mode** | Real-time. No batch, no local snapshot |
| **Update frequency** | Every query is current as of that moment |
| **Authentication** | None required — public federal API |
| **What it verifies** | NPI existence and validity, NPI active status, organization name match, address cross-reference, entity type consistency |
| **If unavailable** | The record is marked Indeterminate. It is never auto-classified and always routed to a human analyst. The outage is recorded against the source, not against the entity |

### OIG LEIE — List of Excluded Individuals and Entities

| | |
|---|---|
| **Source** | HHS Office of Inspector General exclusion list |
| **Status** | **PRODUCTION** — live on both environments |
| **Purpose** | Determine whether an organization or individual is excluded from participation in federal health care programs |
| **Data received** | Exclusion presence, exclusion type, exclusion date, reinstatement date where applicable, historical exclusion records |
| **How it enters** | Connector query per entity, with a 24-hour cache refresh |
| **Mode** | Live query with caching |
| **Authentication** | None required |
| **What it verifies** | Active exclusion status and historical exclusions that were subsequently resolved |
| **If unavailable** | Marked Indeterminate. This is the highest-consequence source: a clean-looking record cannot be trusted while LEIE is down, because an active exclusion is exactly what would be hidden |

**How exclusion screening is performed.** The entity's name and NPI are submitted to the exclusion connector. An active exclusion with no recorded reinstatement produces finding `LEIE_ACTIVE_EXCLUSION`, which forces bucket **B4 — Non-Compliant** and Tier 3 escalation. A historical exclusion with a confirmed reinstatement produces `LEIE_HISTORICAL_RESOLVED`, a **B2** administrative finding — it is disclosed but does not condemn the entity.

### Medicare enrollment indicator

> ⚠️ **WARNING — THIS IS NOT A PECOS INTEGRATION**
> Medicare enrollment status is derived from CMS public data obtained through the NPPES registry endpoint. This provides enrollment indicators but is not a direct integration with the CMS PECOS system. A direct PECOS data feed requires COR-provisioned access.

| | |
|---|---|
| **Source** | CMS public data via the NPPES registry endpoint |
| **Status** | **PRODUCTION** — reports available |
| **Purpose** | Provide an enrollment indicator: provider name, provider type and taxonomy, address, enumeration date, and status |
| **Data received** | The same record NPPES returns, interpreted for enrollment purposes |
| **Mode** | Real-time |
| **What it verifies** | That the provider is enumerated and its enrollment details are consistent with the submission |
| **What it does not verify** | **Payment suspension.** The payment-suspension flag is reported as "not provided by this source" — never as a clean value. Determining payment suspension requires a COR-provisioned CMS feed the programme does not currently have |
| **If unavailable** | Marked Indeterminate, as with NPPES |

Because this indicator and the NPI check use the same endpoint, they succeed and fail together. Two green indicators here represent **one** underlying source, and coverage counts should be read with that in mind.

### SAM.gov — System for Award Management

| | |
|---|---|
| **Source** | GSA System for Award Management |
| **Status** | **CONFIGURED, NON-FUNCTIONAL** on both environments |
| **Condition** | API key is configured. Entity lookup endpoints are returning 404 due to an upstream routing issue at api.sam.gov. This is not an application defect |
| **Purpose (when operational)** | Federal registration status, UEI verification, debarment and suspension screening, address cross-reference |
| **Current behaviour** | Reported as `unavailable` in connector health, and as `not_checked` with a stated reason in review coverage. It is excluded from the coverage denominator rather than counted as a failure |
| **Effect on reviews** | Findings that depend on SAM — active debarment, lapsed registration — cannot currently be produced. Reviewers must not infer that absence of a SAM finding means the entity is clear with SAM |

### USPS Address APIs v3

| | |
|---|---|
| **Source** | USPS APIs v3, OAuth 2.0 client credentials, `apis.usps.com` |
| **Status** | **PRODUCTION — configured on both environments**, environment set to production, circuit breaker closed. **Zero production calls recorded to date** |
| **Purpose** | Standardize and validate a street address against USPS records |
| **Data received** | Standardized address line, city, state, ZIP5, ZIP+4, delivery-point validation indicators |
| **Mode** | Real-time, on demand |
| **Protections** | Bounded retries with exponential backoff; no retry on 400, 401 or 404; circuit breaker opens after five consecutive failures and stays open for five minutes; address values are never written to application logs |
| **If unavailable** | The system falls back to code-only normalization automatically. An address check can never break a review. The result records that the standardization came from the fallback rather than from USPS |

### Address normalization — USPS Publication 28

| | |
|---|---|
| **Status** | **PRODUCTION** — always available, no key, no network, no quota |
| **Purpose** | Resolve formatting differences so that two renderings of the same address compare as equal |
| **Rules applied** | Street-suffix standardization (Street/St, Avenue/Ave, Boulevard/Blvd and the rest of the Publication 28 set); directional abbreviation (North/N, Southwest/SW); secondary-unit designators (Suite/Ste, Floor/Fl, Apartment/Apt, Unit); punctuation and case normalization; whitespace collapse; ZIP extraction and ZIP+4 handling |
| **When code normalization runs vs the USPS API** | Code normalization runs on every comparison, always. The USPS API is an additional existence-and-standardization check on top of it. When USPS is unavailable, out of budget, or circuit-broken, code normalization alone is used and the result is labelled as a fallback |

### Name matching

| | |
|---|---|
| **Status** | **PRODUCTION** |
| **Purpose** | Decide whether two organization names refer to the same organization despite differences in wording |

> ℹ️ **NOTE — TWO ALGORITHMS ARE IN USE**
> The ARC module uses Jaro-Winkler similarity scoring for entity name comparison. The Registry module uses Levenshtein distance with a different threshold. Both perform fuzzy name matching but use different algorithms and sensitivity settings. This is a known inconsistency scheduled for unification in a future release.

**How Jaro-Winkler works, in plain terms.** It produces a score between 0 and 1 for how similar two strings are. It counts characters that appear in both names near the same position, penalizes characters that appear in a different order, and then adds a bonus when the names share an opening prefix — because organizations that begin with the same words are usually the same organization. A score of 1.0 is identical; 0.0 is nothing in common.

Before scoring, both names are normalized: lowercased, punctuation stripped, and legally meaningless words removed — *Inc, LLC, Corp, Company, Ltd, PLLC, PC, Group, Holdings, the, of, and*. "Mercy Health LLC" and "Mercy Health, Inc." therefore compare as identical rather than as a difference.

**Thresholds used in ARC classification:**

| Similarity | Finding | Bucket effect |
|---|---|---|
| 0.90 and above | No finding — names agree | — |
| 0.70 to 0.89 | `NAME_ABBREVIATION_DIFF` | B2 |
| 0.50 to 0.69 | `NAME_DBA_VS_LEGAL` or `NAME_PUNCTUATION_DIFF` | B2 |
| 0.30 to 0.49 | `NAME_COMPLETELY_DIFFERENT` | B3 |
| Below 0.30 | `NAME_UNRESOLVABLE` | B4 |

**When AI assists with name matching.** Only when the deterministic steps cannot decide, and only when AI is explicitly enabled — which it is not today. See Section 14.

### Sources not currently available

| Source | State | Explanation |
|---|---|---|
| **RCE Directory (The Sequoia Project)** | **NOT CONFIGURED** | TEFCA entity population data is provided by ONC under contract direction. AGT does not query an external directory for it. Reported live as `rce_directory_live: false` |
| **IQVIA OneKey** | **NOT CONFIGURED** | Commercial provider hierarchy data, pending ODC procurement. No API key configured on any environment |
| **State licensure registries** | **NOT IMPLEMENTED** | Reported as `not_checked` with the reason "connector not implemented" — a disclosed roadmap gap, not an outage |
| **IRS** | **NOT APPLICABLE** | No public IRS API exists for verifying a for-profit entity. IRS TEOS covers only tax-exempt organizations, and IRS data is keyed on EIN, which the registry does not hold. This will not be built |

## 5.4 What happens when any source is unavailable

This is the platform's most important safety property. The sequence is fixed:

1. The connector fails, times out, or returns a non-success status.
2. The failure is recorded in the connector log with the source name, reason, and timestamp.
3. The source is marked `unavailable` in that entity's verification record — never `not_found`, and never omitted.
4. Only explicitly approved deterministic fallbacks are used — for example, code-only address normalization in place of the USPS API.
5. The verification is marked **Indeterminate** and flagged for human review. Auto-classification is disabled for that record.
6. The complete audit trail is preserved, including which source failed and why.
7. **A successful verification is never fabricated.**

---

# 6. User Roles and Responsibilities

## 6.1 The role model

DocuAction uses an eight-level hierarchy. Each level inherits every permission of the levels below it.

| Level | Role | Short description |
|---|---|---|
| 1 | **viewer** | Read-only access |
| 2 | **contributor** | Can draw review samples |
| 3 | **manager** | Team oversight |
| 4 | **reviewer** | Front-line entity review — **PII access begins here** |
| 5 | **senior_analyst** | Bucket overrides, B3 escalation queue, calibration |
| 6 | **qalead** | Methodology approval, QA sign-off, all queues |
| 7 | **program_manager** | Deliverable submission, cycle management, full audit log |
| 8 | **admin** | Full access including user management |

> ℹ️ **NOTE — HOW ROLES ARE ASSIGNED**
> Administrators can assign these roles through the application interface: **admin, manager, contributor, viewer**.
> Specialized operational roles (**reviewer, senior_analyst, qalead, program_manager**) are configured by the system administrator through direct system configuration. Contact the system administrator to request assignment to these roles.

New accounts default to **viewer** and require administrator approval before they can be used.

## 6.2 VIEWER

**Purpose.** Read-only visibility for programme staff, oversight, and observers who need to see the state of the work without changing it.

**Can access.** Mission Control, Analytics, Reports (view and download), Findings, Review Cycles, Connectors, Trust Center, Help, methodology and discrepancy-taxonomy references, Registry browsing.

**Can perform.** View dashboards and metrics; open reviews and findings; download generated reports; read review rules and their version history.

**Cannot perform.** Cannot see PII — entity-level personally identifying detail is restricted to reviewer and above. Cannot draw samples, execute reviews, resolve findings, change entity status, author rules, or manage users.

**Daily responsibilities.** Monitor progress and escalation volume; raise questions to the reviewer or QA lead.

**Escalation.** Anything requiring action goes to a reviewer or the programme manager.

## 6.3 CONTRIBUTOR

**Purpose.** Prepare work for reviewers.

**Can access.** Everything a viewer can, plus sample creation.

**Can perform.** Draw a statistical sample against a review cycle, using the configured confidence level, margin of error, and seed.

**Cannot perform.** Cannot review entities, resolve findings, or see PII.

**Daily responsibilities.** Confirm the cycle is ready; draw the sample when directed; verify the sample size and that the seed was recorded.

**Escalation.** If a sample cannot be drawn — usually an empty or unconfigured population — escalate to the programme manager.

## 6.4 MANAGER

**Purpose.** Team and workload oversight.

**Can access.** Everything a contributor can.

**Can perform.** All contributor actions, plus workload monitoring across the team.

**Cannot perform.** Cannot review entities or see entity-level PII — those require reviewer.

**Daily responsibilities.** Track queue depth, SLA bands, and unassigned work; rebalance workload; report blockers.

**Escalation.** Capacity problems to the programme manager; methodology questions to the QA lead.

## 6.5 REVIEWER

**Purpose.** Front-line review of assigned entities and their verification evidence. This is the core operational role and the first role permitted to see PII.

**Can access.** All ARC pages including Entity Reviews, Validation Queue, Findings, Priority Reviews, Data Import, Reports, Search; entity-level detail including PII; Registry entity detail.

**Can perform.** Open and work assigned entity reviews; run a verification for an entity; read the full evidence record for every source; resolve a B3 review by confirming the classification or reclassifying it to B1, B2, B3, or B4 with a mandatory written rationale; add review notes; escalate to Tier 3; upload entity files.

**Cannot perform.** Cannot author or version classification rules; cannot approve methodology; cannot sign off QA; cannot submit contract deliverables; cannot manage users; cannot override a QA determination.

**Daily responsibilities.**

1. Log in and check Mission Control for assigned work and overdue items.
2. Work the Entity Reviews queue in SLA order — overdue first, then at-risk.
3. For each entity: read the submitted data, then read every source result individually.
4. Confirm which sources actually answered before judging the outcome.
5. Investigate exceptions in the Validation Queue.
6. Record a rationale for every decision, including confirmations.
7. Escalate B4 and anything outside your authority.
8. Re-check the queue at end of day for newly arrived or newly overdue work.

**Escalation.** B4 findings and active exclusions to senior_analyst or Tier 3. Bucket disputes to senior_analyst. Methodology questions to qalead. Connector problems to the administrator.

## 6.6 SENIOR_ANALYST

**Purpose.** Adjudicate the difficult cases and keep reviewers consistent with each other.

**Can access.** Everything a reviewer can, plus the B3 escalation queue and calibration views.

**Can perform.** All reviewer actions; override a bucket classification; work the B3 escalation queue; run calibration comparisons across reviewers.

**Cannot perform.** Cannot approve methodology or sign off QA deliverables.

**Daily responsibilities.** Clear the B3 escalation queue; adjudicate bucket disputes; review inter-rater agreement and raise systematic divergence with the QA lead; coach reviewers on recurring errors.

**Escalation.** Methodology change proposals to qalead; contract-affecting findings to the programme manager.

## 6.7 QALEAD

**Purpose.** Assure the quality of the review process itself, not of individual entities.

**Can access.** Everything a senior analyst can, plus QA Operations and every queue.

**Can perform.** Run and interpret all QA gates — evidence completeness, internal consistency, sampling validation, inter-rater agreement, statistical checks, golden-record regression, SLA, and the report gate; approve methodology; provide QA sign-off; export the QA audit trail.

**Cannot perform.** Cannot submit contract deliverables; cannot manage users.

**Daily responsibilities.** Review the QA score and alerts; run the report gate before any deliverable is generated; investigate any failed gate; monitor inter-rater agreement; approve or reject methodology changes.

**Escalation.** A failed report gate blocks the deliverable and goes to the programme manager immediately.

## 6.8 PROGRAM_MANAGER

**Purpose.** Own the contract deliverable and the review cycle.

**Can access.** Everything a QA lead can, plus the full audit log and cycle management.

**Can perform.** Create and manage review cycles; generate weekly, biweekly, quarterly, and final reports; submit deliverables; review the complete audit log; create priority reviews.

**Cannot perform.** Cannot manage user accounts.

**Daily responsibilities.** Confirm cycle progress against schedule; confirm the QA report gate passed before generating a deliverable; review overdue volume; handle COR-directed priority reviews.

**Escalation.** Contractual and schedule issues to the COR; access and infrastructure issues to the administrator.

## 6.9 ADMIN

**Purpose.** Operate and safeguard the platform.

**Can access.** Everything, plus user management, configuration, connector health detail, USPS metrics, and seeding functions.

**Can perform.** Create, approve, disable, and delete user accounts; assign admin, manager, contributor, and viewer roles through the interface; author and version classification rules; configure the system; monitor connector and integration health; access operational metrics.

**Cannot perform.** Should not make review determinations. The administrator role exists to run the platform, not to decide entity outcomes — doing both in one person removes the separation the audit trail depends on.

**Daily responsibilities.** See Section 18.

**Escalation.** Security events to the security contact; upstream source outages to the COR where they affect delivery.

## 6.10 Role summary

| Capability | viewer | contributor | manager | reviewer | senior_analyst | qalead | program_manager | admin |
|---|---|---|---|---|---|---|---|---|
| View dashboards and reports | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Draw samples | No | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| See entity PII | No | No | No | Yes | Yes | Yes | Yes | Yes |
| Review and resolve entities | No | No | No | Yes | Yes | Yes | Yes | Yes |
| Override bucket / B3 queue | No | No | No | No | Yes | Yes | Yes | Yes |
| QA gates and sign-off | No | No | No | No | No | Yes | Yes | Yes |
| Generate and submit deliverables | No | No | No | No | No | No | Yes | Yes |
| Author classification rules | No | No | No | No | No | No | No | Yes |
| Manage users | No | No | No | No | No | No | No | Yes |

# 7. Logging In and Getting Started

## 7.1 Before your first login

You need an account created by an administrator, and it must be **approved**. New accounts default to the **viewer** role and remain inactive until an administrator approves them. If you require an operational role, see Section 6.1 — the specialized roles are configured by the system administrator outside the interface.

## 7.2 Signing in

| Step | Action | Expected result |
|---|---|---|
| 1 | Open the application URL for your environment (Section 17) | The login page appears |
| 2 | Sign in with your organizational account, or with your username and password | You are taken to the platform |
| 3 | Confirm the environment banner and the module you landed on | Mission Control or your default landing page |

Two sign-in methods exist: username and password, and Microsoft Entra ID single sign-on. Both produce the same session. Where your organization has enabled single sign-on, use it.

**If sign-in fails.** Check that you are using the correct environment URL — a development account will not work against production, and the reverse is also true. If the credentials are correct and sign-in still fails, your account may be unapproved or disabled; contact your administrator.

## 7.3 What you should see immediately

Three things are worth checking every time you sign in:

1. **The demonstration-mode banner.** While the platform is in pre-production mode, an amber banner appears at the top of every TEFCA ARC page:
   *"DEMONSTRATION MODE — This module is displaying synthetic data for evaluation purposes. Do not use for operational decisions."*
2. **The System Health Summary** — three collapsed cards reading Compliance, Connectors, and Verifications. Expand Connectors and confirm which sources are live before you begin work.
3. **Your role.** The Trust Center page shows your signed-in account, role, and session type. If your role is not what you expect, stop and contact your administrator before working.

## 7.4 Sessions and timeouts

Sessions use a short-lived bearer token that expires automatically. The interface checks the token's expiry **before** each request rather than waiting for a rejection, so an expired session returns you to the login page rather than showing an error or, worse, an empty page you might read as "no records".

If you are signed out unexpectedly, sign in again. Work that was saved is saved; work in an unsubmitted form is not.

## 7.5 If a page says you lack permission

A page that replaces its content with **"Insufficient permissions"** is telling you the server refused the request for your role. This is deliberate: the platform shows denial as denial rather than as an empty table, because an empty table reads as "there are no records" and would be misleading. Request the appropriate role from your administrator; do not attempt to work around it.

---

# 8. Dashboard Guide

## 8.1 Mission Control

**Where:** `/tefca-arc` — the landing page for the TEFCA ARC module, labelled **Mission Control** in the left navigation.

Mission Control is the single operational view. It reads live values from the API and shows an explicit empty state rather than a zero when data is genuinely unavailable — a blank panel means "not returned", not "none exist".

| Panel | What it shows | What to do with it |
|---|---|---|
| **KPI cards** | Headline counts for the current cycle | Your first read on volume and progress |
| **Review Pipeline** | Stage counts from the live API | Shows where work is accumulating |
| **Evidence Agreement** | Source agreement per review | Low agreement suggests conflicting sources worth investigating |
| **Review Distribution** | Distribution by risk level | Skew toward high risk warrants attention |
| **Pending Reviews** | Entities awaiting disposition | Your work queue in summary |
| **Priority Reviews** | COR-assigned cases with severity and status | Highest priority; work these first |
| **Validation Queue** | Entities not yet validated against the authoritative sources | Records that have not yet been checked |
| **Recent Activity** | Latest reviews and QA events | Confirms work is flowing |
| **Notifications** | Real failing signals only | Empty is good. "No failing QA checks, connector outages, or SLA breaches" means exactly that |
| **Quick Actions** | Navigation shortcuts | Convenience only |
| **Connector Health** | Status read from the API only | Confirm your sources before trusting a result |

## 8.2 The System Health Summary

Above the work area sit three collapsed cards. Each expands to show its detail.

| Card | Counts | What it means |
|---|---|---|
| **Compliance** | Active / Pending | Organizational and framework posture — see the caution below |
| **Connectors** | Live / Error / Unavailable | Runtime state of the external data sources |
| **Verifications** | Active / Partial | Which verification capabilities are operating |

> ⚠️ **WARNING — READ THE COMPLIANCE CARD CAREFULLY**
> The Compliance card mixes several different kinds of claim under one Active/Pending vocabulary: application security controls, security categorization, government authorization status, and organizational certifications held by AGT as a company. These are not equivalent. In particular, an "Active" corporate certification such as ISO 27001 or CMMI Level 3 is an **organizational** credential and is **not** a statement that this application has been certified, assessed, or authorized. Section 19 states each item precisely. Do not cite the Compliance card as evidence of application compliance.

**Connector states you will see.** The platform resolves connector health fail-closed — a source is shown as Live only when the health check affirmatively reports it healthy.

| Badge | Meaning |
|---|---|
| **Live** | The source responded and is healthy |
| **Unavailable** | Not configured or not reachable. Amber, not red — this is "needs configuration or is not answering", not necessarily a fault |
| **Error** | A source that normally answers has failed. Red |
| **Unable to Verify** | Health could not be established at all. Never treated as healthy |
| **Mock** | The connector is declared as not wired to a live source |

## 8.3 Other views

| Page | Path | Purpose |
|---|---|---|
| Data Import | `/tefca-arc/import` | Upload entity files; view import history |
| Review Cycles | `/tefca-arc/cycles` | Cycle creation and progress |
| Entity Reviews | `/tefca-arc/reviews` | The main reviewer queue |
| Validation Queue | `/tefca-arc/validation` | Entities awaiting validation against sources |
| Priority Reviews | `/tefca-arc/priority` | COR-directed cases |
| Findings | `/tefca-arc/findings` | All findings, filterable |
| Reports | `/tefca-arc/reports` | Generate and download deliverables |
| QA Operations | `/tefca-arc/qa` | QA gates, scores, and alerts |
| Connectors | `/tefca-arc/connectors` | Per-source health detail |
| Analytics | `/tefca-arc/analytics` | Trends and distributions |
| Search | `/tefca-arc/search` | Search by NPI, name, or QHIN |
| Trust Center | `/tefca-arc/trust-center` | Security posture, data classification, session info |
| Audit | `/tefca-arc/audit` | Chronological activity view |
| Help | `/tefca-arc/help` | In-application reference |

Registry pages — QHIN Overview, Entities, Verification, Issues — sit in the separate **TEFCA REGISTRY** navigation section. Remember that Registry verification does not query external sources (Section 3.4).

---

# 9. Daily Operating Procedure

> ⚠️ **WARNING — PRE-PRODUCTION DEMONSTRATION MODE**
> The procedure below is the correct operational routine, and it is the routine that will apply unchanged once ONC entity data is received. Today it runs against synthetic demonstration data on both environments. Work performed under this procedure now is operational readiness demonstration; it is not official production verification. Every report carries the stamp "MOCK — demonstration data only. Do not use for operational decisions."

## 9.1 The daily cycle

```
   START OF DAY
        |
        v
   LOG IN
        |
        v
   REVIEW DASHBOARD  -- Mission Control: KPIs, notifications, connector health
        |
        v
   CHECK ASSIGNED WORK  -- Entity Reviews and Priority Reviews
        |
        v
   REVIEW ALERTS AND EXCEPTIONS  -- Notifications panel, Validation Queue
        |
        v
   PROCESS ENTITIES  -- open, read, verify, judge
        |
        v
   REVIEW VERIFICATION EVIDENCE  -- every source, individually
        |
        v
   RESOLVE OR DOCUMENT EXCEPTIONS  -- rationale is mandatory
        |
        v
   SUBMIT, APPROVE, OR ESCALATE
        |
        v
   END-OF-DAY REVIEW  -- nothing left overdue or unrecorded
```

## 9.2 Reviewer — step by step

### Step 1 — Log in

**Where:** the login page for your environment.
**Action:** sign in.
**Expected result:** you land on Mission Control and see the demonstration-mode banner while the platform is in pre-production mode.
**If something is wrong:** an unapproved or disabled account cannot sign in. Contact your administrator.

### Step 2 — Read the dashboard before touching any work

**Where:** Mission Control.
**Action:** read the Notifications panel and expand the **Connectors** card in the System Health Summary.
**Expected result:** Notifications shows "No active alerts". Connectors shows which sources are live.
**If something is wrong:** if a source you depend on is Unavailable or Error, note it before you start. Results produced while a required source is down will be Indeterminate, and that is correct behaviour, not a fault. Do not attempt to close those records as clean.

### Step 3 — Check your assigned work

**Where:** Entity Reviews (`/tefca-arc/reviews`); then Priority Reviews (`/tefca-arc/priority`).
**Action:** sort by SLA band. Work **overdue** first, then **at risk**, then **on track**. Priority Reviews are COR-directed and take precedence over routine queue work.
**Expected result:** a prioritized list of entities awaiting your disposition.
**If something is wrong:** an empty queue when you expect work usually means the sample has not been drawn for this cycle. Ask the programme manager or a contributor.

### Step 4 — Review alerts and exceptions

**Where:** Validation Queue (`/tefca-arc/validation`).
**Action:** identify entities not yet validated against the authoritative sources, and any records showing conflicting evidence.
**Expected result:** you know which records need source verification run before they can be judged.

### Step 5 — Process each entity

Follow Section 10 for the full walkthrough. In summary: open the record, read the submitted data, run or review the verification, read each source result individually, judge the classification, and record your rationale.

### Step 6 — Review the verification evidence properly

**Where:** the entity detail view.
**Action:** for every source, read the state and the reason — not just the summary.
**Expected result:** you can state, in one sentence, which sources answered and what each said.

> ⚠️ **WARNING**
> A summary that looks clean is not evidence of a clean entity if a required source did not answer. Always confirm coverage before accepting a favourable result. The coverage note tells you how many implemented sources were actually checked.

### Step 7 — Resolve or document exceptions

**Where:** the entity detail view, Resolution section.
**Action:** confirm the classification, or reclassify it to B1, B2, B3, or B4. Enter a rationale — this is mandatory and cannot be skipped.
**Expected result:** the review is marked reviewed, the effective bucket updates, and an audit entry is written with your identity, the timestamp, the resolution, and your rationale.
**If something is wrong:** if you cannot form a defensible rationale, do not resolve the record. Escalate it.

### Step 8 — Submit or escalate

**Action:** escalate B4 findings and active exclusions to senior analyst or Tier 3. Escalate anything outside your authority or your confidence.
**Expected result:** the record moves to the escalation queue and leaves your list.

### Step 9 — End-of-day review

**Where:** Mission Control.
**Action:** confirm nothing you touched is left half-finished, and check whether anything became overdue during the day.
**Expected result:** your queue reflects the day's work and every decision carries a rationale.

## 9.3 Senior analyst — daily

1. Clear the B3 escalation queue first; these are the records the rules could not explain.
2. Adjudicate bucket disputes raised by reviewers.
3. Review inter-rater agreement in QA Operations; raise systematic divergence with the QA lead rather than correcting reviewers case by case.
4. Apply bucket overrides only with a recorded rationale.

## 9.4 QA lead — daily

1. Open QA Operations and read the QA score and alerts.
2. Run the gates that apply to today's stage: evidence completeness, internal consistency, sampling validation, inter-rater agreement, SLA.
3. Investigate every failed gate before the deliverable stage is reached.
4. Run the **report gate** before any deliverable is generated. A failed report gate blocks the deliverable — that is the gate working correctly.

## 9.5 Programme manager — daily

1. Confirm cycle progress against schedule on Mission Control.
2. Review overdue volume and its trend.
3. Confirm the QA report gate has passed before generating any deliverable.
4. Handle COR-directed priority reviews the day they arrive.

## 9.6 Administrator — daily

See Section 18.

---

# 10. Entity Review — Step-by-Step

## 10.1 Opening a review

**Where:** Entity Reviews (`/tefca-arc/reviews`) → click the entity row.

The detail view presents four things: the entity as submitted, the verification results per source, the classification the engine produced, and the resolution controls.

## 10.2 Reading the submitted entity

Check these fields first, because they determine what can be verified at all:

| Field | Why it matters |
|---|---|
| **Legal name** | The basis of name matching against NPPES |
| **NPI** | The primary key for every source lookup. Missing NPI means most checks cannot run |
| **Address** | Cross-referenced against NPPES; normalized before comparison |
| **Entity type** | Compared against the NPPES taxonomy classification |
| **TEFCA identifiers** | TEFCAID and HCID; used for registry integrity |
| **Entity level** | QHIN, Participant, Subparticipant — determines hierarchy expectations |

## 10.3 Running verification

**Action:** use the verify control on the entity.
**What happens:** the platform queries NPPES, the Medicare enrollment indicator, and OIG LEIE concurrently, with per-source timeouts and bounded retries. SAM.gov is attempted but is currently non-functional upstream.
**Expected result:** a five-state result for every source, plus a coverage note.

## 10.4 Reading the evidence — the part that matters most

For each source you will see one of five states:

| State | Meaning | Counts against the entity? |
|---|---|---|
| **verified** | The source has the record and it matches | No — this is the good outcome |
| **not_found** | The source answered, and does not have this record | **Yes** — this is a statement about the entity |
| **unavailable** | The source did not answer | **No** — this is a statement about the source |
| **not_checked** | No connector, or the connector is not operational, with a stated reason | No — a disclosed gap |
| **failed** | The call errored | No — investigate the platform |

> ⚠️ **WARNING — THE DISTINCTION THAT MATTERS MOST**
> `not_found` and `unavailable` look similar and mean opposite things. `not_found` means the federal source answered and does not have this organization — that is evidence against the entity. `unavailable` means the federal source did not answer — that is a gap in your evidence and says nothing about the entity. Never treat `unavailable` as `not_found`, and never treat either as verified.

Then read the **coverage note**, which states in plain language how many implemented sources were checked, which were unavailable, which were not checked and why, and which are not implemented at all. Only implemented connectors count toward coverage; roadmap gaps are reported separately so a review is never penalized for a connector that does not exist.

## 10.5 Understanding the classification

The engine compares fields, produces finding codes, and assigns a bucket. **The worst finding wins** — a single B4 finding makes the entity B4 regardless of how many checks passed.

Then the coverage test runs. If any required source was unavailable, the record is marked **Indeterminate**, auto-classification is disabled, and the label becomes "Indeterminate — Source Unavailable" with the reason attached.

Finally the record is routed to a tier:

| Tier | When | Meaning |
|---|---|---|
| **Tier 1** | B1, confidence 0.95 or above, full coverage | Auto-complete — no human needed |
| **Tier 2** | B2, B3, Indeterminate, or B1 below the confidence threshold | Analyst review |
| **Tier 3** | B4 | Escalation |

## 10.6 Making your determination

**Where:** the Resolution section of the entity detail view.

Two outcomes are available:

1. **Confirm** — you agree with the engine's classification. Confirming is itself a finding, not the absence of one, and it requires a rationale.
2. **Reclassify** — you disagree. Select B1, B2, B3, or B4 and state why.

**The rationale is mandatory in both cases.** Write it for a reader a year from now who has none of today's context. State which sources you relied on, what they said, and why that leads to your conclusion.

**What is recorded:** your identity, your IP address, the timestamp, the resolution type, the reclassified bucket if any, and your rationale. The sampled entity's status becomes `reviewed` and its effective bucket updates to your decision.

## 10.7 What good rationale looks like

**Adequate:**
> "NPPES confirms NPI 1234567893 active, Type 2, legal name 'Riverbend Regional Medical Center'. Submitted name 'Riverbend Regional Med Ctr' scores 0.94 after abbreviation normalization — an abbreviation difference, not a different organization. LEIE returned no exclusion. Address matches after Publication 28 normalization (suite designator only). SAM unavailable, so no federal registration evidence — noted, not counted against the entity. Confirming B2 on the abbreviation finding."

**Not adequate:**
> "Looks fine." — states no evidence, names no source, and cannot be reviewed by QA.

---

# 11. Verification Services and Data Sources

## 11.1 The verification services

| Service | Status | What it does |
|---|---|---|
| **NPI Verification** | **Active** | Confirms the NPI exists in NPPES, is active rather than deactivated, is the correct type (Type 1 individual / Type 2 organization), and validates the check digit locally before querying |
| **Name Matching** | **Active** | Normalizes both names and scores their similarity; classifies any difference into an abbreviation, punctuation, DBA, or genuine-difference finding |
| **Address Cross-Reference** | **Active** | Normalizes both addresses to USPS Publication 28 form and compares state, ZIP, and street core; optionally standardizes against the USPS API |
| **Exclusion Screening** | **Active** | Checks OIG LEIE for active exclusions and for historical exclusions with confirmed reinstatement |
| **Medicare Enrollment** | **Partial** | Enrollment indicators are derived from CMS public data via the NPPES endpoint. Payment suspension is **not** available and is reported as not provided, never as clean |
| **Entity Import** | **Active** | CSV, Excel, JSON, and FHIR R4 bundle upload with schema validation and duplicate detection |

## 11.2 Why Medicare Enrollment shows Partial

It is Partial for one specific, documented reason: **the payment-suspension data element is not obtainable from the source in use.**

Medicare enrollment status is derived from CMS public data obtained through the NPPES registry endpoint. This provides enrollment indicators but is not a direct integration with the CMS PECOS system. A direct PECOS data feed requires COR-provisioned access.

The platform handles this honestly rather than by omission: the payment-suspension field is returned as "not provided by this source" with an explanatory note. It is never returned as a clean or passing value. Consequently the finding `PECOS_PAYMENT_SUSPENSION` exists in the taxonomy but cannot currently be produced, and reviewers must not infer from its absence that an entity has no payment suspension.

## 11.3 NPI validation before any lookup

Every NPI is checked locally before a source is queried, using the CMS check-digit algorithm (a Luhn calculation over the prefix `80840` plus the first nine digits). An NPI that fails this check is a malformed identifier, not a missing organization, and produces finding `NPI_INVALID` in the Registry or `NPI_NOT_FOUND` handling in ARC. Validating locally first avoids sending obviously bad identifiers to federal systems.

## 11.4 Registry structural checks

The Registry area runs a different and complementary set of checks. These are internal only.

| Check | Severity | What it catches |
|---|---|---|
| Missing mandatory TEFCAID | Critical | Entity has no TEFCA identifier |
| Missing mandatory HCID | Critical | Entity has no health care identifier |
| Circular relationship | Critical | The hierarchy contains a loop |
| Retired TEFCAID on active entity | High | Identifier retired but entity still active |
| Invalid NPI | High | Fails the check digit |
| Multiple active NPIs | High | One entity holding several active NPIs |
| Duplicate NPI / HCID / TEFCAID | High | One identifier shared across entities |
| Orphan entity | High | No parent relationship, or all parent relationships inactive |
| Multiple active parents | High | Entity parented by more than one entity |
| Incorrect parent level | High | A Participant parented by something other than a QHIN, and so on |
| Inactive parent with active children | High | A deactivated parent still holding active children |
| Missing NPI on treatment entity | Medium | Treatment-purpose provider with no NPI |
| Expired CCN | Medium | CMS Certification Number past its end date |
| QHIN with zero participants | Medium | A QHIN holding no active participants |
| Entity has zero relationships | Medium | Entirely unconnected record |

Registry verification sets the entity's verification status to **exception** if any critical or high finding exists, **in_review** if only lower-severity findings exist, and **verified** if none do.

> ⚠️ **WARNING**
> A Registry status of "verified" means the record is structurally sound. It does **not** mean the organization has been verified against federal sources. Only ARC produces that determination.

## 11.5 Technical / Administrator Notes

- Source queries run concurrently with a per-source timeout and bounded retries. Retries apply only to conditions that might succeed on repeat; a definitive rejection is not retried.
- Every connector call is logged individually with its own short-lived database session, so concurrent calls never share a transaction.
- Connectors never raise into the verification pipeline. Every failure path returns a structured result carrying its reason.
- Verification writes are idempotent — job, check, finding, and audit rows use deterministic identifiers so a re-run updates rather than duplicates.
- Source health is probed actively rather than assumed. No connector has a hardcoded healthy status.

---

# 12. Understanding Results and Statuses

This section lists every status a user will encounter, what causes it, what to do, and who can change it.

## 12.1 Classification buckets

| Bucket | Label | Meaning | What causes it | What you should do | Who can change it |
|---|---|---|---|---|---|
| **B1** | No Discrepancy | All checks passed within tolerance | No findings raised | If Tier 1, nothing. If Tier 2, confirm the evidence supports it | Reviewer and above |
| **B2** | Minor or Administrative | Real but explainable difference | Abbreviation, punctuation, DBA-vs-legal name, address formatting, or a historical exclusion since resolved | Confirm the explanation is genuine and record it | Reviewer and above |
| **B3** | Inexplicable | The rules could not explain the evidence | Completely different name, state conflict between sources, entity type mismatch, missing NPI, or lapsed SAM registration | **Must** be resolved by a human — confirm or reclassify, with rationale | Reviewer; escalate to senior analyst |
| **B4** | Non-Compliant | A disqualifying condition is present | NPI not found, NPI inactive or deactivated, active LEIE exclusion, active SAM debarment, payment suspension, or an unresolvable name | Escalate to Tier 3. Do not close at your level | Senior analyst and above |

**Worst finding wins.** A single B4 finding makes the record B4 no matter how many other checks passed.

## 12.2 Indeterminate

| | |
|---|---|
| **What it means** | One or more required sources did not answer, so the evidence is incomplete and the result cannot be trusted in either direction |
| **Label shown** | "Indeterminate — Source Unavailable", with the specific sources named |
| **What causes it** | Any required source in an `unavailable` state at the time of the run |
| **What you should do** | Do not close it as clean. Either re-run once the source recovers, or review it as a human with the gap explicitly documented |
| **Who can resolve it** | Reviewer and above, with a rationale that acknowledges the missing evidence |

> ⚠️ **WARNING**
> Indeterminate is the platform protecting you. A clean-looking record whose exclusion source was down is precisely the failure mode this status exists to prevent. It can never be auto-classified.

## 12.3 Source states

| State | Meaning | What caused it | What you should do |
|---|---|---|---|
| **verified** | The source has this record and it matches | Successful lookup | Nothing |
| **not_found** | The source answered and does not hold this record | Successful lookup, negative result | Treat as evidence about the entity |
| **unavailable** | The source did not answer | Outage, timeout, or non-success response | Treat as a gap. Re-run later |
| **not_checked** | No connector, or the connector is not operational | A stated reason accompanies it | Read the reason. This needs a decision, not a retry |
| **failed** | The call errored | Platform or network error | Report to the administrator |
| **clear** | Exclusion screening found no exclusion | LEIE responded negatively | Nothing |
| **excluded** | An exclusion was found | LEIE responded positively | Escalate immediately |

## 12.4 Connector health badges

| Badge | Meaning | Colour |
|---|---|---|
| **Live** | Health check affirmatively reported healthy | Green |
| **Partial** | Operating with a stated limitation | Amber |
| **Unavailable** | Not configured or not reachable — not a fault in itself | Amber |
| **Error** | A source that normally answers has failed | Red |
| **Unable to Verify** | Health could not be established | Grey |
| **Mock** | Declared as not wired to a live source | Amber |

## 12.5 Review record statuses

| Status | Meaning | Who sets it |
|---|---|---|
| **pending** | Awaiting review | System |
| **reviewed** | A human has confirmed or reclassified it | Reviewer and above |
| **confirmed** | The reviewer agreed with the engine's classification | Reviewer |
| **reclassified** | The reviewer changed the bucket | Reviewer |

The **effective bucket** is the reviewer's reclassification where one was made, and the engine's classification otherwise. Reports use the effective bucket.

## 12.6 Entity lifecycle states

| State | Meaning | Can move to |
|---|---|---|
| **draft** | Record created, not yet submitted for verification | pending_verification |
| **pending_verification** | Submitted, verification in progress or awaiting outcome | active, draft |
| **active** | Verified and operating | suspended, inactive |
| **suspended** | Temporarily halted | active, inactive |
| **inactive** | Deregistered. **Terminal** | Nothing |

Transitions outside this model are refused with an explanation, and the refusal is written to the audit trail. An attempt to move an entity straight from draft to active is exactly the event an auditor wants to see, so it is recorded rather than silently rejected.

A deregistered entity cannot be reactivated; it must be re-registered. This is deliberate.

## 12.7 Entity verification status (Registry)

| Status | Meaning |
|---|---|
| **verified** | No structural findings |
| **in_review** | Findings exist, none critical or high |
| **exception** | At least one critical or high finding |

## 12.8 Finding severities

| Severity | Meaning | Typical response |
|---|---|---|
| **critical** | Structural integrity failure | Immediate correction required |
| **high** | Serious defect | Correct before the entity is treated as sound |
| **medium** | Material but not disqualifying | Document and schedule |
| **low** | Minor | Record |
| **info** | Informational | No action |

Findings carry a status of **open** until resolved, with the resolving user, timestamp, and resolution notes recorded.

## 12.9 SLA bands

Review due dates are set from the date the sample was drawn, with the window depending on cadence:

| Cadence | Window |
|---|---|
| Weekly | 7 days |
| Quarterly | 90 days |
| Priority (COR-directed) | 3 days |

| Band | Condition | Action |
|---|---|---|
| **on_track** | More than 2 days remaining | Normal |
| **at_risk** | 2 days or fewer remaining | Prioritize |
| **overdue** | Past the due moment | Work first; report if systemic |

A review due later today has zero days remaining and is **at_risk**, not overdue. It becomes overdue only once the due moment has passed.

> ℹ️ **NOTE**
> These windows are a starting operational policy, not a contractual service level ratified by the government. If the contract specifies different windows, they are configurable in one place.

---

# 13. Exceptions and Human Review

## 13.1 Where automation stops

The platform is explicit about the boundary between automated processing and human judgement.

| Stage | Automated | Human |
|---|---|---|
| Source queries | Yes | No |
| Field comparison and scoring | Yes | No |
| Finding-code assignment | Yes | No |
| Bucket assignment | Yes | Reviewer may override |
| Coverage test and Indeterminate marking | Yes | No |
| Tier routing | Yes | No |
| **Tier 1 completion (clean B1 only)** | **Yes** | No |
| **Every other disposition** | No | **Yes** |
| B3 resolution | No | **Mandatory** |
| B4 escalation | Routed automatically | **Decided by a human** |
| Bucket override | No | Senior analyst |
| Methodology approval | No | QA lead |
| Deliverable submission | No | Programme manager |

**Only one path completes without a human:** bucket B1, confidence 0.95 or above, and full source coverage. Anything less reaches a person.

## 13.2 Conditions that force human review

| Condition | Why |
|---|---|
| Any required source unavailable | Evidence is incomplete; the result cannot be trusted |
| Bucket B3 | The rules could not explain the evidence — that is the definition of B3 |
| Bucket B4 | A disqualifying condition needs human accountability |
| Bucket B2 | A real difference exists and someone must confirm the explanation |
| Confidence below 0.95 on B1 | The clean result is not certain enough to stand alone |
| Source conflict | Two sources that both answered contradict each other |
| AI consulted at any confidence | The recommendation is context, never a decision |

## 13.3 Source conflict

The platform recognizes two specific contradictions, and only between sources that **both actually answered** — if one is unavailable there is a gap, not a disagreement, and calling that a conflict would manufacture a B3 out of an outage:

1. NPPES holds the provider but the Medicare enrollment indicator does not — an enrolment inconsistency.
2. The enrollment indicator shows the provider enrolled while OIG lists them as excluded — the more serious pairing, since an excluded provider should not be actively enrolled.

Either condition routes the record to human review.

## 13.4 How to work an exception

1. **Establish coverage first.** Read the coverage note. Which sources answered?
2. **Read each source result individually**, including its reason text.
3. **Identify the specific finding codes** raised, and read their descriptions.
4. **Decide whether the finding is explicable.** An abbreviation difference is explicable. A different organization at a different address is not.
5. **Confirm or reclassify**, with a rationale that names the sources and the evidence.
6. **Escalate** if the determination exceeds your authority or your confidence.

## 13.5 What must never happen

> ⛔ **PROHIBITED**
> Do not close an Indeterminate record as clean.
> Do not treat `unavailable` as `not_found`, or either as verified.
> Do not modify submitted entity data to make a check pass.
> Do not resolve a record without a rationale you would defend in an audit.
> Do not use an AI recommendation as the evidence for a determination.

# 14. AI-Assisted Analysis (What It Does and Does Not Do)

> ℹ️ **NOTE — AI IS CURRENTLY DISABLED**
> AI entity resolution is **disabled on both the development and production environments**. The feature flag that controls it is not set on either environment, and the system defaults to disabled. The platform is fully functional without it. This section documents the capability so that its controls are understood before it is ever switched on.

## 14.1 The three modes

| Mode | Behaviour |
|---|---|
| **disabled** | AI is never called. This is the default and the current setting on both environments |
| **advisory** | AI may be consulted, but its answer is context for the reviewer only and never sets the match outcome |
| **production** | AI may set a provisional match outcome, but the record still requires manual review |

The setting fails closed. An unrecognized or misspelled value is treated as **disabled** rather than being interpreted generously — a typo in configuration must not silently switch AI on in a pipeline that produces compliance evidence.

## 14.2 When AI would be called

AI is the fourth and last step of entity resolution, and it is reached only when the first three cannot decide:

```
  1. EXACT IDENTIFIER MATCH  (NPI or TEFCAID)
     Decisive both ways. Same identifier = same entity.
     Different identifiers in the same space = different entities.
                |  no identifier available on both records
                v
  2. USPS ADDRESS NORMALIZATION
     Deterministic, free, always available.
                |
                v
  3. JARO-WINKLER NAME SIMILARITY
     Deterministic, free.
     Both signals strong  -> MATCH, no AI
     Both signals weak    -> NO MATCH, no AI
                |  signals disagree
                v
  4. AI ADJUDICATION
     Only if AI is enabled. Only on this residue.
```

Steps 1 to 3 settle the overwhelming majority of cases. Step 4 exists for the narrow residue where two records plausibly describe one organization and no deterministic signal decides it.

## 14.3 What AI does

- Receives two organization records and returns a single structured judgement: whether they refer to the same real-world entity, a confidence value between 0 and 1, and one sentence of reasoning.
- It is instructed to be conservative and to return low confidence rather than guess when evidence is genuinely ambiguous.
- Its output is explicitly framed to the model itself as *a recommendation for a human reviewer, not a decision*.

## 14.4 What AI does not do

| AI does not | Enforced how |
|---|---|
| Make the determination | Every AI-touched result carries mandatory manual review, at any confidence |
| Replace an authoritative source lookup | AI is never consulted in place of NPPES, LEIE, or any federal source. It adjudicates between two records; it does not fetch facts |
| Receive PHI or patient data | An allowlist restricts the payload to six public fields |
| Set the outcome in advisory mode | The match value is not populated at all in advisory mode |
| Break the pipeline when it fails | Failures are caught, recorded, and the deterministic result is returned |

## 14.5 What data is sent

Only these six fields, and only when present:

**name · address · npi · entity_type · state · tefcaid**

This is an allowlist, not a blocklist. Any field not on this list is dropped before the payload is built, so a future column added to the entity model — including one carrying protected information — cannot leak by default.

**Never sent:** protected health information, patient data, Social Security numbers, internal identifiers, review history, reviewer notes, or findings.

## 14.6 Confidence thresholds

| Confidence | Threshold applied | Effect |
|---|---|---|
| 0.95 and above | `show_recommendation` | The recommendation is surfaced to the reviewer as context |
| 0.70 to 0.94 | `mandatory_manual_review` | Shown as context only; manual review is compulsory |
| Below 0.70 | `ignored_below_threshold` | **The recommendation is discarded entirely** — not downgraded, not shown |

A low-confidence guess must not reach a reviewer as evidence, so it is dropped rather than displayed with a caveat.

## 14.7 What is recorded for every AI call

Every call is audit-logged with: model identifier, prompt version, the exact input payload, the output, the confidence, the threshold applied, the timestamp, the latency, the software version, and any error. This is the record that lets an auditor reconstruct precisely what the model was asked and what it said.

## 14.8 Fallback when AI is unavailable

```
   AI unavailable, disabled, or failing
                |
                v
   Deterministic matching continues
   (identifier, USPS address normalization, Jaro-Winkler)
                |
                v
   No AI-generated content produced
                |
                v
   Entity flagged for manual review
                |
                v
   Human reviewer makes the determination
                |
                v
   System continues operating without AI
```

The failure is caught, logged with its exception type, and the deterministic result is returned. An AI outage is visible in the audit record and invisible in the outcome — the review simply proceeds without it.

## 14.9 The rule for users

> ⛔ **PROHIBITED**
> Do not treat AI-generated content as authoritative evidence. An AI recommendation is never the basis for a determination. Your rationale must cite what a federal source returned, not what a model suggested.

## 14.10 A note on scope

The AI capability described here is the TEFCA entity-resolution adjudicator. A separate platform-level AI governance module exists in the codebase but is **not mounted on production** — its endpoints return 404 — and it is therefore not part of the operating system described by this guide.

---

# 15. QA and Approval

## 15.1 What QA is for

QA in DocuAction examines **the review process**, not individual entities. Its purpose is to catch systematic error — a reviewer consistently misclassifying a pattern, a sample that was not drawn correctly, evidence that was not retained, a deliverable being assembled from incomplete work — before any of it reaches the government.

QA Operations is at `/tefca-arc/qa` and is available to the **qalead** role and above.

## 15.2 The QA gates

| Gate | What it checks | What a failure means |
|---|---|---|
| **QA score** | Overall composite quality measure | Aggregate degradation; investigate the contributing gates |
| **Evidence summary / validate evidence** | That each review retained the source evidence supporting its determination | A determination exists without the evidence behind it. Blocks reporting |
| **Validate review** | That an individual review is internally complete and correctly formed | The review cannot be relied upon |
| **Internal consistency** | That classifications agree with the findings that produced them | A bucket does not match its own evidence |
| **Sampling validation** | That the sample was drawn correctly and is statistically valid | The population conclusions cannot be supported |
| **Inter-rater agreement** | Whether reviewers classify comparable records the same way | Reviewers are diverging; calibration needed |
| **Statistical** | Distribution and outlier checks across the cycle | Results are skewed in a way that warrants explanation |
| **Golden records** | Known-answer records that must classify correctly | The engine's behaviour has changed |
| **Regression** | That previously correct behaviour has not degraded | A change broke something that used to work |
| **SLA** | Due dates, overdue counts, and status bands | Delivery risk |
| **Connector health** | Source availability from the QA perspective | Coverage risk to the cycle |
| **Sweep** | Broad cross-check across the cycle | Systemic issue |
| **Alerts** | Active QA alerts | Something needs attention now |
| **Report gate** | **The go / no-go check before a deliverable is generated** | **The deliverable is blocked** |
| **QA audit / audit export** | The QA activity trail, exportable | Evidence for the government |

## 15.3 The report gate

The report gate is the most consequential control in the QA set. It is run before a deliverable is generated, and a failure blocks the deliverable.

> ⚠️ **WARNING**
> A blocked report gate is the control working correctly. It is not an obstacle to be worked around. Do not generate a deliverable by another route when the gate has failed. Resolve the underlying condition, re-run the gate, and then generate.

## 15.4 QA checklist before accepting a completed entity review

Work through this for each review being accepted:

- [ ] **Entity identity validated** — the submitted identifiers are well-formed and the NPI passed the check-digit test
- [ ] **Source coverage confirmed** — you know which sources answered and which did not; the coverage note is present
- [ ] **NPI verified** — NPPES was actually queried and its result recorded, not inferred
- [ ] **Name match reviewed** — the similarity finding matches what the names actually show
- [ ] **Address evidence reviewed** — normalization was applied and the comparison result is recorded
- [ ] **Exclusion screening completed** — LEIE answered; if it did not, the record is Indeterminate and not closed as clean
- [ ] **Medicare enrollment reviewed** — noting that payment suspension is not available from the current source and its absence is not evidence of absence
- [ ] **SAM status understood** — SAM is currently non-functional upstream; no SAM-dependent conclusion has been drawn
- [ ] **Indeterminate handled correctly** — no Indeterminate record has been closed as clean
- [ ] **Exceptions resolved or documented** — every finding has a disposition
- [ ] **Source evidence retained** — the raw source results are attached to the record
- [ ] **Required human review completed** — nothing in Tier 2 or 3 was auto-closed
- [ ] **Final disposition supported by evidence** — the rationale names the sources and what they said
- [ ] **Rationale is defensible in an audit** — a reader with no context could follow it
- [ ] **Audit trail complete** — actor, timestamp, resolution, and rationale are all present
- [ ] **Bucket overrides justified** — any override carries a senior analyst's recorded reason

## 15.5 Approval authority

| Decision | Authority |
|---|---|
| Entity disposition (B1–B3) | reviewer |
| Bucket override | senior_analyst |
| B4 / Tier 3 escalation outcome | senior_analyst and above |
| Methodology approval | qalead |
| QA sign-off | qalead |
| Deliverable generation and submission | program_manager |
| Classification rule authoring and versioning | admin |

Classification rules are versioned, and a review records the rule code and rule version that classified it. A record classified under an earlier rule version remains valid under that version — changing the rules does not retroactively reclassify completed work.

---

# 16. Reports and Outputs

## 16.1 Report types

| Report | Cadence | Produced by |
|---|---|---|
| **Weekly** | Weekly during a cycle | program_manager |
| **Biweekly** | Every two weeks | program_manager |
| **Quarterly** | Quarterly | program_manager |
| **Final** | At cycle close | program_manager |
| **Priority / quarterly priority** | As directed by the COR | program_manager |
| **QA report** | On demand | qalead |

## 16.2 Formats

| Format | Use |
|---|---|
| **PDF** | Formal delivery and archival |
| **DOCX** | Delivery where the recipient will annotate |
| **CSV** | Data analysis |
| **Excel** | Review workbooks and round-trip QA review |
| **HTML** | On-screen viewing |

Reports are generated to a stored record and then downloaded, so the artefact delivered is the artefact retained.

## 16.3 What a report contains

- The cycle and its parameters, including the sampling confidence level, margin of error, and seed
- Population and sample sizes
- Results by bucket, using the **effective** bucket — the reviewer's decision where one was made
- Findings and their severities
- Connector health during the cycle
- Coverage statements — which sources were checked, which were unavailable, which are not implemented
- The data-provenance stamp

## 16.4 The provenance stamp

> ⚠️ **WARNING — EVERY CURRENT REPORT CARRIES THIS STAMP**
> While the platform is in pre-production demonstration mode, every report, dashboard payload, and status response is stamped:
> **"MOCK — demonstration data only"** with the warning **"This report uses synthetic demonstration data. Do not use for operational decisions."**
> This stamp is applied automatically from a single source of truth and cannot be removed by a user, an option, or a report setting. It disappears on its own when ONC entity population data is loaded and the entity-data configuration key is set.

Do not circulate a stamped report as an official verification result. Do not remove or crop the stamp from an exported document.

## 16.5 Generating a report

| Step | Where | Action |
|---|---|---|
| 1 | QA Operations | Run the **report gate** and confirm it passes |
| 2 | Reports (`/tefca-arc/reports`) | Select the report type and cycle |
| 3 | Reports | Generate |
| 4 | Reports | Download in the required format |
| 5 | — | Confirm the provenance stamp is present and correct before circulating |

---

# 17. Development vs Production

> ⚠️ **WARNING — PRE-PRODUCTION DEMONSTRATION MODE**
> DocuAction TEFCA is currently operating in pre-production demonstration mode. Both development and production environments serve synthetic demonstration data. No ONC entity population data has been received. All verification results shown are operational readiness demonstrations and must NOT be treated as official production verification determinations.
> The system stamps every report with: "MOCK — demonstration data only. Do not use for operational decisions."
> When ONC provides the initial entity population data and the entity-data configuration key is configured, the system transitions to production verification mode and this warning is removed automatically.

## 17.1 How the environments are separated

Isolation between development and production is architectural, not procedural:

| Separation | Implementation |
|---|---|
| **Compute** | Separate Azure App Service (Linux) applications. No shared compute |
| **Database** | Separate Azure Database for PostgreSQL Flexible Servers. Production data never resides on a development server |
| **Identity** | Distinct Microsoft Entra ID app registrations and redirect URIs per environment |
| **Secrets** | Every secret is unique per environment. A secret is never copied between them |
| **Network trust** | Separate allowed-hosts and allowed-origins lists. Each application trusts only its own hostnames and its own frontend origin |
| **Monitoring** | Production carries Application Insights instrumentation; development does not |

## 17.2 Development

**Purpose.** Testing, training, feature validation, and demonstration rehearsal.

**Who should use it.** Developers, testers, and staff being trained. New users should learn here.

**What is there.** Synthetic demonstration data, the same connector configuration as production, and no production database access of any kind.

**What must never be considered official.** Everything. No result produced in development is a verification determination, regardless of how it is labelled or how correct it looks.

## 17.3 Production

**Purpose.** Official review activity under contract 7571MN26F80064 — once entity data is received.

**Who can access it.** Approved accounts only, with roles assigned according to duty.

**What is there.** The production database, production identity, production monitoring, and full audit retention.

**Restrictions.** Production activity is monitored and audited. Every action is attributed. Access is least-privilege and time-limited by session expiry.

## 17.4 The honest current position

> ⚠️ **WARNING — WHAT THIS MEANS IN PRACTICE TODAY**
> For the TEFCA modules specifically, development and production are configured identically. The same connectors are live, the same keys are absent, AI is disabled in both, and both serve synthetic demonstration entity data.
> **There is currently no configuration difference that makes production authoritative for TEFCA work.** Production is production in every architectural respect — isolated compute, isolated data, monitored, audited — but it is not yet performing production verification, because the entity population it would verify has not been received.
> This will change in a single, well-defined step: when ONC provides the entity population data and the entity-data key is configured, the demonstration stamp is removed automatically and production output becomes authoritative. Nothing else needs to change.

## 17.5 The rule

> ⛔ **PROHIBITED**
> NEVER treat DEV/test/mock verification results as official production verification.
> This applies to development output at all times, and to production output for as long as the demonstration-mode stamp is present.

## 17.6 Technical / Administrator Notes — environment configuration

Configuration differences between the two environments, by setting name only (no values are recorded in this document):

**Present on production, absent on development:** Application Insights instrumentation key and connection string; the Bulletin news-provider key; container start-time limit; DNS server; HTTP logging retention.

**Present on development, absent on production:** email template version.

**Identical across both:** database URL, secret key, allowed hosts, allowed origins, application URL, Entra ID client and tenant settings, email sender configuration, SAM.gov key, USPS client credentials, Anthropic key, scheduler enablement, Bulletin authentication and cost-tracking flags, Python path, and build settings.

**Absent from both, and therefore inactive everywhere:** the TEFCA entity-data key, the AI entity-resolution flag, the IQVIA OneKey key, and the legacy USPS user identifier.

---

# 18. Administrator Operations

## 18.1 What administrators monitor

| Area | Where | Healthy looks like |
|---|---|---|
| **Application health** | `/health` | `status: healthy`, expected version, modules active |
| **Connector health** | Connectors page; `/api/tefca/status` | NPPES and LEIE live; the enrollment indicator live; SAM currently unavailable and expected to be |
| **Data provenance** | `/api/tefca/status` | Shows the current data-source label — confirm whether the demonstration stamp is still in force |
| **USPS integration** | `/api/v1/usps/metrics` (admin only) | `configured: true`, circuit breaker `closed`, error rate low |
| **Bulletin news-provider budget** | `/api/v1/bulletin/perigon/health` (admin only) | Budget remaining, provider enabled |
| **Scheduler** | `/health` | Scheduler running with the expected jobs and next-run times |
| **Review queues** | Mission Control | Queue depth stable, overdue count low |
| **QA gates** | QA Operations | All gates passing; report gate green before deliverables |
| **Failed verifications** | Findings; connector logs | Failures attributable to a named source, not to the platform |
| **User access** | Admin → Users | No unapproved or stale accounts |
| **Audit log** | Audit page; QA audit export | Entries present and attributable |
| **Security events** | Azure Defender for Cloud | No open high-severity alerts |

> ℹ️ **NOTE**
> `/api/v1/usps/metrics` and `/api/v1/bulletin/perigon/health` require an administrator session. A 401 from an unauthenticated call is the expected response, not a fault.
> The USPS metrics endpoint is deliberately read-only. There is no control to reset the circuit breaker or zero the counters — a breaker an operator can force closed is a breaker that gets forced closed during the incident it exists to contain. It reopens on its own cooldown or on a successful probe.

## 18.2 Daily checklist

- [ ] `/health` returns healthy, with the expected version
- [ ] Connector health reviewed; any change from the known baseline investigated
- [ ] Data-provenance label checked — is the platform still in demonstration mode?
- [ ] USPS metrics reviewed: configured, circuit breaker closed, error rate acceptable
- [ ] Scheduler running with the expected next-run times
- [ ] Mission Control Notifications panel empty, or every alert triaged
- [ ] Overdue review count reviewed and reported if rising
- [ ] No unapproved user accounts awaiting action
- [ ] No new high-severity security alerts
- [ ] Failed integrations from the previous 24 hours reviewed and attributed

## 18.3 Weekly checklist

- [ ] User access reviewed — every account still required, every role still appropriate
- [ ] Accounts for departed staff disabled
- [ ] Role assignments reconciled against duty, including the specialized roles configured outside the interface
- [ ] Audit log reviewed for unexpected patterns — bulk changes, unusual hours, repeated permission denials
- [ ] Connector error trends reviewed across the week, not just point-in-time
- [ ] Import history reviewed — every batch accounted for
- [ ] QA gate history reviewed for repeated failures
- [ ] SLA bands reviewed; systemic overdue investigated
- [ ] Backup and restore state confirmed
- [ ] Dependency and security scan results reviewed

## 18.4 Monthly checklist

- [ ] Full access recertification — every user, every role, documented
- [ ] Audit retention and volume reviewed
- [ ] Connector availability reported as a monthly figure
- [ ] Upstream source issues reviewed with the COR where they affect delivery — currently SAM.gov
- [ ] Outstanding configuration gaps reviewed: entity-data key, RCE, IQVIA
- [ ] Classification rule versions reviewed; changes documented
- [ ] Incident and change records reconciled
- [ ] Disaster-recovery procedure reviewed; restoration rehearsal considered
- [ ] Open security and compliance enhancement items reviewed, including database-level audit tamper evidence (Section 19)

## 18.5 What administrators must not do

> ⛔ **PROHIBITED**
> Do not make entity review determinations using the administrator role. Administration and adjudication are separate duties, and combining them in one person removes the separation the audit trail depends on.
> Do not alter entity data to resolve a finding.
> Do not remove or suppress the demonstration-mode provenance stamp.
> Do not disable a QA gate to unblock a deliverable.

---

# 19. Audit and Traceability

## 19.1 What is logged

| Event | Recorded |
|---|---|
| Entity created | Actor, timestamp, entity |
| Entity updated | Actor, timestamp, entity |
| Entity import | Actor, batch, counts |
| Status changed | Actor, from-state, to-state |
| **Status change refused** | Actor, attempted transition, refusal reason |
| Verification started | Actor, entity, trigger type |
| Verification completed | Actor, entity, findings count, resulting status |
| Finding created | Entity, finding type, severity, title |
| Review resolved | Actor, review, resolution, reclassified bucket, **rationale**, IP address |
| NPI flagged | Entity, reason |
| AI entity resolution (when enabled) | Model, prompt version, input, output, confidence, threshold, latency, version, error |
| Batch validation started | Actor, population, sample size, data source |
| Authentication and authorization events | Actor, outcome |
| Administrative actions | Actor, action, target |

Refused actions are logged as well as successful ones. An attempt to move an entity straight from draft to active is precisely the event a reviewer wants to see, and logging only successes would hide it.

## 19.2 What evidence is retained

- The raw response from each authoritative source, per entity, per run
- The five-state result and reason for every source
- The coverage note for every review
- The finding codes raised and the field comparisons that produced them
- The classification bucket, the rule code, and the **rule version** that produced it
- The reviewer's resolution and rationale
- The sampling parameters — confidence level, margin of error, proportion, and **seed** — so any sample can be re-drawn and checked

## 19.3 The accurate statement about audit integrity

> ⚠️ **IMPORTANT — HOW TO DESCRIBE AUDIT INTEGRITY**
> Audit records are written in append-only mode through the application. Once created, audit entries cannot be modified or deleted through normal application operations. Note: this is an application-level control, not cryptographic immutability. Database-level tamper evidence (hash chains) is identified as an open enhancement item.

Do not describe the audit trail as "immutable" in correspondence with the government. The accurate term is **append-only through the application**. The distinction matters to an assessor: an application-level control governs what the application will do, while cryptographic tamper evidence would allow any alteration made by other means to be detected. The latter is an identified enhancement, not a present capability.

One related behaviour users should understand: when a user account is deleted, audit records referencing that user are **retained**, but their attribution to that user is severed. The record survives; the name attached to it does not. Where continuous attribution matters, disable accounts rather than deleting them.

## 19.4 What users should put in review notes

Write for a reader a year from now who has no context:

| Include | Do not include |
|---|---|
| Which sources you relied on | Vague conclusions such as "looks fine" |
| What each source actually returned | Personal opinions about the entity |
| Which sources did not answer, and that you accounted for it | Protected health information |
| Why the evidence supports your bucket | Credentials or internal system detail |
| Any assumption you made, stated as an assumption | Speculation presented as fact |

## 19.5 Compliance posture — precise statements

The following statements are the accurate form. Use them verbatim; do not paraphrase them upward.

**Security categorization.**
> The system's target security categorization is Moderate, based on an organizational assumption documented in the System Security Plan. A formal FIPS 199 categorization with explicit impact levels has not been independently performed.

**NIST SP 800-53 controls.**
> 92 security controls from NIST SP 800-53 Rev. 5 Moderate baseline have been mapped and self-assessed (78 Implemented, 12 Partially Implemented, 2 Inherited). These are internal implementation statements, not independently assessed or verified by a third-party assessor.

**Accessibility.**
> Accessibility implementation work is in progress across application components. No formal Section 508 conformance testing (axe, pa11y, Lighthouse) or VPAT/ACR has been completed. Implementation activity does not constitute conformance certification.

**Organizational certifications.**
> ISO 27001, ISO 9001, and CMMI Level 3 are certifications and appraisals held by Alliance Global Tech, Inc. as an organization. They are corporate credentials. They are not application certifications, and they do not constitute assessment or authorization of this system.

**Authorization status.**
> This application does not hold a FedRAMP authorization or an Authority to Operate. Microsoft Azure's own authorizations cover Azure infrastructure; they do not authorize applications deployed on Azure.

> ⚠️ **WARNING**
> The Compliance card on the dashboard presents these items under a single Active/Pending vocabulary that does not distinguish between an implemented application control, a corporate certification, a security categorization, and a government authorization. When communicating posture to the government, use the statements above rather than the dashboard labels.

---

# 20. Troubleshooting

Each entry gives the symptom as a user sees it, what it most likely means, what the user should do, and when to escalate.

## 20.1 Data source problems

**Symptom: a source shows "Unavailable"**
*Likely meaning:* the source is not configured, or it did not answer this time. This is not necessarily a fault.
*User action:* note it. Any review run while a required source is unavailable will be marked Indeterminate — that is correct. Do not close those records as clean. Re-run once the source recovers.
*Escalate:* to the administrator if a source that is normally live has been unavailable for more than a few hours.

**Symptom: NPPES lookup failure**
*Likely meaning:* a transient outage or timeout at CMS, or a network problem.
*User action:* wait and re-run. NPPES is the primary identity source; almost nothing can be concluded without it.
*Escalate:* administrator, if it persists beyond a single cycle of retries.

**Symptom: SAM.gov unavailable**
*Likely meaning:* this is the known current state. API key is configured. Entity lookup endpoints are returning 404 due to an upstream routing issue at api.sam.gov. This is not an application defect.
*User action:* proceed without SAM evidence. Do not conclude that an entity is clear with SAM. Do not raise it as a new defect.
*Escalate:* no individual escalation needed; the administrator tracks it and reports it to the COR where it affects delivery.

**Symptom: USPS API authentication failed**
*Likely meaning:* an OAuth credential or upstream issue. The system falls back to code-only address normalization automatically.
*User action:* none. Address comparison continues; the result records that standardization came from the fallback rather than from USPS.
*Escalate:* administrator, for the credential.

**Symptom: connector shows "Error" rather than "Unavailable"**
*Likely meaning:* a source that normally answers has actually failed.
*User action:* stop relying on that source's results for the moment.
*Escalate:* administrator, promptly.

**Symptom: "Coverage Assurance not available" or a coverage figure lower than expected**
*Likely meaning:* fewer implemented sources answered than usual. Remember that only implemented connectors count toward coverage, and that SAM is currently excluded.
*User action:* read the coverage note; it names exactly what was checked, what was unavailable, and what is not implemented.
*Escalate:* QA lead, if coverage is materially below the cycle norm.

## 20.2 Verification results

**Symptom: no Medicare enrollment match**
*Likely meaning:* the NPI is not enumerated, or the identifier is wrong.
*User action:* confirm the NPI is correct and passes the check digit. Remember this indicator uses the NPPES endpoint, so a failure here usually accompanies an NPPES failure.
*Escalate:* senior analyst if the NPI is correct and the record still cannot be found.

**Symptom: address mismatch**
*Likely meaning:* usually a formatting difference, which normalization should have resolved; occasionally a genuine relocation or a different site.
*User action:* check whether the finding is `ADDRESS_FORMAT_DIFF` (B2, administrative) or `ADDRESS_STATE_CONFLICT` (B3, a real conflict between sources). Treat them very differently.
*Escalate:* senior analyst for a state conflict.

**Symptom: name mismatch**
*Likely meaning:* depends entirely on the similarity band. An abbreviation difference is routine; an unresolvable name is disqualifying.
*User action:* read the finding code and its band (Section 5.3). Confirm or reclassify with rationale.
*Escalate:* senior analyst for `NAME_COMPLETELY_DIFFERENT` or `NAME_UNRESOLVABLE`.

**Symptom: possible exclusion match**
*Likely meaning:* LEIE returned an exclusion. Determine whether it is active or historical-and-reinstated.
*User action:* an active exclusion forces B4 and Tier 3. Do not attempt to resolve it yourself.
*Escalate:* **immediately**, to senior analyst and the programme manager.

**Symptom: the record is marked "Indeterminate"**
*Likely meaning:* a required source did not answer. The system is refusing to guess.
*User action:* re-run when the source recovers, or review manually with the evidence gap explicitly documented.
*Escalate:* not usually needed; this is designed behaviour.

**Symptom: verification requires review when you expected auto-completion**
*Likely meaning:* the record is B1 but confidence is below 0.95, or coverage was incomplete.
*User action:* review it. Only a clean, high-confidence, fully covered record completes automatically.

## 20.3 Data and import

**Symptom: duplicate entity**
*Likely meaning:* the same identifier appears on more than one record, or an import ran twice.
*User action:* check import history for a repeated batch. Duplicate NPI, HCID, and TEFCAID are detected and raised as high-severity findings.
*Escalate:* administrator, to resolve the underlying record.

**Symptom: import failure**
*Likely meaning:* schema mismatch, malformed file, or an unsupported format.
*User action:* confirm the file is CSV, Excel, JSON, or a FHIR R4 bundle, and that required columns are present. Read the validation errors returned.
*Escalate:* administrator if the file is correct and the import still fails.

**Symptom: empty review queue when work was expected**
*Likely meaning:* the sample has not been drawn for this cycle.
*User action:* confirm the cycle exists and a sample was drawn.
*Escalate:* programme manager.

## 20.4 Access and system

**Symptom: cannot access a page — "Insufficient permissions"**
*Likely meaning:* your role is below the level the page requires. Denial is shown as denial rather than as an empty page.
*User action:* check your role in the Trust Center. Request the appropriate role.
*Escalate:* administrator.

**Symptom: incorrect role or permission**
*Likely meaning:* your account holds a different role than expected, or you need one of the specialized roles that are configured outside the interface.
*User action:* do not work around it.
*Escalate:* administrator — specialized roles are assigned by direct system configuration.

**Symptom: signed out unexpectedly**
*Likely meaning:* the session token expired. The interface checks expiry before each request rather than waiting for a rejection.
*User action:* sign in again. Saved work is retained; unsubmitted form content is not.

**Symptom: AI entity resolution unavailable**
*Likely meaning:* this is the current expected state — AI is disabled on both environments.
*User action:* none. Deterministic matching continues and inconclusive cases route to manual review, which is the designed behaviour.
*Escalate:* not required.

**Symptom: unexpected system error**
*User action:* record what you were doing, the entity or review identifier, and the time. Do not retry repeatedly.
*Escalate:* administrator with those details.

## 20.5 Escalation summary

| Issue | Escalate to |
|---|---|
| Active exclusion or debarment | Senior analyst **and** programme manager, immediately |
| B4 determination | Senior analyst |
| Bucket dispute | Senior analyst |
| Methodology question | QA lead |
| Failed QA gate | QA lead, then programme manager |
| Blocked deliverable | Programme manager |
| Connector outage | Administrator |
| Access or role problem | Administrator |
| Suspected security event | Administrator and the security contact |
| Contract or schedule impact | Programme manager, then the COR |

# 21. User Security Responsibilities

## 21.1 The system use notice

Every user is bound by the notice presented in the Trust Center:

> This is a U.S. Government information system provided for authorized use only. Activity is monitored and audited. Unauthorized use is prohibited and may result in administrative or legal action. By continuing, you consent to monitoring. Handle all data in accordance with its classification.

## 21.2 Data classification

| Classification | What it covers | Who may see it |
|---|---|---|
| **CUI // PII** | Entity names and NPIs | reviewer and above; masked for lower roles |
| **Sensitive** | Findings, dispositions, evidence | Role-gated |
| **Public** | Aggregate metrics and module status — no PII | All authenticated users |

PII is role-gated rather than universally visible. Entity-level detail and exports are restricted to reviewer and above. Aggregate surfaces never expose PII.

## 21.3 Your obligations

| Obligation | In practice |
|---|---|
| **Protect your credentials** | Never share an account. Every action is attributed to the account that performed it, so sharing an account destroys attribution and makes the audit trail unusable |
| **Use the right environment** | Do not perform training or experimentation against production |
| **Handle data by classification** | Do not copy entity detail into email, chat, tickets, or personal files |
| **Report anomalies** | Report suspected security events to your administrator and the security contact promptly |
| **Lock your session** | Sessions expire automatically, but do not leave an authenticated session unattended |
| **Least privilege** | Request only the role your duties require. Do not request elevation for convenience |
| **Keep notes professional** | Review notes are part of a government record and may be read by an auditor |

## 21.4 What is monitored

Authentication and authorization events, administrative actions, entity and review changes, imports, status transitions including refused ones, and report generation. Production carries application monitoring and security alerting.

---

# 22. What Users Must Not Do

> ⛔ **PROHIBITED — OPERATIONAL GUARDRAILS**
> These are not stylistic preferences. Each one exists because violating it produces a defective government record.

## 22.1 Evidence and determinations

1. **Do not override authoritative evidence without a documented reason.** A reclassification against what a federal source returned requires a rationale that explains why the source is not decisive here.
2. **Do not treat "unavailable" as "verified."** A source that did not answer has told you nothing about the entity.
3. **Do not treat "unavailable" as "not found."** One is a gap in your evidence; the other is evidence against the entity. They are opposite.
4. **Do not close an Indeterminate record as clean.** That status exists precisely to stop this.
5. **Do not manually change evidence, or entity data, to force a successful result.**
6. **Do not infer a clean result from a missing finding** when the source that would have produced that finding was unavailable, not implemented, or non-functional. This applies today to SAM.gov and to payment suspension.
7. **Do not resolve a record without a rationale you would defend in an audit.**

## 22.2 AI

8. **Do not treat AI-generated content as authoritative evidence.** An AI recommendation is context for a human, never the basis of a determination — and AI is disabled at present in any case.

## 22.3 Environments

9. **Do not use development results for production decisions.**
10. **Do not treat demonstration-mode output as official verification**, on either environment, for as long as the provenance stamp is present.
11. **Do not remove, crop, or suppress the provenance stamp** on any exported document.

## 22.4 Process

12. **Do not bypass required human review.** Only a clean, high-confidence, fully covered B1 completes without a person.
13. **Do not work around a failed QA gate.** A blocked deliverable is the control working.
14. **Do not close a B4 at reviewer level.** Escalate it.

## 22.5 Security and data handling

15. **Do not share credentials**, or work under another person's session.
16. **Do not upload unnecessary sensitive information.** Import only the fields the review requires.
17. **Do not copy entity PII out of the platform** into email, chat, tickets, spreadsheets, or personal storage.

## 22.6 Interpretation

18. **Do not assume a connector health check equals verification success.** A green connector means the source is reachable. It says nothing about any particular entity.
19. **Do not treat organizational certifications (ISO, CMMI) as application security controls.** They are corporate credentials held by AGT, not statements about this system.
20. **Do not cite the dashboard Compliance card as evidence of application compliance.** Use the precise statements in Section 19.5.
21. **Do not present a Registry structural result as an authoritative verification.** The Registry does not query federal sources.

---

# 23. Daily, Weekly, and Monthly Checklists

## 23.1 Reviewer — daily

- [ ] Signed in to the correct environment
- [ ] Demonstration-mode banner noted
- [ ] Mission Control read: notifications, connector health, queue depth
- [ ] Connector card expanded; live sources confirmed before starting work
- [ ] Priority Reviews worked first
- [ ] Entity Reviews worked in SLA order — overdue, then at risk
- [ ] For every entity: each source result read individually, not just the summary
- [ ] Coverage note read before accepting any favourable result
- [ ] Every determination carries a rationale naming sources and evidence
- [ ] Every B4 and every active exclusion escalated, not closed
- [ ] No Indeterminate record closed as clean
- [ ] End-of-day queue check for newly overdue work

## 23.2 Senior analyst — daily

- [ ] B3 escalation queue cleared or triaged
- [ ] Bucket disputes adjudicated
- [ ] Every override carries a recorded reason
- [ ] Inter-rater agreement reviewed; divergence raised with the QA lead rather than corrected case by case

## 23.3 QA lead — daily

- [ ] QA score and alerts reviewed
- [ ] Evidence completeness and internal consistency gates run
- [ ] Sampling validation confirmed for the active cycle
- [ ] Inter-rater agreement reviewed
- [ ] SLA gate reviewed
- [ ] Report gate run before any deliverable — and honoured if it fails

## 23.4 Programme manager — daily

- [ ] Cycle progress against schedule confirmed
- [ ] Overdue volume and trend reviewed
- [ ] QA report gate confirmed passed before generating a deliverable
- [ ] COR-directed priority reviews actioned the day they arrive
- [ ] Provenance stamp confirmed on any report before circulation

## 23.5 Administrator — daily, weekly, monthly

See Sections 18.2, 18.3, and 18.4.

## 23.6 Weekly — all operational roles

- [ ] Open findings older than one week reviewed
- [ ] Recurring finding patterns identified and raised
- [ ] Sample progress against cycle plan confirmed
- [ ] Connector availability trend reviewed across the week
- [ ] Rationale quality spot-checked by the QA lead

## 23.7 Monthly — programme

- [ ] Cycle outcomes summarized by effective bucket
- [ ] Coverage reported honestly, including sources not implemented and SAM's upstream condition
- [ ] Overdue and SLA performance reported
- [ ] Methodology or rule changes documented with their version
- [ ] Outstanding configuration gaps reviewed with the COR: entity population data, SAM upstream, RCE Directory, IQVIA OneKey
- [ ] Access recertification completed

---

# 24. End-to-End Example Walkthrough

> ℹ️ **NOTE — FICTIONAL DATA**
> Every value in this walkthrough is fictional and is used for training only. The organization, identifiers, and addresses do not correspond to any real entity.

## 24.1 The scenario

**Sample Healthcare Organization**

| Field as submitted | Value |
|---|---|
| Legal name | Riverbend Regional Med Ctr |
| NPI | 1999999984 *(fictional)* |
| Address | 400 Riverbend Pkwy, Suite 210, Columbus, OH 43215 |
| Entity type | Organization (Type 2) |
| Entity level | Participant |
| TEFCAID | TEF-OH-004821 *(fictional)* |
| HCID | HC-4821-OH *(fictional)* |

## 24.2 Step 1 — The record enters DocuAction

A contributor uploads the cycle's entity file at Data Import (`/tefca-arc/import`). The file is a CSV.

**What happens:** the schema is validated, duplicates are detected, and the batch is recorded in import history with a batch identifier. An audit entry `entity_import` is written with the actor and counts.

**Result:** the entity exists in the platform, attributable to a named import batch.

## 24.3 Step 2 — Validation of what was received

Before any source is contacted, the platform checks what it has:

| Check | Outcome |
|---|---|
| TEFCAID present | Yes |
| HCID present | Yes |
| NPI present | Yes |
| NPI check digit valid | Passes |
| Entity level valid | Participant |

**Result:** the record is well-formed enough to verify. Had the NPI failed its check digit, that would be a malformed identifier finding — not a missing organization.

## 24.4 Step 3 — Sample selection

The entity is included in the cycle sample. The sample was drawn with a Cochran calculation, finite population correction applied, at 95% confidence and 5% margin of error, using a recorded seed.

**Why this matters:** because the seed is stored, this exact sample can be re-drawn a year from now and shown to be the same sample. That is what makes it evidence rather than an assertion.

## 24.5 Step 4 — Authoritative source verification

A reviewer opens the entity and runs verification. Three sources are queried concurrently.

| Source | State | What it returned |
|---|---|---|
| **NPPES** | `verified` | NPI 1999999984 active, Type 2, legal name "Riverbend Regional Medical Center", taxonomy General Acute Care Hospital, location 400 Riverbend Parkway, Columbus OH 43215 |
| **Medicare enrollment indicator** | `verified` | Provider enumerated; provider type consistent. Payment suspension: **not provided by this source** |
| **OIG LEIE** | `clear` | No exclusion record |
| **SAM.gov** | `not_checked` | "API key configured. Entity lookup endpoints returning 404 — API version under investigation." |

**Coverage note:** *3 of 3 implemented sources checked. Not implemented (excluded from coverage): irs, state_registry.*

> ℹ️ **NOTE**
> Coverage reads 3 of 3 because SAM is not counted among implemented connectors in this path. The reviewer must still register that no federal registration evidence exists for this entity.

## 24.6 Step 5 — NPI verification

Submitted NPI matches the NPPES record exactly. Status is active, not deactivated. Enumeration type is Type 2, consistent with an organization.

**Finding:** none.

## 24.7 Step 6 — Name matching

| Stage | Value |
|---|---|
| Submitted | Riverbend Regional Med Ctr |
| NPPES | Riverbend Regional Medical Center |
| After normalization | `riverbend regional medical center` on both sides — "Med Ctr" expands to "Medical Center" |
| Similarity | 0.94 |

0.94 falls in the 0.70–0.89 band once the raw comparison is scored, producing an abbreviation finding rather than a clean pass.

**Finding:** `NAME_ABBREVIATION_DIFF` — *Name difference attributable to abbreviation.* Bucket contribution: **B2**. Confidence deduction: 0.10.

## 24.8 Step 7 — Address cross-reference

| Stage | Value |
|---|---|
| Submitted | 400 Riverbend Pkwy, Suite 210, Columbus, OH 43215 |
| NPPES | 400 Riverbend Parkway, Columbus, OH 43215 |
| After Publication 28 normalization | `400 RIVERBEND PKWY COLUMBUS OH 43215` on both sides |
| State comparison | OH = OH — no conflict |
| ZIP comparison | 43215 = 43215 |
| Street core comparison | Matches once the secondary unit designator is set aside |

**Finding:** none. The suite number is a secondary designator, not an address difference.

## 24.9 Step 8 — Exclusion screening

Name and NPI submitted to OIG LEIE. No exclusion record, current or historical.

**Finding:** none.

## 24.10 Step 9 — Medicare enrollment verification

The enrollment indicator confirms the provider is enumerated with a consistent provider type and address.

**Payment suspension:** reported as *not provided by this source*. It is not reported as clean.

**Finding:** none — and no finding could be raised about payment suspension, because the data element is unavailable.

## 24.11 Step 10 — Rules applied and classification

| Input | Value |
|---|---|
| Findings | `NAME_ABBREVIATION_DIFF` |
| Bucket rule | Worst finding wins |
| Bucket | **B2 — Minor or Administrative** |
| Confidence | 0.90 (1.00 less the 0.10 abbreviation deduction) |
| Required sources unavailable | None among implemented sources |
| Indeterminate | No |
| Classification state | CLASSIFIED |
| Auto-classify | **No** — auto-completion requires B1 |
| Tier | **Tier 2 — analyst review** |

## 24.12 Step 11 — Evidence collected

Retained against the review: the raw NPPES response, the enrollment indicator response, the LEIE response, the SAM not-checked state with its reason, the coverage note, the field-by-field comparison table, the finding code, the confidence calculation, and the rule code and rule version applied.

## 24.13 Step 12 — Exception handling and human review

The record reaches the reviewer's queue as Tier 2, bucket B2.

**The reviewer:**

1. Reads the coverage note. Three implemented sources answered; SAM did not and is known non-functional.
2. Reads each source result individually.
3. Examines the name finding and agrees the difference is genuinely an abbreviation, not a different organization.
4. Confirms no exclusion.
5. Notes that payment suspension could not be assessed.
6. Selects **Confirm** and writes the rationale.

**Rationale as recorded:**

> "NPPES confirms NPI 1999999984 active, Type 2, legal name 'Riverbend Regional Medical Center'. Submitted 'Riverbend Regional Med Ctr' normalizes to the same string; similarity 0.94 — abbreviation difference only. Address matches after Publication 28 normalization; the suite designator is the sole difference. LEIE returned no exclusion, current or historical. Medicare enrollment indicator confirms enumeration; payment suspension not available from this source and therefore not assessed. SAM not checked — upstream 404 condition, so no federal registration evidence exists for this entity. Confirming B2 on the abbreviation finding."

**What is written to the audit trail:** actor identity, IP address, timestamp, resolution `confirmed`, effective bucket B2, and the rationale in full.

## 24.14 Step 13 — QA

The QA lead's checks on this record:

| Check | Outcome |
|---|---|
| Evidence retained for every source | Pass |
| Classification consistent with findings | Pass — B2 follows from an abbreviation finding |
| Coverage documented | Pass |
| Rationale names sources and evidence | Pass |
| Human review completed where required | Pass — Tier 2 was not auto-closed |
| Indeterminate handling | Not applicable — no source was unavailable |

## 24.15 Step 14 — Final disposition

**Effective bucket: B2 — Minor or Administrative.** The entity's registered name differs from its NPPES legal name by an abbreviation. This is disclosed, explained, and not disqualifying.

## 24.16 Step 15 — Report generation

The record is included in the cycle report under B2, with its finding code and the reviewer's rationale.

> ⚠️ **WARNING**
> While the platform is in pre-production demonstration mode, this report carries the stamp "MOCK — demonstration data only. Do not use for operational decisions." The walkthrough above demonstrates the operational process correctly, but its output is not an official verification determination.

## 24.17 Step 16 — Audit history

The complete history of this entity, retrievable at any later date:

```
  entity_import            actor, batch, timestamp
  verification_started     actor, entity, trigger
  verification_completed   actor, findings count, resulting status
  finding_created          NAME_ABBREVIATION_DIFF, severity, title
  review_resolved          actor, IP, resolution=confirmed,
                           effective bucket=B2, rationale (full text)
```

Each entry is append-only through the application, attributable to a named actor, and timestamped.

## 24.18 A contrasting case — what an outage looks like

Had OIG LEIE been unavailable at the moment of this run, everything above would be identical except:

| Field | Value |
|---|---|
| LEIE state | `unavailable` |
| Indeterminate | **Yes** |
| Indeterminate reason | "Source unavailable: leie" |
| Bucket label | Unchanged for B2, but a clean B1 would read **"Indeterminate — Source Unavailable"** |
| Auto-classify | Forced off |
| Tier | **Tier 2**, regardless of confidence |

The reviewer could not close it as clean. The correct action is to re-run once LEIE recovers, or to review it manually with the evidence gap documented explicitly in the rationale.

---

# 25. Frequently Asked Questions

**Why does every report say "MOCK — demonstration data only"?**
Because the platform is in pre-production demonstration mode. ONC has not yet provided the entity population data. The stamp is applied automatically from a single source of truth and cannot be removed by any user setting. It disappears on its own when the entity data is loaded and the entity-data key is configured.

**If production is stamped MOCK, what is the difference between development and production today?**
Architecturally, everything — separate compute, separate databases, separate identity, separate secrets, monitoring, and audit retention. Operationally for TEFCA, nothing yet: both serve demonstration data and are configured identically. Production becomes authoritative when the entity data arrives.

**Why do NPPES and Medicare enrollment always succeed or fail together?**
Because the Medicare enrollment indicator is derived from CMS public data obtained through the NPPES registry endpoint. It is not a direct integration with the CMS PECOS system. Two green indicators here represent one underlying source.

**Why can I never see a payment-suspension result?**
Because that data element requires a COR-provisioned CMS feed the programme does not have. The platform reports it as "not provided by this source" rather than as clean. Its absence is not evidence that no suspension exists.

**Why is SAM.gov always unavailable?**
The API key is configured. Entity lookup endpoints are returning 404 due to an upstream routing issue at api.sam.gov. This is not an application defect. No SAM-dependent conclusion can currently be drawn.

**I clicked Verify in the Registry and got "verified" — is that entity confirmed?**
No. The Registry Verify function performs internal structural data-quality checks only. It does NOT query external authoritative sources. External verification is performed through the ARC module. A Registry "verified" means the record is internally well-formed.

**Why did a record with no problems still require my review?**
Auto-completion requires all three of: bucket B1, confidence 0.95 or above, and full source coverage. Anything less reaches a person.

**What is the difference between "not found" and "unavailable"?**
`not_found` means the federal source answered and does not hold this record — evidence about the entity. `unavailable` means the source did not answer — a gap in your evidence. They are opposite and must never be conflated.

**Can I close an Indeterminate record if everything else looks fine?**
No. Indeterminate means a required source did not answer, so a clean-looking result cannot be trusted — the missing source is exactly what would have flagged a problem. Re-run when it recovers, or review manually with the gap documented.

**Is AI making any decisions?**
No. AI entity resolution is disabled on both environments. Even when enabled it never decides: it produces a recommendation a human accepts or rejects, and every AI-touched record carries mandatory manual review at any confidence level.

**What data would be sent to AI if it were enabled?**
Six public fields only — name, address, NPI, entity type, state, TEFCAID — enforced by an allowlist. Never protected health information, patient data, or Social Security numbers.

**Is the audit trail immutable?**
Audit records are written in append-only mode through the application. Once created, audit entries cannot be modified or deleted through normal application operations. This is an application-level control, not cryptographic immutability. Database-level tamper evidence (hash chains) is identified as an open enhancement item.

**Is DocuAction FedRAMP authorized?**
No. This application does not hold a FedRAMP authorization or an Authority to Operate. Azure's own authorizations cover Azure infrastructure and do not authorize applications deployed on it.

**AGT holds ISO 27001 and CMMI Level 3 — does that certify the application?**
No. Those are corporate credentials held by Alliance Global Tech as an organization. They are not application certifications and do not constitute assessment or authorization of this system.

**Is the system Section 508 compliant?**
Accessibility implementation work is in progress across application components. No formal Section 508 conformance testing (axe, pa11y, Lighthouse) or VPAT/ACR has been completed. Implementation activity does not constitute conformance certification.

**Why do two parts of the application match names differently?**
The ARC module uses Jaro-Winkler similarity scoring for entity name comparison. The Registry module uses Levenshtein distance with a different threshold. Both perform fuzzy name matching but use different algorithms and sensitivity settings. This is a known inconsistency scheduled for unification in a future release.

**Why can't the administrator give me the reviewer role in the interface?**
Administrators can assign these roles through the application interface: admin, manager, contributor, viewer. Specialized operational roles (reviewer, senior_analyst, qalead, program_manager) are configured by the system administrator through direct system configuration. Contact the system administrator to request assignment to these roles.

**A page says "Insufficient permissions" — is it broken?**
No. The platform shows denial as denial rather than as an empty page, because an empty page reads as "no records exist" and would mislead you. Request the appropriate role.

**What happens to audit records when a user account is deleted?**
The audit records are retained, but their attribution to that user is severed. Where continuous attribution matters, disable accounts rather than deleting them.

**Can I re-draw a sample to check it?**
Yes. The sampling seed is stored with the sample along with the confidence level, margin of error, and proportion, specifically so a reviewer can reproduce the draw and verify it.

---

# 26. Glossary

| Term | Definition |
|---|---|
| **ARC** | Audit, Review & Compliance — the DocuAction module that performs contract verification work against authoritative federal sources |
| **Append-only** | Records are added but not modified or deleted through the application. Distinct from cryptographic immutability |
| **Authoritative source** | A federal system of record whose answer is treated as fact — here, NPPES and OIG LEIE |
| **B1 / B2 / B3 / B4** | The discrepancy classification buckets: No Discrepancy, Minor or Administrative, Inexplicable, Non-Compliant. The worst finding determines the bucket |
| **BAA** | Business Associate Agreement — the HIPAA contract governing handling of protected health information by a service provider |
| **CCN** | CMS Certification Number — a Medicare provider identifier distinct from the NPI |
| **CMS** | Centers for Medicare & Medicaid Services, part of HHS. Operates NPPES and Medicare enrollment |
| **Cochran sampling** | A statistical method for calculating how large a sample must be to draw conclusions about a population at a stated confidence level and margin of error |
| **Confidence (statistical)** | The probability that a sample's conclusion holds for the whole population. Cycles here use 95% |
| **Confidence (verification)** | A score from 0 to 1 expressing how certain the engine is about one entity's classification. Reduced by each finding |
| **COR** | Contracting Officer's Representative — the government official who directs the contractor's work |
| **Corroboration** | Confirmation of the same fact by an independent second source |
| **Coverage** | How many implemented sources actually answered for a given review. Reported as a note on every review |
| **CUI** | Controlled Unclassified Information — government information requiring safeguarding but not classified |
| **DEV** | The development environment. Never authoritative |
| **Effective bucket** | The reviewer's reclassification where one was made; otherwise the engine's classification. Reports use this |
| **Evidence quality** | Whether the source responses supporting a determination were retained and are complete. Checked by a QA gate |
| **Exception** | A record the automated rules could not resolve, requiring human judgement |
| **Finite population correction** | An adjustment to a sample size for a population small enough that sampling without replacement matters. Prevents a 900-entity frame demanding the same sample as a 96,000-entity one |
| **Finding code** | A specific named defect, such as `NAME_ABBREVIATION_DIFF` or `LEIE_ACTIVE_EXCLUSION` |
| **HCID** | Health Care Identifier — a mandatory TEFCA entity identifier |
| **HIPAA** | Health Insurance Portability and Accountability Act — the federal law governing protected health information |
| **Human review** | A determination made by a qualified person. Required for everything except a clean, high-confidence, fully covered B1 |
| **Indeterminate** | A result that cannot be trusted because a required source did not answer. Never auto-classified, never a pass |
| **Jaro-Winkler similarity** | A string-similarity measure from 0 to 1 that counts shared characters near the same position, penalizes reordering, and rewards a shared opening prefix. Used for organization name matching in ARC |
| **Levenshtein distance** | The number of single-character edits needed to turn one string into another. Used for name matching in the Registry module |
| **LEIE** | List of Excluded Individuals and Entities — the HHS OIG list of parties excluded from federal health care programs |
| **NPI** | National Provider Identifier — a ten-digit identifier for a health care provider, validated by a CMS check-digit algorithm |
| **NPPES** | National Plan and Provider Enumeration System — the CMS registry of NPIs. The platform's primary identity source |
| **OIG** | Office of Inspector General, HHS. Publishes the LEIE |
| **ONC / ASTP** | Office of the National Coordinator for Health Information Technology, now the Assistant Secretary for Technology Policy. The programme sponsor |
| **Participant** | A TEFCA entity that exchanges data through a QHIN |
| **PECOS** | Provider Enrollment, Chain and Ownership System — the CMS enrollment system. **The platform does not have a direct PECOS integration**; enrollment indicators come from CMS public data via the NPPES endpoint |
| **PHI** | Protected Health Information — individually identifiable health information governed by HIPAA. Never sent to any external service by this platform |
| **PII** | Personally Identifiable Information. Role-gated to reviewer and above |
| **PROD** | The production environment |
| **QHIN** | Qualified Health Information Network — a top-level TEFCA entity that connects Participants |
| **RBAC** | Role-Based Access Control — permissions granted by role rather than individually |
| **RCE** | Recognized Coordinating Entity — The Sequoia Project, which administers TEFCA. Not currently integrated |
| **SAM.gov** | System for Award Management — the GSA registry of federal registrations, exclusions, and debarments. Currently non-functional upstream |
| **Sample** | The subset of the entity population selected for review in a cycle |
| **Seed** | The value that makes a random sample reproducible. Stored with every sample so a draw can be checked |
| **SLA band** | on_track, at_risk, or overdue — derived from the review due date |
| **Source state** | One of five: verified, not_found, not_checked, unavailable, failed |
| **SSP** | System Security Plan — the document describing a system's security controls |
| **Subparticipant** | A TEFCA entity that exchanges data through a Participant |
| **Taxonomy code** | The standard code describing a provider's type and specialization, held in NPPES |
| **TEFCA** | Trusted Exchange Framework and Common Agreement — the national framework for health information exchange |
| **TEFCAID** | The mandatory TEFCA entity identifier |
| **Tier 1 / 2 / 3** | Review routing: auto-complete, analyst review, escalation |
| **UEI** | Unique Entity Identifier — the federal award identifier issued through SAM.gov |
| **USPS Publication 28** | The USPS standard for addressing. Its abbreviation and formatting rules are implemented in the code-only address normalizer |
| **Verification** | Confirming a fact against an authoritative source. Distinct from the Registry's internal structural checks |

---

## Document control

| | |
|---|---|
| **Title** | DocuAction TEFCA — User & Operations Guide |
| **Version** | 1.0 |
| **Date** | 10 August 2026 |
| **Contract** | 7571MN26F80064 — TEFCA Audit, Review & Compliance |
| **Contractor** | Alliance Global Tech, Inc. |
| **Classification** | CONFIDENTIAL — Internal & Government Use |
| **Basis** | Written from inspection of the running application: source code, configuration, database models, API routes, connectors, live production endpoints, and Azure environment configuration, as at 10 August 2026 |
| **Review trigger** | Re-issue when ONC entity population data is received, when SAM.gov is restored, when AI entity resolution is enabled, or when the dashboard status model is revised |
