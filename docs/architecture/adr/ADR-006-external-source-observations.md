# ADR-006: External sources are observations; ONC RCE is the only TEFCA authority

**Alliance Global Tech, Inc. (AGT)** — Copyright © 2024–2026
**Status:** Accepted (foundation); IQVIA observation tables PROPOSED, blocked on licensed specifications
**Deciders:** DocuAction Engineering (@imran-agt), TEFCA ARC QA lead
**Applies to:** DocuAction TEFCA ARC backend, rule set 1.3.0 and later

---

## Context

The ARC programme is adding IQVIA OneKey (HCO, HCP, affiliation) as an
evidence source beside NPPES, CMS PECOS/CCN and the monthly ONC RCE snapshot.
The licensed file specifications, sample files and a data-use approval for
IQVIA are **not in DocuAction's possession** at the time of this decision.
The September 2026 snapshot work (`docs/rce/SEPTEMBER_2026_RECONCILIATION.md`)
established the persistence the sources will share: a `source_snapshot`
ledger, `entity_source_match`, and `arc_assessment_run`.

Two failure modes drove the decision: (1) an external record that *looks* like
a TEFCA organisation being used to create, end or re-parent a TEFCA
relationship; (2) a "match" that a human never determined becoming
reportable.

## Decision

1. **Source authority.** TEFCA facts — class (`sequoiaorgtype`), managing
   QHIN, `partOf`, `active`, purposes of use, node type, TEFCA identifiers —
   come from the ONC RCE delivery and from nowhere else.
   `source_matching.TEFCA_AUTHORITATIVE_FIELDS` is the list;
   `assert_not_tefca_fact()` refuses any write from an observation source.
2. **Observation, determination, reportability.** Every external row is an
   *observation* about an entity. An analyst *determines* a match
   (`reviewed_by`); independent QA makes it *reportable* (`qa_by`, must differ
   — enforced by `ck_esm_maker_checker`). Decisions are append-only rows in
   `entity_source_match`, chained by `supersedes_match_id`.
3. **The one automatic rule.** `NPI_EXACT_TYPE2` may be `AUTO_APPROVED` only
   when the NPI is Luhn-valid, NPPES evidences Type 2 (organisation), and
   exactly one registry entity carries it (`ck_esm_auto_only_npi`). A Type-1
   NPI or an ambiguous NPI is an `EXCEPTION`; CCN, exact name+address+phone
   and fuzzy discovery are `CANDIDATE` only.
4. **Snapshot approval gate.** A licensed snapshot is registered `RECEIVED`
   and is unusable until a QA lead or above (not the registrant) records an
   `APPROVED` successor row with an approval reference. The original row is
   never edited (partial unique index on original registrations only).
5. **Access.** Licensed content is served above the reviewer floor and only
   when `ENABLE_IQVIA_SOURCES` is on. Keys naming IQVIA/OneKey/HCP content
   are redacted from every log line (`logging_config._SENSITIVE_KEY`).
6. **Isolation.** Matches and assessment runs carry the snapshot ids they were
   made against; tenant, delivery, review-cycle and report isolation are
   unchanged.

## Consequences

* IQVIA integration cannot proceed past this foundation until the licensed
  specifications and a data-use approval exist. The observation tables are a
  proposal (`docs/architecture/iqvia_release1_schema_proposal.md`) and are
  deliberately **not** created by `20260921_september_snapshot`.
* Adding a new automatic matching method requires changing a database CHECK
  and this ADR — not a code path.
* Any future rule that wants to move a TEFCA relationship from external
  evidence must first amend decision 1 here.

## Alternatives rejected

* Fuzzy auto-matching with a confidence threshold — rejected: the harm of a
  wrong merge in a Government report is not recoverable by a score.
* Mutable snapshot status — rejected: the approval must be provable after the
  fact, and the app role has no UPDATE on these tables.
