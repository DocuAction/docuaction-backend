# Policy / rule versioning architecture

Question the system must eventually answer: **WHAT RULE APPLIED ON THE REVIEW DATE?** Implemented tonight as a register with effective-date resolution (`app/core/entity_intelligence/policy.py`, seeded by `rce_policy_register.py`, 10 tests). No rule logic executes; the register is consulted for citation and status only.

## Model

```
RuleDefinition(rule_id, program, topic, description)
RuleVersion(rule_id, version, status, authority_layer, source_document, section,
            effective_from, effective_to (exclusive), approved_on, citation_url,
            evidence_requirement, applicability, supersedes, last_verified, note)
PolicyRegister.applicable(rule_id, on_date) -> [RuleVersion]   # EFFECTIVE on that date
RuleVersion.status_on(date)  -> DRAFT | UNDER_CONSIDERATION | APPROVED_FUTURE | EFFECTIVE | SUPERSEDED | GUIDANCE | WITHDRAWN
overlapping(register, date)  -> integrity check: more than one effective version is a register error, never resolved silently
PolicyRegister.proposals()   -> everything non-binding, so product alignment can cite it without treating it as a rule
```

Statuses: DRAFT · UNDER_CONSIDERATION · APPROVED_FUTURE · EFFECTIVE · SUPERSEDED · GUIDANCE · WITHDRAWN. Time moves APPROVED_FUTURE → EFFECTIVE → SUPERSEDED; it never moves a DRAFT, UNDER_CONSIDERATION or GUIDANCE entry anywhere (tested).

## Authority layers (never collapsed)

| Layer | Name | `is_contract_requirement` |
|---|---|---|
| 1 | EXECUTED_CONTRACT (contract / SOW / modifications) | yes |
| 2 | COR_ACCEPTED_METHODOLOGY (Task 2, once accepted) | yes |
| 3 | WRITTEN_COR_DIRECTION | yes |
| 4 | RCE_GOVERNING_MATERIAL (Common Agreement, QTF, SOPs) | **no** |
| 5 | FEDERAL_REFERENCE_DATA (NPPES, CMS datasets, LEIE) | no |
| 6 | INDUSTRY_COMMERCIAL (IQVIA, Google) | no |
| 7 | DOCUACTION_PROPOSAL | no |

The seeded register contains layer-4 entries only; every entry is tested to be non-contractual. A layer-1/2/3 register (contract clauses, accepted methodology sections, written COR direction) is the program's to populate from the executed documents, which are not on disk for this branch (see the contract traceability memory: executed award pages not available; D2 unaccepted).

## How a run would cite rules (future, not wired)

`EntityIntelligenceRun` gains `applicable_rules: List[dict]` = `register.status_report(review_date)` filtered to the rules the comparisons touched (e.g. RCE.XP_VETTING for the identity data points; RCE.DIRECTORY_REQUIREMENTS for identifier publication). This is citation, not evaluation: the engine does not decide whether an entity "meets" a rule.

## Deltas

`DeltaScope.RULE_VERSION` exists in the vocabulary for the case "same evidence, different applicable rule between two reviews". It is not produced by `compute_deltas` (which sees observations only); the future integration produces it by comparing `applicable(rule_id, prior_review_date)` with `applicable(rule_id, current_review_date)`.

## Persistence

Under ADR option B the register is a versioned data file (Python or JSON) in the repository, reviewed like code, with `last_verified` on every row. A database table is unnecessary until rules are edited by users, which is not proposed.

## What was deliberately not built

A policy engine that evaluates entities against rule text; automatic download of SOPs; any inference from a rule's existence to an entity's status. Rule versions describe the world's rules; observations describe the world; the analyst connects them.
