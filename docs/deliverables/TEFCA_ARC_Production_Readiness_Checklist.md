# TEFCA ARC — Production Readiness Checklist

**Classification:** INTERNAL ENGINEERING / OPERATIONS · Version 1.0 · 2026-08-23
Assessed at commit `4bcf74f` + Phase 8/9 · Evidence `phase6-bulk-1.1.0`

| Label | Meaning |
| --- | --- |
| **READY** | Verified in this environment |
| **EXTERNAL ACTION** | Requires someone outside engineering |
| **PENDING** | Engineering work or a decision is outstanding |
| **N/A** | Not applicable to this deployment |

---

## Infrastructure

| # | Item | Status | Note |
| --- | --- | --- | --- |
| 1 | Azure App Service defined | **READY** | `infra/modules/appService.bicep` |
| 2 | Container image with rendering libraries | **READY** | WeasyPrint native stack present in the Linux image, absent on Windows workstations |
| 3 | Disk for retained source artefacts | **EXTERNAL ACTION** | ~1.7 GB per cycle; sizing depends on retention (D8) |

## Database

| # | Item | Status | Note |
| --- | --- | --- | --- |
| 4 | Single Alembic head | **READY** | `20260828_area1_grants`, head count 1 |
| 5 | Database at head | **READY** | |
| 6 | Migration chain tests | **READY** | 70/70 |
| 7 | No unexplained drift | **READY** | 87 public tables; Phase 8/9 introduced none |
| 8 | Program boundary respected | **READY** | No unrelated product domains pulled in |
| 9 | **`docuaction_owner` ownership transfer** | **EXTERNAL ACTION** | See below — the one production blocker |

## Area-1 controls

| # | Item | Status | Note |
| --- | --- | --- | --- |
| 10 | Source artefact read-only on disk | **READY** | Append refused |
| 11 | `rce_source_records` not updatable by the app role | **READY** | Verified: `permission denied` at the database |
| 12 | Artefact hash re-verifies | **READY** | `689472073480b1cc…` |
| 13 | Content digest stable | **READY** | `24524f70c370d6c42a2b03d5385295a5` |

## Secrets and credentials

| # | Item | Status | Note |
| --- | --- | --- | --- |
| 14 | Deployed secrets from Key Vault | **READY** | `@Microsoft.KeyVault(...SecretName=SECRET-KEY)` via managed identity |
| 15 | Local developer `.env` SECRET_KEY ≥64 chars | **PENDING** | Local only — 42 chars, blocks `alembic current` on a workstation. `.env` is gitignored and untracked. **Not a deployed defect** |
| 16 | SAM.gov API credential | **EXTERNAL ACTION** | Absent. 23,566 records `SOURCE_UNAVAILABLE` |
| 17 | USPS Address API credential | **EXTERNAL ACTION** | Configured, never exercised |

## Access control

| # | Item | Status | Note |
| --- | --- | --- | --- |
| 18 | RBAC ladder | **READY** | reviewer / qalead / program_manager / admin |
| 19 | Analyst–QA segregation | **READY** | Refused in code; exception needs a senior grant and reason |
| 20 | Append-only decision events | **READY** | No override column, no MODIFY action |
| 21 | Audit logging | **READY** | 23,812 rows |

## Operations

| # | Item | Status | Note |
| --- | --- | --- | --- |
| 22 | Monitoring | **PENDING** | Scripts exist; alert routing not confirmed for this programme |
| 23 | Backups | **EXTERNAL ACTION** | `docs/BACKUP_RESTORE_PROCEDURE.md`; not exercised against 706 MB |
| 24 | Recovery tested | **PENDING** | Procedure documented, restore not rehearsed |
| 25 | Source connectivity | **READY** | NPPES, CMS, OIG reachable and retained |
| 26 | Report rendering — HTML / structured | **READY** | |
| 27 | Report rendering — PDF | **PENDING** | Container-only; must be pinned there if PDF is the format of record (D9) |
| 28 | Section 508 automated checks | **READY** | Contrast, alt text, table headers, heading hierarchy, lang |

## Programme

| # | Item | Status | Note |
| --- | --- | --- | --- |
| 29 | Analyst staffing | **EXTERNAL ACTION** | 28 items are analyst-ready; no analyst has yet acted |
| 30 | QA staffing | **EXTERNAL ACTION** | 0 QA decisions recorded |
| 31 | Methodology approved by COR | **EXTERNAL ACTION** | Draft issued |
| 32 | D1–D9 + D4_ADDRESS_MATERIALITY decided | **EXTERNAL ACTION** | All PENDING |
| 33 | Dataset contractual provenance documented | **EXTERNAL ACTION** | **Gate 4 — the release blocker** |

---

## The production blocker: `docuaction_owner`

**Status: EXTERNAL ACTION — not performed, and deliberately not performed here.**

Area-1 tables are currently owned by the application role. The approved design
transfers ownership to a non-login role `docuaction_owner`, leaving the
application role with only the privileges it needs.

**Required production procedure** (to be run by a DBA with sufficient
privilege, in a maintenance window):

1. Confirm `docuaction_owner` exists as a **NOLOGIN** role.
2. Take a verified backup and confirm it restores.
3. In one transaction, `ALTER TABLE … OWNER TO docuaction_owner` for each Area-1
   table, then grant the application role `SELECT` plus the narrow column-level
   `UPDATE` the FK lock requires.
4. **Validation, before committing:** insert a row into each child table that
   holds a foreign key into an Area-1 table. The referential-integrity check runs
   as the owner of the referenced table, and this exact step failed during
   development — an ordinary INSERT broke because the app role lost the implicit
   `FOR KEY SHARE` privilege. If the insert fails, roll back.
5. Re-run reconciliation (expect 18/18) and confirm the Area-1 content digest is
   unchanged.
6. Confirm the application still starts and can read Area 1.

**Do not perform this transfer without step 4.** It is the step that catches the
failure mode.
