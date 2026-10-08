# IQVIA HCP_AFFIL consumption: tested integration proposal (default off)

Status: DRAFT proposal with code behind a flag. Not approved policy. Does not approve a snapshot, activate IQVIA, change B1/B4 rules, change `source_policy` (IQVIA stays `PROPOSED_INACTIVE`) or alter any report.

## Intended use, traced to the design documents
* `docs/architecture/iqvia_release1_schema_proposal.md`: affiliation rows are verbatim licensed observations, "never joined to registry tables directly"; "an affiliation row never becomes a `tefca_entity_relationships` row"; organisation links are candidates unless NPI-exact, Type 2, Luhn-valid and unique.
* Settled direction: HHS/ONC supplied the IQVIA data and directed its use as the fifth source, supplemental to S1-S4 (identity and relationship corroboration; never exclusion clearance; staging is not verified coverage). That direction supersedes the earlier design-update sentence that described the extract as population identification only, and the AGT review copies of the revised methodology record the same S5 wording. No new acceptance gate is introduced here.
* Consequence: three layers stay separate. (1) Direction to use IQVIA as the fifth source: given. (2) Technical readiness: this PR, default off. (3) Activation: QA review of the staged snapshot, then the controlled source-policy activation, each its own step. The code is ADVISORY: it supplies identity/relationship corroboration candidates for analysts and a truthful coverage statement, and nothing that can raise a bucket, mark an entity verified or add a coverage row.

## What the code does (`iqvia_affiliation_consumer.py`)
| Function | Purpose |
|---|---|
| `normalize_npi` / `normalize_ccn` | trim, spreadsheet `.0`, Luhn check (existing validator); CCN upper-case, strip separators, restore exactly one lost leading zero, require the 6-character CMS shape. Failures are reason CODES; values are never echoed |
| `resolve_organisation_from_sets` / `resolve_organisation` | ORG_NPI and ORG_CCN_ID to registry entity candidates. Outcomes: NO_ORG_IDENTIFIER, INVALID_IDENTIFIER, NOT_IN_REGISTRY, CANDIDATE, AMBIGUOUS, CONFLICT |
| `classify_relationship` / `summarise_relationships` | PROVIDER vs CONTACT vs UNTYPED kept apart; relationship rows, distinct HCP keys and distinct HCO keys counted separately; an HCP key carrying two valid NPIs is flagged, never merged |
| `relationships_for_hcp` | indexed lookup (snapshot, HCP key); refuses a snapshot that is not APPROVED/SUPERSEDED; returns provenance (snapshot id, status, record count, hash prefix); `found_in_extract=false` means only "not found in this extract" |
| `coverage_statement` | not_used / staged_not_used / approved_not_consumed / consumed_candidates_only; always `verification_source: false` |

Invariants asserted by tests: `determination = AUTOMATED_CANDIDATE_NOT_CONFIRMED`, `requires_analyst_review = true`, `affects_verification = false` on every outcome; no AUTO_APPROVED; no database write.

## Gating
`ENABLE_IQVIA_AFFILIATION_CONSUMPTION` (default false) controls whether the three routes exist at all; they also need `ENABLE_IQVIA_SOURCES` and the reviewer floor. Identifiers go in POST bodies, not URLs.

## Tests (synthetic only, no database)
31 tests: normalization; positive (single NPI hit, NPI+CCN agree); absent (valid but not in registry, no identifier, invalid identifier are three different outcomes); ambiguous (shared NPI, overlapping sets); conflicting (NPI and CCN point to different entities); relationship-versus-identity; coverage states; eligibility (PENDING refused, wrong source refused, superseded chain read under the root snapshot); masked output; flag-off router empty.

## Masked diagnostic on the staged PENDING snapshot (DEV, read-only, counts only)
Page-level random sample (TABLESAMPLE 0.05%, 3,953 relationship rows) of snapshot afb55a68:
* Organisation resolution: CANDIDATE 213 (5.4%), NOT_IN_REGISTRY 2,772 (70.1%), NO_ORG_IDENTIFIER 965 (24.4%), INVALID_IDENTIFIER 3. All candidates came from NPI. AMBIGUOUS 0, CONFLICT 0.
* Distinct ORG_NPI values 2,319, of which 149 (6.4%) exist in the DEV registry.
* The DEV registry holds NPI identifiers only (21,730 across 49,423 entities); it holds ZERO CCN identifiers, so the CCN path and the NPI-versus-CCN conflict path are untestable against real data here. They are proven with synthetic fixtures only.
* ORG_CCN_ID is empty in about half the rows; non-empty values are mostly 6 digits, with a minority of other shapes (for example 5 digits plus a letter, or 10 characters) that the 6-character CMS rule rejects as CCN_BAD_SHAPE rather than guessing.
* Relationships: 3,564 provider, 389 contact, 0 untyped; 80 HCP-HCO pairs appear under more than one affiliation type; 0 HCP keys with conflicting NPIs; 3,689 distinct HCP keys across 3,953 rows.
Reading: even if approved, most IQVIA organisations are simply not in the ONC-delivered registry. "Not in registry" is neither invalid nor cleared.

## Organisation lookups and indexes (corrected)
An earlier version of this section said organisation-side lookups had no index. That was wrong: the table already has `idx_iqvia_affiliation_hco` on the HCO key. Only lookup BY ORG_NPI / ORG_CCN_ID (inside JSONB) is unindexed (28 s measured on DEV). The organisation-first lookup, the options compared, measured sizes and timings, and the recommendation are in `docs/architecture/iqvia_org_first_design.md`.

## Not included, deliberately
Writing `entity_source_match` rows; flipping `_matching_capability` for AFFILIATION; any report, bucket or `source_policy` change; the stale "no affiliation file exists" wording (candidate follow-up).

## Remaining activation steps (separate, each its own step)
1. QA review of snapshot afb55a68 by a QA lead other than the registrant (testadmin@docuaction.io), after durable-original integrity is verified. Approval is explicit and is not implied by deployment.
2. Controlled `source_policy` activation for IQVIA (currently PROPOSED_INACTIVE), after the snapshot is approved.
3. Whether any organisation candidate may be persisted as `entity_source_match` CANDIDATE rows, and index/storage approval for org-side lookup.
4. Whether a report coverage row for IQVIA is wanted, and its wording (this PR provides the truthful statement function only).
