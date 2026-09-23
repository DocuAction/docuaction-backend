# Overnight executive summary — Entity Identity & Location Intelligence hardening

Date: 2026-09-12 · Branch `feat/entity-intelligence-foundation` · Draft PR #54 · Feature OFF · Not merged · Not deployed · Shared QA baseline untouched.

## WHAT WAS TESTED

- 342 synthetic tests on the isolated capability (59 from the foundation + 283 new): flag hardening, NPPES parser robustness (long/Unicode/punctuated names, ZIP+4, suites, directionals, missing/extra columns, header drift, empty and header-only files, duplicates, embedded newlines, oversized fields), normalisation with no false equivalence, verified CMS sentinels (type code 6, `<UNAVAIL>`), delta scope and wording, value-handling modes, run status, prior-review passthrough, adversarial corpus A–R, no-voting proofs, explanation-template banned-claims scan, closed assessment vocabulary, migration fail-closed against Azure/private/look-alike hosts, intake safety (zip-slip, bombs, allowlist, CSV injection, limits, decoding), static security review, seeded invariant tests.
- Full backend suite: 3202 passed / 333 skipped / 0 failed.
- OpenAPI, Alembic head and pre-existing file diff re-proven identical to the foundation proof.
- Synthetic performance 2k / 5k / 10k / 25k / 50k organisations.
- Isolated DDL on an ephemeral local PostgreSQL 18.3 cluster (credential-free, deleted afterwards).
- bandit on the isolated packages.

## WHAT WAS FOUND

1. `flag_enabled` used `bool()`: the string `"false"` would have enabled a feature. (Isolated; fixed.)
2. In current CMS data every organisation's main-file "Provider Other Organization Name" is the placeholder `<UNAVAIL>` with type code 6, which the readme defines as a pointer to the Other Name Reference File. The adapter would have emitted `<UNAVAIL>` as a name of UNKNOWN kind and could have produced a false AMBIGUOUS_NAME. (Isolated; fixed; reference file is now documented as mandatory for DBA evidence.)
3. `<UNAVAIL>` also fills the EIN and Parent Organization TIN columns; it is undocumented in the readme and CodeValues. (Recorded in the traceability table; parser blanks it.)
4. Parse outcome was implicit; a csv-module error on an oversized field would have raised mid-file. (Fixed: explicit OK / PARTIAL / FAILED, `stopped_at_line`, notes.)
5. Import-time `assert` guarding the assessment vocabulary would vanish under `python -O`. (Fixed: `RuntimeError`.)
6. Google Maps Platform Service Specific Terms could not be fully retrieved by the research tool; the caching-period clause is therefore not asserted and stays an open legal question.
7. IQVIA's public page adds no layout information; AWAITING_SCHEMA stands.

## WHAT WAS FIXED (all inside the isolated packages)

Strict flag parsing; `<UNAVAIL>` and pointer-code-6 handling; explicit parse status and csv-error containment; delta scope with careful wording; source-authority classes (RCE-provided third party, DocuAction historical, prior human determination) proven non-weighted; value-handling modes applied at serialisation; data-rights descriptor per adapter; state-registry capability availability enum; run status and prior-review echo; IQVIA profile and unknown-field report; intake safety helpers; import guard.

## WHAT WAS NOT CHANGED

Tasks 1–6 code · RBAC · authentication · reports · Learning Center (1.2.0) · frontend (no commit) · `alembic/versions/` · shared DEV QA database · PROD · GitHub/Azure OIDC · any secret · any live QA data · the only pre-existing file touched remains `app/core/config.py` (+12 lines from the foundation sprint).

## WHAT IS NOW READY

- The NPPES V2 corroboration path is researched against primary sources with a per-code traceability table, hardened, and performance-characterised.
- The comparison/delta/assessment engine is adversarially tested and its vocabulary and wording are enforced by tests.
- The persistence decision is written up with a recommendation.
- The IQVIA receiving pipeline is implemented up to the human-review boundary and a question-only intake checklist exists.
- The acquisition job, data-rights register, observability model, UI spec and Task 2–5 integration analysis exist as designs.

## WHAT NEEDS HIS DECISION (program owner)

- Gate A — Accept the hardening commit(s) on the draft PR (still DRAFT, still unmerged).
- Gate B — Confirm the source-authority class for the expected IQVIA file (`RCE_PROVIDED_THIRD_PARTY` recommended).
- Gate C — Approve building the NPPES acquisition job as designed (network code, scheduler) — after QA closure.
- Gate D — Persistence ADR: option B (separate bounded context) recommended; A or C otherwise.
- Gate E — Whether the read-only analyst panel (UI spec) is wanted; it triggers a Learning Center module and an OpenAPI change.
- Gate F — Whether to ask counsel the Google questions now or defer Google entirely.
- Gate G — Whether any state registry jurisdiction should be evaluated for terms.
- Gate H — Whether the active-falsification extension should be raised with ONC as a methodology question.

## WHAT NEEDS ONC / RCE INPUT

Which delivered fields are the subject of identity review; whether NPPES/IQVIA evidence is admissible in analyst reasoning and how it must be cited; whether an assessment may influence sampling (design says no); the IQVIA delivery terms (checklist sections A–E); whether any evidence statistic may appear in a Government deliverable.

## WHAT MUST WAIT FOR THE IQVIA FILE

Field mapping approval; observation production; provenance values (delivery reference, dataset date); data-rights register entry moving from AWAITING_DELIVERY_TERMS to DOCUMENTED; any IQVIA test beyond generic synthetic headers.

## WHAT MUST WAIT UNTIL QA COMPLETES

Merging PR #54; any Alembic revision (either chain); enabling any flag in DEV; the program-delivery adapter that reads curated entities; any route; any UI; any Learning Center change.
