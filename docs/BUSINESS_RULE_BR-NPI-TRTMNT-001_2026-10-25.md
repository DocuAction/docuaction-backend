# BR-NPI-TRTMNT-001 — T-TRTMNT purpose of use requires an NPI

| Field | Value |
|---|---|
| Rule id | `BR-NPI-TRTMNT-001` |
| Statement (as received) | "Under purposeofuse, T-TRTMNT requires an NPI." |
| Provenance | Business clarification from **Meley**, relayed by Imran Siddiqui (Alliance Global Tech) on **2026-10-25**. Meley's organisational role and the written form of the clarification are not on file in this repository; record them when received. |
| What it is | A clarification of **one** requiredness question. It is **not** COR acceptance of the ARC methodology, and it does not close any of the open decisions D1-D9. |
| Implemented as | Quality rule **NPI-001 v1.3.0**, rule set **1.5.0** (`app/tefca_registry/rce/quality_rules.py`: `purpose_requires_npi`, `npi_requirement_basis`). Issue type, severity and authority unchanged: `NPI_REQUIRED`, HIGH, HUMAN_REQUIRED. |
| Effective | Assessments dated **on/after 2026-10-25** (`BR_NPI_TRTMNT_EFFECTIVE`). The quality engine stamps each run with its own `assessment_date`; a run dated earlier keeps the 1.2.0 predicate. |
| Scope | The exact token `T-TRTMNT` in the delivered `purposesofuse` list (split on `, ; |` and whitespace; surrounding whitespace removed; nothing else normalised). **Not** `T-TREAT` (a suspected variant the RCE never equated), not `T-PYMNT`, not any other purpose — no supporting policy. |
| Independent of | `hl7orgrole` (a blank role does not suppress it), `sequoiaorgtype` (Participant/Subparticipant labels do not suppress it), `active` (source status is recorded in the finding text, never consulted). |

## Rule dictionary entry (NPI-001 v1.3.0)

| Outcome | Condition | Issue type | Severity | Authority | Meaning |
|---|---|---|---|---|---|
| no finding | `NPI` present | — | — | — | Presence satisfied. Validity (NPI-002/004/003) and NPPES verification are separate, later questions; a present, valid NPI is **not** evidence of a successful NPPES verification. |
| required, basis `PROFILE_ROLE` (1.2.0, unchanged) | `NPI` blank, Participant/Subparticipant, `hl7orgrole` populated and not payer/agency/HIE-HIO | `NPI_REQUIRED` | HIGH | HUMAN_REQUIRED | Review finding for analyst determination. |
| required, basis `PURPOSE_T_TRTMNT` (**new**) | `NPI` blank, `T-TRTMNT` ∈ purposes, assessment ≥ 2026-10-25 | `NPI_REQUIRED` | HIGH | HUMAN_REQUIRED | Review finding for analyst determination; description begins `No NPI supplied, and BR-NPI-TRTMNT-001 requires one`. |
| recorded fact | `NPI` blank, neither basis | `NPI_NOT_SUPPLIED` | INFORMATIONAL | NO_CORRECTION | Absence is a fact, never a failure. Nothing infers that an NPI is *never* required when T-TRTMNT is absent. |

Vocabulary mapping for readers of the overnight instruction: "REQUIRED_NPI_MISSING" ≙ `NPI-001 NPI_REQUIRED`;
"INVALID_NPI" ≙ `NPI-002 NPI_LENGTH_INVALID / MULTIPLE_NPI_IN_ONE_FIELD`, `NPI-004 NPI_FORMAT_INVALID`,
`NPI-003 NPI_CHECKSUM_INVALID` (one primary finding per value, unchanged). No new issue type was
introduced, so historical rows need no legacy mapping and nothing is rewritten.

## What stays separate (and why the finding is not a bucket)

| Concept | Where it lives | Relationship to this rule |
|---|---|---|
| Requiredness finding | `rce_issues` (NPI-001) | **This rule.** |
| Identifier validity | NPI-002/004/003 | Independent; a malformed NPI is not a missing one. |
| Verification outcome | NPPES / PPEF / LEIE / SAM evidence rows | Independent; presence does not verify. |
| Workflow hold | Curation / `promotion_status` | Existing HUMAN_REQUIRED controls apply; no new hold rule was invented. |
| B1-B4 candidate | `bucket_classifier` (RULE-001..005 v3) | **No rule keys on NPI_REQUIRED** (tested). A missing required NPI does not establish B4 or an exclusion. |
| Analyst determination / independent QA / reportable | `review_decision_events`, `reportable_at` | Untouched. |

## Historical applicability (September population, intake `4417b334`)

**The rule's effective date (2026-10-25) is an implementation choice, not a confirmed business effective
date.** It is the day the clarification was relayed and the rule was coded, chosen so that a quality run
stamped before the rule existed keeps producing the result it produced (no silent retroactive rewrite on
re-execution). It must not be described as Meley's accepted policy date. Three dates stay separate: the
September population date (snapshot 2026-09-02, registered 2026-09-24, run of record on rule set 1.3.0),
this implementation date, and the governing SOP/requirement effective date (when the T-TRTMNT requirement
began to bind participants), which was **not supplied** and is not inferred. The
2026-09-24 quality run (rule set 1.3.0) stands as written. Impact if assessed as of 2026-10-25
(read-only DEV count, 24,589 records, all parsed): **3,464** records carry T-TRTMNT with a blank
NPI — every one also has a blank `hl7orgrole` (so all 3,464 are `NPI_NOT_SUPPLIED` INFO today and
would be `NPI_REQUIRED` HIGH under 1.5.0); 2,961 active / 503 inactive; 633 Participant / 2,831
Subparticipant. 1,109 blank-NPI records carry no T-TRTMNT and are unaffected. T-TREAT appears on
10 records (4 blank-NPI) and is **not** covered. These counts belong to the separately labelled
reassessment ("September population assessed using rule set 1.5.0, 2026-10-25"), not to the
September run of record; executing that reassessment is an owner decision.

## Tests

`tests/test_npi_required_t_trtmnt_2026_10_25.py` (token membership incl. multiple purposes and
separators; blank role; any role; Participant/Subparticipant/other labels; active 0/1/blank;
missing/malformed/checksum-invalid/valid NPI; null/other/similar-looking purposes; effective-date
gating with date, datetime and ISO strings; no bucket keys on the finding; vocabulary untouched).
`tests/test_preflight.py` updated: PF-CTX-001 ("requirement undecidable") is no longer recorded
for a blank-role record whose purposes include T-TRTMNT.

## Reconciliation with the September logic draft (second opinion), 2026-10-26

Source compared: `DocuAction_Sept_Records_Logic_Draft (3).xlsx` (draft logic workbook, 24,589 rows,
"proposed, not verified as implemented or tested", no bucket assigned). Read-only DEV counts from
intake `4417b334` (`rce_source_records.parsed`), exact-token split on `, ; |` and whitespace.

### Blank-NPI population: one partition, mutually exclusive, reconciling to 4,573

| Delivered purpose set (blank NPI) | Records | active=1 | active=0 | T-TRTMNT basis |
|---|---:|---:|---:|---|
| T-TRTMNT only | 2,622 | 2,199 | 423 | required |
| T-TRTMNT with other purpose(s) (7 combinations: +T-PH-ECR 698, +T-GOVDTRM-ACP+T-PH-ECR 52, +T-GOVDTRM-ACP 48, +T-IAS 40, nine-token row 2, +T-TREAT 1, +T-HCO 1) | 842 | 762 | 80 | required |
| purposes blank | 683 | 651 | 32 | not on this basis (60 carry a role → PROFILE_ROLE basis decides, as before) |
| single other purpose (T-PH-ECR 156, T-GOVDTRM-ACP 55, T-IAS 38, T-PH 7, T-GOVDTRM-SSD 1, T-TREAT 1) | 258 | 240 | 18 | not required |
| multiple other purposes (T-GOVDTRM-ACP,T-PH-ECR) | 168 | 168 | 0 | not required |
| **Total** | **4,573** | **4,020** | **553** | **3,464 required / 1,109 not** |

The two "required" rows are the 3,464 T-TRTMNT/blank-NPI observations (2,961 active / 503 inactive;
633 Participant / 2,831 Subparticipant). The second-opinion workbook reproduces the same figures
("Treatment asserted with NPI missing" 3,464; its NPI-missing rows split 2,961 / 503 / 1,059 / 50 by
treatment × inactive). A purpose-category subtotal of 3,997 that left 576 records unexplained is superseded by this partition, which places every one of the 4,573 blank-NPI records in exactly one row; the earlier category list is not on file, so the omitted rows cannot be named, and the subtotal matches no row or union of rows here (an arithmetic coincidence, 2,622 + 692 + 683, is noted as an unverified hypothesis only). Multi-purpose combinations are counted once each above.
Tests: `tests/test_npi_required_t_trtmnt_2026_10_25.py::SEPTEMBER_BLANK_NPI_COMBINATIONS`.

### Four dates, kept apart

| Date | Meaning | Status |
|---|---|---|
| **2026-09-02** | snapshot (population) date of `onc-snapshot-20260902.csv` | fixed |
| **2026-09-03** | the "NPI date" the second-opinion draft cites against (its wording: "snapshot date versus the 2026-09-03 NPI date") | **not on file** in this repository as a governing document; recorded as the draft's reference, not adopted. A 2026-09-02 snapshot pre-dates it, so it cannot violate that deadline **solely** because it is assessed in October; any earlier obligation is a separate question for the owner |
| **assessment date** | when a quality run or labelled reassessment is executed (persisted as `assessment_date`) | per run |
| **2026-10-25** | software effective date (`BR_NPI_TRTMNT_EFFECTIVE`) — implementation choice, see above | fixed |

### Points the draft raised, and what the rule does

- **Blank `hl7orgrole`** never suppresses the T-TRTMNT basis (all 3,464 are blank-role; tested).
- **Inactive records** are not exempted and are not routed to B4: `active` is recorded in the finding text and
  never consulted; no seed rule (v1–v3) keys on `NPI_REQUIRED` (tested). Their findings are preserved
  separately (503 of the 3,464) while applicability remains an owner question.
- **T-TREAT** (10 records; 4 with blank NPI, of which 3 also carry T-TRTMNT and 1 does not) stays a reported
  variant with its own documented exception; it is not merged into T-TRTMNT (tested).
- **Purpose-of-use grain:** the delivered `purposesofuse` is one organization-row attribute of the RCE
  directory export. Nothing on file (data contract, 41-field matrices, profile) states whether it enumerates
  initiating purposes, responding purposes or their union; `initiatoronly` is populated on 5 of the 4,573
  blank-NPI rows (value `OTHER`, undocumented). **Applicability by exchange direction is therefore unknown and
  is recorded as unknown**; the rule asserts only token membership.
- **Shared NPIs** remain available to screening without merging entities (resolver fallback, PR #175);
  Case Management returns each entity sharing an identifier as its own row (PR #176).
- **Malformed NPIs:** the September intake carries exactly 3 (`854565` line 2; `116442605` line 4667;
  `1780787176, 1770559767` line 13171). Each parsed value is byte-identical to the NPI field of its original
  source line (CSV-parsed with the intake's delimiter), and 0 of 24,589 records differ between original line
  and parsed NPI — the importer transformed nothing; the malformation is in the delivery.

## NPI-001 1.4.0 / rule set 1.6.0 — the six applicability decision cases (2026-10-26)

Source: TEFCA ARC Task 2 Methodology v1.4, revision of 2026-10-25, "NPI applicability decision cases
(proposed)"; `DocuAction_Sept_Records_Logic_Draft (5).xlsx`, sheet "NPI Decision Cases". Official basis:
**Treatment XP SOP v2.0** (RCE, 7.1.2026; SOP effective 2026-08-03) — §7(a): a Participant or
Subparticipant that operates a Principal Node initiating Queries for either Treatment XP Code MUST have
appropriate NPIs populated in the RCE Directory Service; §7(a)(v): an entry with no NPI and no CLIA Number
that initiates Queries for T-TREAT is not required to populate one; §8: compliance date for §7
**2026-09-03**, applicability QHINs. Implementation: `app/tefca_registry/rce/npi_applicability.py` (pure),
wired into NPI-001 for assessments dated on/after **2026-10-26** (software effective date; implementation
choice, not a policy date).

| Case | Condition | Finding (NPI-001) | Separate applicability record |
|---|---|---|---|
| 1 | `T-TRTMNT` ∈ purposes; NPI missing | `NPI_REQUIRED` HIGH HUMAN_REQUIRED (observation under BR-NPI-TRTMNT-001) | §7 requirement: applies / does not apply / **unresolved** by exchange direction; deadline by snapshot date |
| 2 | `T-TRTMNT` ∈ purposes; NPI supplied | none here — validity NPI-002/004/003, then verification | supplied ≠ verified |
| 3 | `T-TREAT` without `T-TRTMNT`; NPI missing | `NPI_APPLICABILITY_UNRESOLVED` MEDIUM HUMAN_REQUIRED | §7(a)(v) exception: established / not established / **unknown**; never auto pass or fail |
| 4 | other recognized purposes only; NPI missing | `NPI_NOT_SUPPLIED` INFO | does not apply under this rule; other checks preserved |
| 5 | purpose missing or any unrecognized code; NPI missing | `NPI_APPLICABILITY_UNRESOLVED` MEDIUM HUMAN_REQUIRED | unresolved; NPI not assumed optional |
| 6 | `T-TRTMNT` absent; NPI supplied (incl. missing/unrecognized purpose) | none here — validity, then verification | `npi_presence = supplied` is a fact, not inapplicability: T-TREAT present → §7 by direction (applies / does not apply / unresolved); purpose missing or unrecognized → **unresolved**; other recognized purposes only → does not apply |

Rules: exact-token membership on the parsed `purposesofuse` list (`, ; |` and whitespace); `T-TRTMNT` anywhere
takes precedence; look-alikes never match; unknown codes stay unrecognized (a mixed list with an unknown code is
case 5 unless `T-TRTMNT` is present or an NPI is supplied); raw purposes and raw NPI are preserved on the
assignment; blank `hl7orgrole` never suppresses; `active` is recorded, never a case input (no exemption, no
non-compliance); the 1.2.0 PROFILE_ROLE basis is unchanged and still labels the finding when both apply.

**Field mapping (requirement 1):** source header `purposesofuse` → `RecordContext.values["purposesofuse"]` →
`rce_curated_records.exchange_purposes` / `tefca_reg_entities.exchange_purposes` (JSON token lists) with the raw
cell kept in `rce_attributes.purposesofuse_raw`. Tested in `test_source_header_is_purposesofuse_and_the_mapping_is_documented`.

**Kept separate (requirements 6–7, 11–12):** observation (case) · requirement applicability (`initiating_requirement`)
· deadline applicability (`deadline_applicability`) · NPI validity (NPI-002/004/003) · verification outcome (NPPES/PPEF/
LEIE/SAM evidence) · severity · HELD (curation now exempts NPI-001 from automatic hold: `curation.NON_HOLDING_RULES`;
identifier validity still holds) · B1–B4 (no seed rule keys on either finding) · NPPES candidates (never written to the
submitted NPI; analyst confirmation + independent QA stay in the review routes) · IQVIA (not an input anywhere here).

**Exchange direction (requirement 8):** only a delivered `organizationNodeType = initiating-node` marks a record
`initiating`; everything else is `unknown` and the §7 requirement stays **unresolved** with the observation retained.
Whether `purposesofuse` enumerates Organization-level initiating or Endpoint-level responding purposes is not
documented in any delivered specification on file; nothing is inferred from a blank role or a Participant label.

**Four dates (requirements 9–10):** snapshot date (**only** `source_metadata.snapshot_date`, recorded by a Program Manager with its basis through `POST /api/tefca/rce/deliveries/{intake_id}/snapshot-date` and audited as `source_snapshot_date_recorded` — `received_at` is the registrant-stated RECEIPT date and `created_at` the ingestion instant; neither is a population date and neither is used; the engine exposes the recorded value as
`dataset["snapshot_date"]`; **missing → unresolved, never the receipt date, the ingestion date, a file-name inference or the assessment date**). Integration test: snapshot 2026-09-02 recorded, received 2026-09-24, assessed in October → `pre_deadline` (`tests/test_npi_snapshot_date_and_curation_integration_2026_10_26.py`) · assessment date (run start) ·
governing requirement date (§8: 2026-09-03) · software rule-version effective date (2026-10-26; T-TRTMNT basis
2026-10-25). A 2026-09-02 snapshot is `pre_deadline`: it cannot establish a §7 breach; earlier requirements are a
separate assessment and are not asserted by this rule.

**September 2026 reconciliation (descriptive, not compliance):** applying the assignment to the 24,589 delivered
records (fixture `tests/fixtures/september_2026_purpose_npi_distribution.csv`, read-only DEV 2026-10-26) gives
Case 1 **3,464** (active 2,961 / inactive 503; Participant 633 / Subparticipant 2,831) · Case 2 **20,002** · Case 3 **1**
· Case 4 **425** · Case 5 **683** · Case 6 **14** — total 24,589, equal to the independently verified counts. The
September run of record (rule set 1.3.0) is unchanged; any new assessment is saved separately with lineage and
rule version. Tests: `tests/test_npi_applicability_six_cases_2026_10_26.py`.
