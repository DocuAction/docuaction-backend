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
