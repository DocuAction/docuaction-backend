# Source Rights Human-Review Matrix: NPPES (NPI Registry, CMS)

**Status:** PENDING_HUMAN_REVIEW. Prepared 2026-09-13 by the release-closure step for a named AGT decision-maker. Nothing in this matrix is authorized; the AI cannot self-authorize source rights. The code record is `app/evidence_sources/nppes_v2/adapter.py` (`DATA_RIGHTS`, status ASSUMED_PUBLIC_DOMAIN, `review_date=None`, `reviewed_by=None`) on the Entity Intelligence branch; it stays unchanged until this matrix is signed.

| Dimension | Current code assumption | Research basis (to be confirmed by the reviewer) | Proposed decision for review | Open question |
|---|---|---|---|---|
| ACQUISITION | Monthly full-replacement file download (`FILE_DOWNLOAD`), no credential | CMS NPPES Data Dissemination files are published for download without registration; the readme (v.2, May 12 2026) describes the file set and the Other Name Reference File | ALLOW file acquisition; no API querying in the current design | Confirm no rate or redistribution notice attaches to the dissemination page at review time |
| PROCESSING | Parse, normalize, compare (identifier, name, location) | Work of the U.S. Government, public domain in the United States | ALLOW | none |
| STORAGE (raw) | `raw_storage_allowed` per record; edition retained for reproducibility (pending) | Public-domain data; internal storage carries no licence restriction | ALLOW raw storage of each acquired edition | Retention period for superseded editions |
| SNAPSHOT_RETENTION | `snapshot_retention_allowed=False` until reviewed | No licence restriction identified; retention is an internal records decision | Reviewer to set ALLOW with a retention rule | Retention length; storage location classification |
| HISTORICAL_COMPARISON | `historical_comparison_allowed=False` until reviewed | Comparing editions is derived use of public data | Reviewer to set ALLOW | Whether historical deltas may be shown to client reviewers or stay internal |
| ATTRIBUTION | `attribution_required=False` | No attribution condition identified for public-domain federal data; citing the edition is a provenance practice, not a licence term | Keep provenance citation (edition date, file name) regardless | none |
| DERIVATIVE_OBSERVATIONS | Observations, comparison signals, assessments derived from records | Derived works of public-domain data are permitted | ALLOW | none |
| REDISTRIBUTION | `redistribution_allowed=False` | Not needed by the product; reports cite observations, not bulk data | Keep FALSE (no bulk redistribution) | none |
| TERMS_SOURCE | Readme v.2 (May 12 2026) and the NPPES dissemination notice | Reviewer confirms the current notice text and date | Record `official_reference` and `reference_version` | Capture the notice text in the repository documentation |
| AUTHORITY | Federal registry; can support NPI observation, organization name, other name/DBA, practice location; cannot alone establish licensure, credentialing, site of care, enrollment | `authority_matrix.MATRIX_V1` (approved_by PENDING_HUMAN_APPROVAL) | Reviewer approves the NPPES rows of the matrix | Matrix approval is a separate signature |
| OPEN_QUESTION | Whether individual-provider (type 1) records are in scope at all | Current adapters process organization records; type 1 rows are skipped | Reviewer confirms scope | Privacy posture for type 1 if ever in scope |

Sign-off block (human only): REVIEWED_BY = ______ ; REVIEWED_AT = ______ ; DECISION = ASSUMED / RESEARCHED / DOCUMENTED / HUMAN_REVIEWED / AUTHORIZED / RESTRICTED.
