# RCE + CMS research — executive decision package

Date 2026-09-12 · Branch `feat/entity-intelligence-foundation` · PR #54 DRAFT, not merged, not deployed · Shared QA baseline untouched · Feature OFF.

## 1. What current RCE rules actually require (organisational identity, as of 2026-09-12)

Vetting SOP v2.0 (effective 2026-08-03) §4.5.2: for an Entrant seeking T-TRTMNT, the Sponsoring QHIN submits the legal entity name, DBA if any, corporate business address, website (or attests none), site of care address (or a stated home/mobile/virtual designation, "not disqualifying"), NPI, type of health care provider, HIPAA CE evidence (Tier 1 transaction document; Tier 2 CMS-directory link accepted only until 2026-12-31), and a trigger description if the Principal Node is not an EHR. §4.5.3 Representative entrants keep the Entrant's legal name/DBA/corporate address. §4.1.9 six-month re-attestation. Common Agreement 2.1 §8.3: QHINs maintain the accuracy of Participant/Subparticipant Directory entries. Directory SOP v1.1 (effective 2026-09-14): Directory represents Nodes; NPI must be published for Treatment; FEIN per Organization resource (out of DocuAction scope). None of this is an ARC contract requirement; it is layer-4 material the contract tells the methodology to align with.

## 2. What is only proposed / under consideration

KYP as a process (Feb 2026 paper): state of incorporation, existing-TEFCAID reconciliation, prior TEFCA participation and termination circumstances, 5%+ owners, org chart, LEIE check, ongoing RCE audits, public map transparency — **not adopted**. Draft SOPs awaiting approval: Restricted Participation Status v1.0, Inquiries and Investigations v1.0, Directory Requirements v1.2. Government Benefits Determination sub-XPs: coming soon.

## 3. What CMS public data can legitimately add

A second federal statement, from a different system (PECOS) with a different meaning: that an enrollment record exists for the delivered NPI in the current extract, its ORG_NAME, its locality (no street), additional NPIs on the enrollment, benefit-reassignment relationships; and, for hospitals/FQHCs/RHCs/hospices, a DBA and a street address. It cannot add history, operational status, licensure or TEFCA status.

## 4. What NPPES can and cannot prove

Can: that an NPI is enumerated to an organisation with a stated legal business name, other names with CMS type codes (DBA only for code 3), a primary practice location, mailing address and additional practice locations, with enumeration/deactivation dates, as of an edition. Cannot: licensure, credentialing, Medicare enrollment, TEFCA eligibility, contractual compliance, current operation at an address. Verified nuance: the main-file other-name field is a pointer (code 6, `<UNAVAIL>`) — the Other Name Reference File is mandatory for DBA evidence.

## 5. What CMS enrollment data can and cannot prove

Can: what the applicable current CMS public enrollment dataset reports for a linked enrollment on its extract date. Cannot: historical enrollment, today's status ("not intended to be used as real time reporting"), street-level location (PPEF), TEFCA eligibility, licensure, that absence means non-enrollment (applicability, population, omissions, other identifiers).

## 6. Should Participation / Program Identity become the sixth dimension?

**Yes — adopted, program-agnostically.** `PROGRAM_PARTICIPATION` observations with `"<PROGRAM>:<KIND>"` roles, compared only within a role, with absence-with-reason semantics. No TEFCA/Medicare constant in Core (tested). Historical identity stays as deltas.

## 7. Source tiers (hypothesis evaluated; one change)

| Tier | Sources | Why |
|---|---|---|
| 1 | ONC/RCE delivered data · NPPES V2 · DocuAction history / prior review | the subject; the identity authority for the delivered NPI; the program's own lineage |
| 2 | CMS public enrollment evidence (PPEF; provider-type files for DBA/street) · RCE-provided IQVIA/OneKey (after terms + mapping) | second federal statement; commercial reference only once its terms are known |
| 3 | Address-quality evidence: USPS-certified (the frozen path already has a configured, unused USPS API) · Google supplemental | supplemental, transient, program approval required |
| Future / program-dependent | State registries · LEIE **as an EI source** · SAM (D4 credential decision) · licensure · other federal/state | LEIE is already used by the frozen Task 3 path; it stays out of EI until the program wants exclusion evidence in the analyst panel |

Change from the hypothesis: OIG LEIE is placed as "already in the Task 3 path, not an EI tier" rather than "future", to avoid implying it is unused. Everything else confirmed.

## 8–11. Task support

- **Task 2**: reusable control-framework elements — evidence vocabulary, applicability, absence semantics, provenance, rule-version register. Any effect on the discrepancy taxonomy (explainable variation, participation rules, provider-type CMS files) requires COR acceptance.
- **Task 3**: NPPES and PPEF corroboration are already in the submitted methodology; EI adds explanation, multi-source guards and what-changed, as analyst context only.
- **Task 4**: delivered-vs-prior deltas (new vs returning, FR-T4-007) are the core value; CMS quarterly cadence limits reference-side change detection; Hospital Enrollments (monthly) is the only faster CMS signal.
- **Task 5**: all of it, scoped to the named entities; rule-version citation and prior-review awareness are most valuable here.

## 12. What requires COR / methodology approval

Explainable-variation as a taxonomy refinement; participation-dimension rules in reports; provider-type CMS files; IQVIA use; Google/USPS external calls; state registries; SAM credential (D4); D4_ADDRESS_MATERIALITY; citing RCE rule versions in Government deliverables.

## 13. Reusable Core that can be built without contract change

Everything on the branch: observation/provenance model, normalisation, comparison signals across six dimensions, deltas with scope, assessment vocabulary, cross-source guard, absence semantics, policy register, intake safety, the read-only CMS adapter (once wired to existing snapshots). All produce context; none produce a category.

## 14. What must wait for the RCE IQVIA file

Terms (checklist A–E), layout, mapping approval, observation production, data-rights status.

## 15. What should not be built

A PECOS connector (no public API; NPPES-proxy confusion already on record); a second PPEF downloader; a policy engine that evaluates entities against rule text; automatic categories; a graph database; KYP "compliance" features encoding a proposal; anything Google-persisting; state-registry scraping.

## 16. Recommended next implementation sprint (after QA closure and gate decisions)

1. Persistence ADR decision (option B) and the separate Alembic chain.
2. Program-delivery adapter (read curated entities → PROGRAM_DELIVERY observations, incl. `TEFCA:MANAGED_BY_QHIN`, `TEFCA:PART_OF`, `TEFCA:PARTICIPANT` participation from `sequoiaorgtype`/`active`) — subject fields to be confirmed by the program.
3. CMSPPEFAdapter over the existing snapshot store via an injected read port; tests listed in the adapter design.
4. Run record cites applicable rule versions (`applicable_rules`) and produces RULE_VERSION deltas.
5. Flag-gated read-only route + analyst panel spec + Learning Center module — only if gate E is approved.

## Evidence for this package

Primary sources read: RCE resources and change-management pages; Vetting SOP v1.0/v2.0 (+ redline), Treatment v2.0, XPs v5.1, IAS v3.0, Directory v1.1 and v1.2 draft, Restricted Participation draft, Inquiries draft, Entity Types SOP, Glossary Jan 2026, Common Agreement 2.1, Consequences v1.0, Feb 2026 Treatment changes paper, RCE FAQ; CMS fact sheet, data.gov DCAT records for PPEF/Hospital/FQHC/RHC/Hospice, 42 CFR 424.517/424.518, PIM ch. 10 §10.1.1 and §10.6.20; OIG LEIE download page and record layout; GSA SAM extracts/API pages. Not retrievable tonight: data.cms.gov pages and PDFs (WAF; the frozen repo code, probed live 2026-08-19, supplied the field names); Google Service Specific Terms (unchanged from the previous sprint).

## Problems found and fixed during this sprint (isolated)

1. The policy register first cited RCE documents by web URL. The platform's frozen provenance guard (`tests/test_data_provenance.py`, a contract statement that entity population data comes from ONC) forbids naming external directory systems in code, and it caught the host name. Fixed: the code register carries host-free document citations ("RCE-published PDF: <file>"); full URLs live only in `RCE_POLICY_VERSION_REGISTER.md`, which is outside the scan. The guard was not modified.
2. The PARTICIPATION_EVIDENCE_NOT_FOUND template originally contained the phrase "is not enrolled" inside a negation; reworded to "a statement about the dataset, not about the entity's enrollment or participation status" so the banned-claims tests pass without an exception list.
3. Research correction recorded: the CMS PPEF sub-files are published as ancillary resources of the parent dataset (the frozen code documents this, verified live 2026-08-19); data.cms.gov refused all research tools tonight (HTTP 403 for curl, the research fetcher and a browser session), so field names were taken from the frozen code and the DCAT records rather than re-read from CMS PDFs.
