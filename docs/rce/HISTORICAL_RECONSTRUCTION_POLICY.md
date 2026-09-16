# Historical Reconstruction Policy — Record-Level Dispositions (2026-09-17)

**Scope.** Deliveries processed before the delivery traceability tables existed
(migration `20260917_delivery_traceability`) have no rows in
`rce_disposition_events`, `rce_delivery_stage_events`, `rce_reconciliation_snapshots`
or `tefca_identifier_decision_events`. This policy governs whether, how and by
whom per-record dispositions may be **derived after the fact** for such a
delivery, and how a derived row is marked so that no report, screen or export
can present it as something the pipeline recorded at processing time.

**Position.** Reconstruction is a *records decision*, not maintenance. It is
never performed by a migration, a scheduler, a startup hook or an unattended
job. It is performed once per delivery, by a named operator, with an approval
reference, after the dry run has been read and its limitations accepted.

---

## 1. Dry run first, always

`scripts/dryrun_reconstruct_dispositions.py` is **read-only by default**:

- the connection is opened with `postgresql_readonly=True` and the transaction
  is `SET TRANSACTION READ ONLY`, so a defect in the tool cannot write;
- it refuses `--write` without `--approved-by <name>` **and** `--approval-ref <ticket>`;
- it refuses `--write` against any non-local host (a shared DEV or PROD server)
  unless `--allow-shared` is also given;
- it refuses `--write` while any record's disposition is `unavailable`, or while
  the intake already has disposition events.

```
python scripts/dryrun_reconstruct_dispositions.py --intake-id <uuid> [--job-id <uuid>] [--summary-only] [--json out.json]
```

The output is JSON: intake and job identity, the job window, one proposal per
received record, totals per disposition, the accounting equation, a
classification summary per field, and the limitation list. Read all of it.
The equation must close (`holds: true`) before a write can be considered.

## 2. Classification of every proposed field

Each proposed value is labelled with **how it was obtained**:

| Classification | Meaning | Examples |
|---|---|---|
| `directly_evidenced` | a persisted column states the fact | `rce_source_records.parse_status != ok` → REJECTED; `rce_curated_records.record_status = HELD` → HELD; `is_test_record` → EXCLUDED; `canonical_entity_id` → entity id |
| `deterministically_recomputed` | a fixed rule over persisted values | no `rce_org_oid` or no `name` → MISSING_KEY; `tefca_reg_entities.created_at` inside the job window → CREATED; reason codes implied by a directly evidenced disposition |
| `inferred` | the best-supported reading of indirect evidence | a `tefca_entity_versions` row inside the window → UPDATED (else MATCHED_UNCHANGED); the HELD reason code read from free-text `status_reason`; `changed_fields` from a version's snapshot keys; CREATED from promotion time when no job window exists |
| `unavailable` | nothing persisted supports a value | the free-text `reason`; `changed_fields` when no version exists; a record with no curated row; a promoted record whose entity no longer exists |

Confidence follows the classification of the disposition itself: `high` for
directly evidenced or deterministic, `medium` or `low` for inferred, `none` for
unavailable. Confidence is stored with the row; it is never rounded up.

Method version: `METHOD_VERSION = "1.0.0"` in the script. A change to any rule
above is a new method version, stated in this document.

## 3. What is never reconstructed

- **Reason text.** It was not persisted. Reconstructed rows carry `reason = NULL`
  and `reason_code` only.
- **Identifier conflicts** raised before the decision ledger existed. A record
  that was HELD for a conflict is reconstructed as `HELD_IDENTIFIER_CONFLICT`
  only when `status_reason` says so; the conflict event itself is not invented.
- **Stage timings.** No stage event is reconstructed. The job row's
  `started_at` / `completed_at` remain the only timing evidence and the report
  says so under *Evidence limitations*.
- **Analyst decisions** that were not recorded. The tool writes SYSTEM-derived
  dispositions attributed to the approving operator; it never attributes a
  decision to an analyst who did not record one.

## 4. Approval

A write requires, in the ticket named by `--approval-ref`:

1. the dry-run JSON (or its path) attached, with the limitation list;
2. the delivery identity: intake id, job id, filename, SHA-256, record count;
3. the equation as proposed, closing exactly;
4. the count of `inferred` dispositions and the operator's acceptance of them;
5. the approver's name and role (`--approved-by`), who must not be the person
   who processed the original delivery when that person is known;
6. for DEV or PROD: the explicit decision to pass `--allow-shared`, recorded as
   an operator step. Automation never passes it.

## 5. Marking of written rows

Every row written by the tool is distinguishable forever:

- `rce_disposition_events.reconstructed = true`;
- `rce_disposition_events.reconstruction` holds
  `{method_version, source_evidence, operator, approval_ref, confidence,
  reconstructed_at, field_classification}`;
- `actor_type = HUMAN`, `actor = <approved-by>`: a person took the decision,
  not the pipeline;
- one `rce_reconciliation_snapshots` row with `trigger = RECONSTRUCTION` and
  `reconstructed = true`, whose `passed` reflects the equation as written and
  whose `source_evidence` carries the method, operator, approval and the
  classification summary.

The Delivery Processing Report prints *(reconstructed)* beside every such
disposition, counts them in the dispositions section, and adds a limitation
whenever the pinned snapshot is marked reconstructed. The API's `availability`
vocabulary exposes `reconstructed` for the same reason.

## 6. What the tool will not do

- **UPDATE or DELETE** anything. The evidence tables are append-only by grant
  and the tool contains no such statement (pinned by `tests/test_dryrun_reconstruction.py`).
- Write when the equation does not close, or when the ledger is not empty.
- Write a snapshot without a job (there is nothing to sequence against).
- Run under `--write` in any automated session. The remediation of 2026-09-17
  did not execute a write anywhere.

## 7. Verification after a write

1. Regenerate the Delivery Processing Report for the job with no `snapshot_id`;
   confirm the pinned snapshot has `trigger = RECONSTRUCTION`, the dispositions
   section shows `N reconstructed`, and the limitations name the reconstruction.
2. Confirm `GET /api/tefca/rce/delivery-jobs/{id}/detail` reports
   `availability.records = "reconstructed"`.
3. Attach the report id and the `rce_delivery_report_links` row to the ticket.
