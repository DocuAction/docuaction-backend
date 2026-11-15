# Proposal: recovery and approval control for IQVIA snapshots staged by an earlier reader

**DRAFT / NOT FINAL / INDEPENDENT QA PENDING. A proposal. Every control here is pending owner confirmation, none is active, and nothing in it is applied to a live snapshot.**

Related: #216 (the importer now reads a leading byte-order mark the way the preflight does). This document concerns snapshots that an EARLIER reader staged. It records no data from any environment; assessment results are kept in the controlled evidence folder, not in this repository.

## 1. What the earlier reader did (corrected statement)

The old UTF-8 reader could retain a leading BOM in the first column's name. If that column was required for identity, rows could be rejected. If identity columns were elsewhere, rows could stage while the first field was mis-keyed, omitted from a projection, or unavailable to downstream logic. Actual impact requires assessment.

A synthetic test that reads 5 rows and rejects 5 establishes that test case only. It says nothing about a real snapshot. No statement of the form "none affected", "all rejected" or "fully correct" is made about real snapshots without evidence.

## 2. How a snapshot is assessed (built, read-only)

Three separate questions, three separate answers (`iqvia_reader_assessment.py`):

| Dimension | Values | Meaning |
|---|---|---|
| Staging class | **A** confirmed all-rejected; **B** confirmed partially staged; **C** confirmed fully staged, correctness NOT established; **U** insufficient evidence | what the observation table holds |
| Mark exposure | CONFIRMED / NOT_FOUND / UNKNOWN | whether the reader retained a byte-order mark |
| Approval, downstream use | recorded as their own flags | a class C snapshot can also be approved and used |

Rules that keep the answer honest:

- Completion is evidenced by the importer's own record (`import_status` completed AND a recorded rejected count), never by the declared record count, which is provisional until an import finishes.
- Missing evidence is `U` or `UNKNOWN`, never "safe". Missing preflight metadata is not evidence a file had no mark.
- A negative result needs a check that could see the header: the recorded preflight (which has reported the mark as REF-SCH-010 since its first version), a staged row's key names for sources that store the full row, or the preserved file's first bytes.
- Absence of downstream evidence is not evidence of no use: references held in JSON, reports or files are invisible to a foreign-key check.

Enforced read-only operation (`scripts/dev_readonly_diagnostics/iqvia_bom_assessment.py`): an inspector that lets only a single SELECT over an allowed function list reach the server; a SELECT-only database role; a read-only transaction verified before every query. Output is screened: fixed keys, no protected-identifier shape. If read-only cannot be established the script does not run and the SELECT package is delivered instead.

## 3. Recovery options

| Option | What it does | Status |
|---|---|---|
| **Retry of identical bytes** | A replacement snapshot is registered from a file whose hash equals the original's, linked to the original through its own metadata (`recovery_of`). The original row, its staged rows, its file and its hash are not touched. | Built as a draft, default OFF in the sense that no route or job calls it. |
| **Cancel** | Record a REJECTED decision row on the original (the status exists; nothing writes it today). The snapshot can then never be approved. | NOT built. Needs a writer and an owner decision. |
| **Corrected file** | Different bytes are a new upload, not a recovery. It registers normally and may cite the original in its label. | Existing path, unchanged. |
| **Supersede by decision** | `supersedes_snapshot_id` means "a decision row" (approval). It is NOT used for recovery: consumers walk it back to the registration row to find staged rows. | Rejected as a design. |

The duplicate rule is NOT weakened globally. `_register_and_enqueue` still refuses identical bytes as a duplicate. Only `register_recovery_snapshot`, holding a valid one-time authorization for that exact original, may register identical bytes, and it does so under a label that differs from the original's (the database index is on source, hash and label).

## 4. Who does what

| Step | Who | Control |
|---|---|---|
| Request | reviewer or above | names the original and the reason |
| Authorize | QA lead or above, **not the requester** | cites the owner's written reference; names the snapshot id and the exact permitted action; expiry (proposal default 72 hours); append-only |
| Execute | the named requester, or a QA lead | file hash must equal the original's; original must still have no decision and no replacement; one use only |
| Approve the replacement | a QA lead who is not its registrant (existing maker/checker) | with the approval protection below |

Authorization wording (the owner supplies the reference):

> AUTHORIZED: recovery action RETRY_IDENTICAL_BYTES on snapshot `<full snapshot id>` only. Register one linked replacement from the preserved original whose sha256 equals the original's. No approval, no deletion, no other snapshot. Expires `<time>`.

## 5. Audit evidence, rollback and withdrawal

- Append-only audit rows (`audit_logs`): `IQVIA_RECOVERY_AUTHORIZED`, `IQVIA_RECOVERY_REGISTERED`, `IQVIA_RECOVERY_WITHDRAWN`, `IQVIA_BOM_ASSESSMENT_RECORDED`. The replacement carries `recovery_of` and `recovery_authorization_id`.
- Withdrawal: an unused authorization is withdrawn by an appended row and stops being valid. A used authorization stays on record as used.
- Rollback: nothing is deleted. A replacement is PENDING until approved and nothing reads a PENDING snapshot, so abandoning it needs no change to data. **Gap, stated:** while the protection is on, an original that has a replacement can never be approved, even if the replacement is abandoned. Whether to add an explicit "recovery abandoned" step is an owner decision.

## 6. Approval protection (built, default OFF, in the shared service)

`approve_snapshot` calls `enforce_assessment_before_approval` when `IQVIA_BOM_APPROVAL_GUARD` is set. It sits in the service, so a direct call cannot bypass the route.

| Current evidence | Result |
|---|---|
| a recovery replacement exists | refused: approve the replacement |
| class A or B | refused: needs a governed recovery, not an approval |
| class U | refused: unresolved is not safe |
| class C, mark CONFIRMED or UNKNOWN | needs a recorded assessment: accepted, matching the CURRENT evidence, by someone other than the approver |
| class C, mark NOT_FOUND by a check that could see the header | no extra requirement |

Fully staged does not establish correctly staged, which is why class C with a confirmed or unknown mark still needs the assessment. A recorded assessment is compared with the evidence at approval time, so a changed state invalidates it. The route's existing completeness check is unchanged and still runs first.

**Already-approved snapshots** are not changed by any of this. Their status is never edited. Where a mark is confirmed for an approved snapshot, an impact assessment is required first (what read it: matches, advisory comparison, assessments, reports), then an owner decision. Because decisions are append-only, the way forward would be a later approved replacement; the earlier row stays on record.

## 7. What this does not do

- No migration. Assessment records live in the snapshot's metadata and the audit trail. A first-class table would need its own plan and authorization.
- No route, no job, no UI.
- It does not fix mis-keyed rows in place. It never rewrites or deletes staged rows.
- Until #216 is integrated and deployed, an environment still runs the old reader, so a marked file registered there can still stage badly and the preflight only reports the mark as informational. Interim compensating control, with no deployment: run the read-only assessment, or read the recorded preflight for REF-SCH-010, before approving any IQVIA snapshot.

## 8. Owner decisions needed

1. Whether to turn the approval protection on, where and when (it is off).
2. Whether class A and B approval should be refused at the service level for every IQVIA snapshot (as built) or only for marked files.
3. The authorization default of 72 hours, and who may authorize and execute.
4. Whether to build Cancel (a REJECTED decision writer).
5. Whether an original with an abandoned replacement stays unapprovable or gets an explicit recovery-abandoned step.
6. The impact-assessment path for approved snapshots with a confirmed mark.
7. Whether assessments and authorizations stay in metadata and audit rows (as built) or move to a table, which needs a migration plan.
8. Whether, until #216 deploys, a recorded REF-SCH-010 should block approval instead of being informational (this would reverse the 2026-10-04 decision).

DRAFT / NOT FINAL / INDEPENDENT QA PENDING.
