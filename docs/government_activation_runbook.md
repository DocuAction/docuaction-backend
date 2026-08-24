# Government Activation Runbook

**Classification:** INTERNAL ENGINEERING — PRODUCTION SENSITIVE · 2026-08-24
**Contract:** 7571MN26F80064 · TEFCA ARC

> **NO STEP IN THIS DOCUMENT HAS BEEN EXECUTED.** No Government data has been
> imported into any environment. At the time of writing, `rce_source_intakes`
> holds exactly one intake, it is development data, and
> `scripts/production_state_gate.py` reports **government import performed:
> False**.

---

## What this runbook is

The procedure between *"the COR has accepted the methodology"* and *"the system
holds Government data"*. That gap has never been written down. The activation
package tells the Government what AGT proposes; the readiness register tells AGT
what is unfinished. Neither tells an operator what to actually do on the day, in
what order, and — more importantly — **when to stop.**

This is written as a gate, not as an encouragement. Most of it is conditions
that must hold before anything happens. If you are looking for the shortest path
to loading a file, it is not in here, and its absence is deliberate.

## What this runbook is not

It is **not** authorisation. Nothing here grants permission to import Government
data. Authorisation is a contractual act by the Government, recorded outside
this repository. This document assumes it has already happened and tells you
what to do next.

---

## Stage 0 — the stop conditions

**Do not proceed past this stage while any row is open.** These are not
best-practice suggestions; they are the pre-production blockers from
`internal/production_readiness_register.md`, and each one is a reason a
Government dataset would be handled worse than it should be.

| # | Blocker | Why it stops activation | Verify with |
| --- | --- | --- | --- |
| 1 | `docuaction_owner` ownership transfer | Area-1 immutability rests on privileges the application role can grant back to itself. Until ownership moves, "immutable" is procedural, not structural. | `docs/production_owner_role_runbook.md` |
| 2 | Azure artifact storage | Finalised deliverables would live only on a container's ephemeral disk. | `az storage account list -g rg-docuaction-prod` |
| 9 | Backup **and restore** rehearsal | An untested restore is a hypothesis. Blocker 1 cannot even begin without it — it is that runbook's own first precondition. | Recovery time recorded, not estimated |
| 10 | Monitoring and alert routing | A failure nobody is paged about is a failure discovered by the Government. | One test alert, received and acknowledged by a named recipient |
| 12 | FIPS-199 categorisation memo | Required by the security clauses. | Drafted and accepted |
| 13 | NIST 800-53 control assessment | An SSP without an assessment against it is a plan, not a control set. | Assessment at the agreed impact level |
| 14 | CUI marking and handling | Government entity data is handled, marked, transported and disposed of under E.O. 13556. | Procedure published; marking applied to deliverables |
| 15 | One-hour incident reporting path | The contract requires reporting a discovered threat within one hour. A path that has never been tested is not a path. | Named recipients; one tested escalation |
| 19 | PTA, and PIA if triggered | Privacy analysis precedes the data, not the other way round. | Drafted and accepted |

**Additionally, from the 2026-08-24 verification
(`production_readiness_verification_2026-08-24.md`):**

| Blocker | Why it stops activation |
| --- | --- |
| **Key Vault is unreachable in DEV** | Every secret is currently an inline plaintext app setting. Confirm production does not share this defect before a Government credential is placed anywhere. |
| **`SAM_GOV_API_KEY` provenance unresolved** | A populated credential contradicts the SAM.gov source limitation disclosed on every report. Resolve it before any report is issued, or the disclosure is false. |
| **Linux PDF never executed** | If PDF is the format of record (**D9**), it must be proven in the image first. |

### Government decisions that must be closed

| Decision | Blocks |
| --- | --- |
| **D8** — retention period, and whether WORM applies | Artifact storage configuration. A WORM lock cannot be undone; it is not applied speculatively. |
| **D9** — deliverable format | Whether PDF is on the critical path at all |
| **D4** — SAM.gov unavailability treatment | Whether a SAM.gov gap affects classification or only readiness |
| **HHS Data Access Agreement** finalisation | The delivery of entity data itself |

---

## Stage 1 — confirm the target environment is empty and honest

Run **before** any transfer, on the target environment, and keep the output.

```bash
python scripts/production_state_gate.py
```

Required result — a clean production deployment reports:

| Field | Required value |
| --- | --- |
| environment classification | `production` |
| government dataset status | `NOT_LOADED` |
| data identity | `NONE` |
| report classification | `NO_DATASET_LOADED` |
| development/mock warning | `False` |
| operational findings available | `False` |
| government import performed | `False` |

**If the label contains `MOCK`, `DEMONSTRATION` or `SYNTHETIC`, stop.** An empty
production system is empty, not fake, and a system that cannot tell the
difference will mislabel the Government's data once it arrives. That distinction
is the whole subject of commit `caa31c1`; do not proceed past a gate failure by
reasoning that the warning is cosmetic.

Also confirm the schema is at head:

```bash
alembic heads      # expect exactly one head
alembic current    # must equal that head
```

Re-derive head from `alembic heads`. **Do not compare against a revision id
transcribed into a document** — that is precisely how the `docuaction_owner`
runbook's precondition went stale between 2026-08-23 and 2026-08-24.

---

## Stage 2 — receive the delivery

**Before the file is placed on any system:**

1. Record the **transmittal**: who sent it, under what contract instrument, on
   what date, by what channel. This is the piece
   `post_certification_operational_readiness.md` correctly identifies as absent
   for the development artefact — user recognition of a file establishes which
   dataset was used, **not** a contractual chain of custody. Do not repeat that
   gap for the real delivery.
2. Record the **control total** the sender states: expected record count, and a
   hash if one was provided.
3. Compute the SHA-256 of the received bytes **before anything reads or
   converts them.**

> **Never accept a spreadsheet round-trip.** The development delivery has an
> Excel-derived copy with the same 23,566 records but comma-padded fields and
> 330 altered `address_text` values. It looks identical and is not. If what
> arrives has been opened and re-saved in Excel, request the original.

4. Mark and handle the file as CUI from the moment it is received.

---

## Stage 3 — the only sanctioned import path

Government state is **never inferred**. It is not inferred from the filename,
from `source_metadata.origin`, from the record count, or from the presence of a
credential — a credential says a source is reachable, not which dataset arrived.
The determination in `app/Tefca/data_state.py` reads none of those fields for
that purpose, and a test asserts against the function's own source that it does
not read a credential.

Reaching `GOVERNMENT` state requires **all** of the following, carried by a
controlled intake:

| Requirement | Meaning |
| --- | --- |
| Intake id | A real intake record, not a configuration flag |
| SHA-256 | Of the bytes actually stored |
| Successful parse | `parse_status` clean |
| Positive record count | Non-zero |
| Schema fingerprint | The locked 41-column header hash |
| Receipt timestamp | When it arrived, and from whom |
| **Explicit authorisation marker** | Set **only** by the authorised import path |

The explicit marker is the one that matters most. Anything that inferred
Government status from origin text would classify the existing development
artefact — whose `source_metadata.origin` reads `"ONC/RCE delivery"` — as
Government. It is a copy of a real ONC snapshot used for development, and under
production configuration it is still correctly reported `MOCK_TEST` /
`NOT_LOADED`.

**Do not set the authorisation marker by hand, by SQL, or by configuration.** If
you find yourself writing an `UPDATE` to make the system consider data
Governmental, stop: you are defeating the control, and the resulting state will
be indistinguishable in the database from a legitimate one.

Run the import through the authorised path only. If it refuses, the refusal is
information — investigate it, do not work around it.

---

## Stage 4 — verify after intake, before anyone relies on it

1. **Hash equality.** Stored artefact SHA-256 equals the SHA-256 computed at
   receipt in Stage 2.
2. **Control total.** Record count equals what the sender stated. A discrepancy
   is a delivery problem to be raised with the sender, **never** silently
   accepted or reconciled by adjusting the expectation.
3. **Schema fingerprint.** Matches the locked value. A mismatch means the
   delivery's shape changed and the field map may not apply.
4. **Content digest.** Record the Area-1 digest immediately, the way
   `24524f70c370d6c42a2b03d5385295a5` is recorded for the development artefact.
   It is the baseline every later drift check compares against, and it is
   worthless if captured after the first mutation.
5. **State gate.** Re-run `scripts/production_state_gate.py`. It must now report
   `GOVERNMENT` / `LOADED` with no mock warning.
6. **Reportability is still gated.** `findings_available` governs only whether
   results *can* exist. Analyst determination and independent QA approval are
   untouched and still required. **An analyst cannot approve their own work**,
   and that is enforced by database trigger, not by convention.
7. **Source limitations must be re-checked, not carried over.** In particular,
   settle the `SAM_GOV_API_KEY` question from B1 Finding 2 before issuing a
   report that discloses a SAM.gov limitation — a false disclosure is its own
   defect.

---

## Stage 5 — first operations

- The first review cycle runs under a **new rule version**. Do not overwrite
  `phase6-bulk-1.1.0`; the development observations are a true record of that
  run and remain evidence of how the method behaved.
- Retention: apply the approved D8 period via
  `RetentionPolicy.with_approved_period(...)`. Apply the **period** first and
  leave `worm_locked=False` unless the Government has explicitly directed an
  irreversible lock. The lock cannot be undone.
- Every report carries its source-limitation disclosures. Confirm each one is
  still true of the production environment rather than inherited from the
  development record.

---

## Abort path

Activation is abandoned partway through more often than anyone plans for. If you
stop after Stage 3 has begun:

1. **Do not delete the intake.** Area 1 is append-only by design; there is no
   delete path and creating one to tidy up would remove the control.
2. Mark the intake's `status` and `error` — the only two columns the application
   role may update on `rce_source_intakes` — to record why it was abandoned.
3. Re-run the state gate and record what it reports. A partial intake that still
   reports `NOT_LOADED` is correct behaviour, not a bug to be fixed.
4. Record the abort in the decision log with the reason. An abandoned activation
   is a fact about the delivery or the environment, and the next attempt needs
   to know it.
5. **Do not retry by loosening a check that refused.** The check refused for a
   reason that has not changed.

---

## Related documents

| Document | Purpose |
| --- | --- |
| `production_owner_role_runbook.md` | Blocker 1 — the ownership transfer itself |
| `production_readiness_verification_2026-08-24.md` | Current verified state of every gate |
| `internal/production_readiness_register.md` | The full blocker register |
| `identifier_authority_boundary.md` | What may and may not be verified, and by whom |
| `BACKUP_RESTORE_PROCEDURE.md` · `runbooks/backup-restore.md` | Blocker 9 |
| `cor_activation_package/05_Government_Data_Request.md` | What was asked of the Government |
| `cor_activation_package/10_First_30_Days.md` | What happens after this runbook completes |
