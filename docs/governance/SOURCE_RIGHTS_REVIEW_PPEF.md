# Source Rights Human-Review Matrix: CMS Provider Enrollment (PPEF) Public Files

**Status:** PENDING_HUMAN_REVIEW. Prepared 2026-09-13 by the release-closure step for a named AGT decision-maker. Nothing here is authorized; the AI cannot self-authorize source rights. PPEF is NOT authorized for Entity Intelligence integration (Gate H remains closed). This matrix concerns rights only; it does not open Gate H. The frozen Task 3 pipeline already ingests PPEF snapshots under the existing methodology; that use is outside this matrix.

| Dimension | Current assumption | Research basis (to be confirmed by the reviewer) | Proposed decision for review | Open question |
|---|---|---|---|---|
| ACQUISITION | Quarterly public files (base enrollment, practice-location sub-file, reassignment, secondary specialty; monthly hospital enrollments; FQHC/RHC/hospice files) via data.cms.gov | CMS publishes these as public use files; automated retrieval from data.cms.gov is blocked for scripted clients (observed HTTP 403) so acquisition is by manual download or the existing Task 3 snapshot process | ALLOW acquisition through the existing snapshot path only; no new connector | Confirm the data.cms.gov terms of use text at review time |
| PROCESSING | Enrollment-level records keyed by NPI/enrollment id; practice-location file carries no street line | CMS public file documentation | ALLOW | Whether enrollment-level (several rows per NPI) records may be summarized per entity |
| STORAGE (raw) | Snapshots retained in `tefca_ppef_snapshots` by Task 3 | Public use files; no licence restriction identified | ALLOW (already practised by Task 3) | Retention length of superseded quarters |
| SNAPSHOT_RETENTION | Existing snapshots retained | Same | Reviewer to confirm and set a rule | Storage classification |
| HISTORICAL_COMPARISON | Not yet performed by Entity Intelligence | Derived use of public data | Reviewer decision; default NOT until Gate H | Client visibility of enrollment history |
| ATTRIBUTION | None required identified | Public federal data | Cite file name and release date in provenance | none |
| DERIVATIVE_OBSERVATIONS | Program-participation observations (sixth dimension) would be derived | Derived works permitted for public data | ALLOW in principle; execution gated by Gate H | none |
| REDISTRIBUTION | Not required | Reports cite observations only | Keep FALSE | none |
| TERMS_SOURCE | data.cms.gov data use notice and the file documentation | Reviewer records the notice text and date | Record `official_reference` and `reference_version` | Terms retrieval is manual (automation blocked) |
| AUTHORITY | Can support Medicare enrollment observation, organization name, benefit reassignment, additional NPI, practice location; cannot alone establish TEFCA eligibility, licensure, credentialing, site of care, DBA | `authority_matrix.MATRIX_V1` CMS_PPEF rows (approved_by PENDING_HUMAN_APPROVAL) | Reviewer approves the CMS_PPEF rows | Separate signature from NPPES |
| OPEN_QUESTION | Whether Entity Intelligence may read the Task 3 snapshot tables at all | Architecture proposes an injected read-only port over existing snapshots | Human decision at Gate H, after AUD-09/10/11 fixes (PR #60) | Read-only guarantee and audit logging of reads |

Sign-off block (human only): REVIEWED_BY = ______ ; REVIEWED_AT = ______ ; DECISION = ASSUMED / RESEARCHED / DOCUMENTED / HUMAN_REVIEWED / AUTHORIZED / RESTRICTED. GATE_H_AUTHORIZED_BY_IMRAN = PENDING (unchanged by this matrix).
