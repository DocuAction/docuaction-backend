# Audit Trail Assessment

> Completeness, immutability, content, and tamper-evidence of the audit trail. Static review of `app/services/audit.py`, `app/models/database.py`, `app/tefca_registry/models.py`. Read-only. Cross-references Part 8 (AUDIT-MUT, AUDIT-READ).

## 1. Is every PHI access logged (read AND write)? — **Gap**
- **Logged:** AI requests (`audit_logger.py:14`), TEFCA entity mutations/verification/import (`audit.py`, `verification.py:362-383`, `fhir_import.py:116-230`), **403 authorization denials** (`error_handler.py:103-125`), auth events.
- **NOT logged:** GET **reads** of documents, transcripts, or registry entities (`fhir_resource`) — no audit call on `routes.py` registry GETs or document GETs.
- **Impact:** fails the HIPAA §164.312(b) intent that PHI *access* (including views) be auditable. **Gap.**

## 2. Audit record immutability / append-only — **Gap**
- **By design append-only:** `tefca_reg_audit_log` docstring (`models.py:318-324`) states append-only, "enforced at the application layer (no UPDATE/DELETE code paths)". No UPDATE/DELETE paths exist against **that** table.
- **BUT the canonical `audit_logs` table is mutable in practice:**
  - `api/compliance.py:129-134` (`/hard-delete`) **deletes** `audit_logs` rows for a user (`await db.delete(log_entry)`).
  - `api/admin_users.py:433` **UPDATEs** `audit_logs` to null out `user_id` on user delete.
  - No DB triggers / append-only constraint / WORM enforcement.
- **Impact:** audit immutability is convention-only and is **actively violated** by admin/compliance flows. Fails §164.312(c)(1) integrity and **NIST AU-9 (protection of audit information).** **Gap.**

## 3. who / what / when / where / outcome — **Compliant (schema)**
`AuditLog` (`models/database.py:88-98`) captures all five:
| Dimension | Field |
|---|---|
| **who** | `user_id` (+ `actor_email` on registry log, `models.py:337`) |
| **what** | `action` + `resource_type` + `resource_id` |
| **when** | `created_at` |
| **where** | `ip_address` |
| **outcome** | `details` JSON incl. `result` |

The writer enforces all fields (`audit.py:52-72`). **Schema is complete.** ✅ (Missing only `user_agent`, a minor addition.)

## 4. Evidence hashing (SHA-256) + tamper detection — **Partial**
- **Present:** SHA-256 for evidence payloads (`connectors.py:164-171` `hash_payload`), evidence-record citations carry `response_hash` (`validation_engine.py:677`), and an `evidence_hash` column exists (`models.py:237`).
- **Gaps:** the internal registry verification writes `evidence_hash=None` (`verification.py:336,346`) — column present, **unpopulated**. **No hash-chaining** of audit records and **no tamper-detection routine** (nothing recomputes and compares stored hashes). Hashing supports **reproducibility**, not active **tamper detection**. **Partial.**

## Audit trail verdict
The audit **schema is complete** (all five W's, append-only registry log) and write/auth/denial coverage is good — but three real gaps undermine it for a healthcare/federal posture: **(1) PHI reads are not logged**, **(2) the canonical `audit_logs` is deleted/updated by admin flows** (immutability violated), and **(3) `evidence_hash` is unpopulated with no hash-chain/tamper detection.** These are the audit-integrity blockers.

## Remediation
| # | Fix | Closes | Effort |
|---|---|---|---|
| 1 | Log PHI **read** access (who/what/when) on document + registry GETs | §164.312(b) / AU-12 | 2–3d |
| 2 | Make `audit_logs` **append-only** — remove the delete/update paths (anonymize via a tombstone row, not row mutation); enforce with a DB trigger/role | §164.312(c) / AU-9 | 2–3d |
| 3 | Populate `evidence_hash`; add a **hash-chain** (each record hashes the prior) + a verify routine | integrity / AU-9(3) | 3–4d |
| 4 | Add `user_agent` to the audit schema | completeness | 0.5d |

## Status
| Item | Status |
|---|---|
| PHI read + write logged | ❌ Gap (reads unlogged) |
| Immutability / append-only | ❌ Gap (audit_logs deleted/updated) |
| who/what/when/where/outcome | ✅ Compliant (schema) |
| SHA-256 + tamper detection | ◐ Partial (evidence_hash unset, no chain) |
