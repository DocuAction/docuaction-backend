# Runbook — rolling back a September-model delivery (schema, image, data)

**Applies to:** backend builds carrying migration `20260921_september_snapshot`
(rule set 1.3.0). **Scope:** DEV in this phase. Nothing here is executed
against DEV or PROD without an explicit, recorded approval.

Three different rollbacks exist. They are not interchangeable, and none of
them alone is "the rollback".

| | What it does | What it does NOT do |
|---|---|---|
| **1. Application / image rollback** — redeploy backend `804b8fe9` (frontend `3024b42c`) | Stops the new code from running. | Leaves every row the new code already wrote: ended edges (`end_date`/`status = historical`), replacement edges, delta/presence/stale-mark/snapshot rows. The registry state is NOT restored. |
| **2. Alembic schema downgrade** — `alembic downgrade 20260918_pp_verification` | Drops the seven 1.3.0 tables and the view. | **Refuses** while any of them holds a row (`SnapshotPreconditionError`) — evidence is not un-recorded. Never touches `tefca_entity_relationships`. Not a data rollback. |
| **3. Data compensation** — `scripts/rce_snapshot_rollback.py` | Reverses the relationship changes ONE snapshot applied, from its own observation evidence, append-only and idempotent. | Does not delete source records, delta/presence rows, stale marks, observations or the snapshot chain (a `ROLLED_BACK` row is appended). |

A complete reversal of a processed delivery is **3 → (optionally 1)**. Step 2
is only for an environment where the tables are still empty.

## Preconditions (fail-closed; the tool checks each and refuses otherwise)

1. No **later APPROVED** snapshot exists (by `received_at`). If one does, roll
   that one back first or leave this one in place.
2. Every ASSERTED observation of the snapshot names its edge; every SUPERSEDED
   edge has an ASSERTED replacement; both edges still exist and match the
   observation's child/type (scope).
3. No superseded edge was also superseded by a different snapshot (ambiguity).
4. Restoring a prior edge would not leave two active parents for the same
   (child, relationship type).
5. Operator role is Data Operations (`program_manager`) or above; a reason is
   given.

## Procedure (DEV; requires the DEV firewall handshake, issue #77)

```
# 0. identify the delivery
GET /api/tefca/rce/deliveries/{intake}/snapshot        # chain, tip status, gates

# 1. plan — read-only, prints every step and any refusal
DATABASE_URL=<dev app url> python scripts/rce_snapshot_rollback.py --intake <uuid> --plan

# 2. apply — only after the plan is reviewed and approved
DATABASE_URL=<dev app url> python scripts/rce_snapshot_rollback.py --intake <uuid> --apply \
    --actor <operator email> --role program_manager --reason "<ticket / why>"

# 3. verify
GET /api/tefca/rce/entities/{child}/relationship-history   # restored edge current, replacement historical (rolled_back)
GET /api/tefca/rce/deliveries/{intake}/snapshot            # tip = ROLLED_BACK
GET /api/tefca/rce/deliveries/{intake}/audit               # relationship_snapshot_rolled_back row

# 4. repeat step 2 is safe: it reports retired=0 restored=0 (idempotent)
```

What the apply writes (all append-only except the two granted columns):

* `tefca_entity_relationships`: replacement edge `end_date = today`,
  `status = rolled_back`; prior edge `end_date = NULL`, `status = active`.
  (The app role holds `UPDATE (end_date, status)` only.)
* `tefca_relationship_observations`: `ROLLED_BACK` per replacement edge,
  `RESTORED` per prior edge — actor, reason, intake, boundary, build SHA,
  correlation id.
* `source_snapshot`: a `ROLLED_BACK` row superseding the chain tip.
* `tefca_reg_audit_log`: `relationship_snapshot_rolled_back` with actor,
  role, reason, counts, build SHA, correlation id.

After a rollback the delivery's snapshot is never current and cannot be
approved (decisions are append-only); a corrected file is registered as a
NEW delivery.

## Snapshot governance (context)

A delivery's snapshot chain: `FAILED` (effects incomplete; retry via
`POST /deliveries/{intake}/snapshot-effects/retry`) → `PENDING` (effects
complete; the system never approves) → `APPROVED` (human, QA lead or above,
not the registrant, only after the persisted reconciliation snapshot PASSED
with a hash) → `ROLLED_BACK`. Current views (stale marks, `arc_current_stale`,
relationship `current`) select only APPROVED snapshots; a pending
snapshot's edges appear as `staged` and the edge it ended stays current with
`pending_supersession_by`.
