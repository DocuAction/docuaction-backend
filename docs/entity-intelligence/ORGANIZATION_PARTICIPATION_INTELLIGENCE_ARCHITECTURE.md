# Evidence-Based Organization & Participation Intelligence — architecture

The foundation asked "does this address match?". This document records the review of whether the architecture should mature to answer the analyst's real questions, and what changed as a result (isolated, feature OFF).

## The questions, and where each is answered

| Analyst question | Answered by | Status |
|---|---|---|
| Who is this organization? | IDENTIFIER + NAME observations across sources, with authority and provenance | foundation |
| What did ONC/RCE provide? | PROGRAM_DELIVERY observations (subject); the 41 delivered fields are the program's, read through a future adapter | adapter not built (Task freeze) |
| What names are associated with it? | NAME observations with source-stated kinds (legal, DBA, former, other) | foundation + verified NPPES semantics |
| What locations? | LOCATION observations with roles (delivered, primary, additional, mailing, enrollment locality, registered, mobile — source-stated only) | foundation + CMS_ENROLLMENT_LOCATION role reserved |
| What identifiers? | IDENTIFIER observations (NPI; additional NPIs; source ids such as OneKey ID, ENRLMT_ID/PAC ID as link keys; TEFCAID/HCID as delivered) | foundation; CMS designed |
| What relationships are observed? | RELATIONSHIP observations with program-qualified kinds; compared only within one kind | foundation; vocabularies documented below |
| What program/participation relationships? | **PROGRAM_PARTICIPATION** observations, role "<PROGRAM>:<KIND>", compared only within one role; absence with reason | **new (sixth dimension)** |
| What does NPPES show? | NPPES V2 adapter | implemented |
| What does CMS/PECOS public enrollment show? | CMSPPEFAdapter reading preserved snapshots | designed |
| What may RCE-provided IQVIA add? | IQVIA adapter, AWAITING_SCHEMA; the model can receive identity/name/location/relationship/participation observations without being IQVIA-specific (every observation type is generic; kinds are strings; value handling per data rights) | contract stub |
| What did prior reviews show? | DOCUACTION_HISTORICAL observations; PRIOR_HUMAN_DETERMINATION reference (echoed, never read by rules) | implemented |
| What changed? | Deltas scoped DELIVERED_VALUE / EVIDENCE / PROGRAM_ENROLLMENT / RELATIONSHIP / SOURCE_VERSION / RULE_VERSION / PRIOR_HUMAN_DECISION | implemented (RULE_VERSION produced by the integration) |
| Why do values differ? Does evidence explain it? | explainable-variation signals; MULTI-SOURCE NAME VARIATION CORROBORATION with the same-identifier guard | implemented |
| What evidence conflicts? | CONFLICT signals, all visible, no voting | implemented |
| What is unknown? | INSUFFICIENT / NOT_FOUND-with-reason / SOURCE_UNAVAILABLE / AMBIGUOUS, never collapsed | implemented |
| What requires human judgment? | `open_questions`, `requires_human_review = True` always | implemented |
| What rule applied? | policy register, `applicable(rule_id, review_date)` | implemented (citation only) |

## The sixth dimension — decision

**Adopt Participation / Program Identity as the sixth dimension.** Review outcome:

- It is a distinct question. "Is this NPI enrolled in Medicare per the current CMS extract" and "is this entity a TEFCA Participant per the delivery" are neither identity nor location nor relationship-to-another-entity; they are relationships to a program with their own absence semantics.
- It belongs in Core only as a generic type: `ObservationType.PROGRAM_PARTICIPATION` with `role = "<PROGRAM>:<KIND>"` and a comparison that refuses to compare across roles. No TEFCA or Medicare constant exists in Core (tested by enumerating every Core enum).
- Historical identity remains expressed through deltas, not a comparison. The six dimensions are therefore: Organization (identifier), Name, Location, Relationship, Historical (deltas), Participation.

## Relationship vocabularies (never translated into one another)

| Domain | Chain | Kind codes (adapter-defined strings) |
|---|---|---|
| TEFCA (delivered) | QHIN → Participant → Subparticipant; `orgManagingOrg`, `partOf` | `TEFCA:MANAGED_BY_QHIN`, `TEFCA:PART_OF` |
| TEFCA technical (Directory) | Logical Entity (TEFCAID) → Node → Endpoint | `TEFCA:NODE_OF`, `TEFCA:ENDPOINT_OF` (Directory represents Nodes; "might not show all Participants and Subparticipants") |
| CMS | Provider → Enrollment; Enrollment → Practice Location; Individual Enrollment → Reassignment → Receiving Organization Enrollment; Organization → Additional NPI | `MEDICARE:ENROLLED_AS`, `MEDICARE:HAS_PRACTICE_LOCATION`, `MEDICARE:REASSIGNS_BENEFITS_TO`, `MEDICARE:HAS_ADDITIONAL_NPI` (mirrors the frozen `PpefRelationship` hop list) |
| Commercial (future) | Corporate Parent → HCO; HCO → Location | `COMMERCIAL:CORPORATE_PARENT`, `COMMERCIAL:HCO_LOCATION` |

Tested: CMS reassignment ≠ TEFCA relationship; corporate parent ≠ TEFCA parent; NPPES practice location ≠ Participant; enrollment ≠ participation.

## Evidence graph — decision

The model is already a graph in the relational sense: ENTITY (canonical id) — OBSERVATIONS (typed, sourced, dated) — COMPARISONS — DELTAS — ASSESSMENTS — PRIOR DECISIONS, with RELATIONSHIP and PROGRAM_PARTICIPATION observations as typed edges to other entities/programs and provenance on every node. Query patterns are: by entity, by source edition, by review date, by relationship kind — all indexed relational lookups. Traversals are shallow (the deepest is PPEF's eight-hop lineage, already a hop list). **PostgreSQL relational modelling is sufficient; a graph database is out of scope** and no technical requirement for one was demonstrated. Revisit only if the program asks for multi-hop network questions (e.g. "all entities within three ownership hops"), which no task requires.

## Source model additions (this sprint)

`SourceAuthority` now distinguishes ONC/RCE delivered, RCE governing material, federal identity reference, federal program enrollment, federal exclusion/integrity, RCE-provided commercial, commercial supplemental, state registry, DocuAction historical, prior human determination. `AbsenceReason` and `absence()` make "no record" an observation with a reason. `EvidenceApplicability` matches the methodology draft's vocabulary.

## What was deliberately not built

CMS/PECOS production connector · second PPEF downloader · RCE policy engine · Google · LEIE/SAM adapters · state registries · IQVIA mapping · UI · graph database.
