# Audit Log Integrity — Control Record (finding AUDIT-MUT)

**Status:** APPLICATION LAYER FIXED — tamper *detection* and *prevention* remain open (§6).
**Date:** 2026-07-26 · **Sprint:** 1 — Critical & High Security Remediation
**Branch:** `sprint1/audit-mut-log-integrity`
**OWASP** A08 · **CWE** 778 · **NIST** AU-9

> **Finding ID note.** This work was requested as "AU-01 (High)". No `AU-01` exists in
> the register; the audit-immutability row is **`AUDIT-MUT`, severity Medium**
> (`AU-9` is the NIST control it maps to, which is the likely source of the
> confusion). This record uses the register's ID. Severity is discussed in §1.1 —
> the evidence supports *splitting* it rather than raising it.

---

## 1. Verification

### 1.1 The finding is PARTIALLY confirmed — the destructive half is unreachable

| Register claim | Verified result |
|---|---|
| `audit_logs` **deleted** by compliance flow (`compliance.py:129-134`) | **Code confirmed — but UNREACHABLE.** `app/api/compliance.py` is **not mounted** in `app/main.py`; `/api/user/hard-delete` is absent from the app's 278 routes and returns **404** at runtime (verified). Dead code, not a live control failure. |
| `audit_logs` **updated** by admin flow (`admin_users.py:433`) | **CONFIRMED AND LIVE** — reachable via `DELETE /api/admin/users/{user_id}`. But the statement is `UPDATE audit_logs SET user_id = NULL`, which **preserves the record** and severs only attribution. |
| No WORM / hash-chain | **CONFIRMED** — see §1.2. |

**Severity implication.** The live exposure is *loss of attribution*, not *loss of
records*: no reachable code path destroys an audit row. The unmounted flow is
nonetheless a loaded gun — mounting `compliance.py` would have made the platform
delete audit history on request — so it was fixed rather than left in place.

### 1.2 Current state of `audit_logs` (read live from the database)

| Property | Value |
|---|---|
| Columns | `id, user_id, action, resource_type, resource_id, details, ip_address, created_at, tenant_id` |
| Triggers | **NONE** — no DB-level immutability |
| Row-level security | **disabled** (`relrowsecurity = false`) |
| Hash / signature / sequence column | **none** — no tamper-detection field exists |
| FK `audit_logs_user_id_fkey` | `user_id → users.id`, **`NO ACTION`** |
| Application DB role | `docuaction`, **not** superuser, but **owns the table** and executes `ALTER TABLE` DDL on every startup (`main.py:126-131`) |

### 1.3 The `NO ACTION` FK makes detachment structurally required

Proven empirically against the live database, not inferred:

```
audit rows: baseline=29 after seeding=30

does the FK force detachment? (NO ACTION)
   FK blocked the delete as expected -> ForeignKeyViolationError
   => nulling user_id is structurally REQUIRED, not gratuitous

fixed admin path: detach, then delete user
   detach -> UPDATE 1
   audit rows after user deletion: 30 (was 30)
   AUDIT RECORD SURVIVED: True | detached row present: True
```

So `admin_users.py:433` is not careless — with a `NO ACTION` FK, a user row cannot be
deleted while any audit row references it. The options are: null the FK (current, and
correct), `ON DELETE SET NULL` (same effect, enforced by the schema), or cascade-delete
the audit rows (**unacceptable** — destroys the trail).

### 1.4 An existing "append-only" claim that is not enforced

`app/models/enterprise_models.py` documents `StateAuditLog` as
*"Immutable append-only state transition log"*. Nothing enforces this — no trigger, no
RLS, no hash chain. It is a comment, not a control. Not changed here (out of scope),
recorded in §6.

---

## 2. Root cause

Audit rows are ordinary mutable table rows in the application's own schema, written and
owned by the same database role that serves user traffic. Integrity therefore rests
**entirely** on application code choosing not to mutate them. There is no detective
control (no hash chain — an edited or removed row leaves no trace) and no preventive
control (no triggers, no RLS, no privilege separation).

The GDPR erasure flow then encoded the wrong resolution of a genuine legal conflict:
it treated audit records as ordinary personal data subject to deletion, when the
retention obligation outranks the erasure request (§3.1).

---

## 3. Why database triggers were NOT the first step

You asked for this to be explained rather than assumed. Triggers are the wrong *first*
move here, for one decisive reason and two supporting ones.

**1. A trigger would have broken production immediately — ordering is not optional.**
A `BEFORE UPDATE OR DELETE` trigger raising an exception on `audit_logs` would break
the **live** `DELETE /api/admin/users/{user_id}` path, because that path *must* null
`user_id` to satisfy the `NO ACTION` FK (§1.3). Add the trigger before fixing the
application and admin user deletion starts failing with a database error. **The
application layer had to be corrected first.** That is what this change does, and it
is the prerequisite for a trigger being safe at all.

**2. It would be a speed bump, not a control.** The app's role owns the table, so the
same credential that writes audit rows can `DROP TRIGGER ... ; UPDATE ... ;`. A
trigger meaningfully protects against *application-logic bugs and future careless
edits* — genuinely worth having — but not against a compromised application or a
privileged operator, which is the threat AU-9 actually contemplates. Presenting a
self-droppable trigger as WORM would overstate the control.

**3. Real immutability requires privilege separation or an external sink.** An
append-only role with `INSERT`/`SELECT` but no `UPDATE`/`DELETE`, or shipping audit
events to Azure Monitor / immutable blob storage with a retention lock. Both are
infrastructure changes, outside a code-only fix.

**Recommended sequence:** (a) fix the application paths — *this change*; (b) hash-chain
for tamper **detection**; (c) privilege separation or external sink for tamper
**prevention**; (d) optionally a trigger as defence-in-depth once (a) has shipped and
the admin path no longer needs `UPDATE`. Items (b)–(d) are §6, deliberately not
half-built against a live audit table.

### 3.1 GDPR vs HIPAA — how the conflict was resolved

These obligations genuinely conflict, and the original code silently picked the wrong
side:

- **HIPAA** §164.312(b) with **45 CFR §164.316(b)(2)** requires audit records to be
  retained for **six years**.
- **NIST 800-53 AU-9** requires audit information to be protected from unauthorised
  modification and deletion.
- **GDPR Article 17(3)(b)** expressly **exempts** erasure where processing is
  necessary for compliance with a legal obligation. Article 17(1) covers *personal
  data*, not the fact that a security event occurred.

**Resolution: retain the record, erase the personal data inside it.** The audit
timeline stays complete and the data subject's identity is removed. This also avoids a
side effect worth naming — deleting a user's audit rows would silently destroy the
evidence of any prior attack *against* that account.

---

## 4. Changes made

Two application-layer changes. No schema change, no migration, no trigger, no
infrastructure change.

### `app/api/compliance.py` — pseudonymise instead of delete

Replaced the `for log_entry in logs: await db.delete(log_entry)` loop with
pseudonymisation:

| Field | Action |
|---|---|
| `user_id` | set `NULL` — detaches attribution, and satisfies the `NO ACTION` FK |
| `details` | passed through new `_redact_personal_data()` — personal keys → `[ERASED]`, audit-meaning keys retained, `_gdpr_erasure: true` stamped |
| `ip_address` | set `NULL` — personal data under GDPR, not needed once the subject is erased |
| the row itself | **RETAINED** |

`_redact_personal_data()` redacts a defined key set (`email`, `full_name`, `phone`,
`ip`, `mrn`, `ssn`, `date_of_birth`, `npi`, and variants) **plus** any string value
containing the subject's email, so an address embedded in otherwise non-personal free
text is caught. Non-dict payloads cannot be selectively redacted, so they are dropped
wholesale rather than risk retaining personal data. It returns a **new** dict so
SQLAlchemy reliably detects the change on a `JSON` column.

The response contract was also corrected. The endpoint previously promised
*"All audit log entries"* under `data_to_be_deleted`; that entry is removed, and a new
`data_to_be_retained` plus `retention_basis` disclose the retention and its legal
basis. Reporting deletion while retaining rows would misrepresent the erasure to the
data subject — its own compliance problem. `audit_logs_deleted` is kept in the report
for API compatibility and now truthfully reports **0**, alongside a new
`audit_logs_pseudonymised` count.

### `app/api/admin_users.py` — keep detachment, make it deliberate and self-documenting

The `sa_update(...).values(user_id=None)` is retained (it is structurally required) and
now carries a comment explaining why, plus an explicit prohibition on converting it to
`sa_delete`. The resulting `rowcount` is recorded in the audit entry for the deletion
(`audit_rows_detached`), so **the trail explains its own attribution gap** — a later
reviewer can see that N records lost their subject, when, and at whose hand.

---

## 5. Validation

### Redaction behaviour

```
deleted_email          -> [ERASED]
full_name              -> [ERASED]
ip                     -> [ERASED]
note                   -> [ERASED]      (email inside free text — caught)
action_outcome         -> success       (audit meaning retained)
status_code            -> 200           (audit meaning retained)
documents_affected     -> 3             (audit meaning retained)
_gdpr_erasure          -> True

non-dict payloads: None -> None | {} -> {_gdpr_erasure} | "str" -> {redacted} | [..] -> {redacted}
```

### Static assertions

| Check | Result |
|---|---|
| Destructive `AuditLog` operations in `compliance.py` | **NONE** |
| `audit_logs_pseudonymised` present | yes |
| `sa_delete(AuditLog` in `admin_users.py` | **False** |
| `sa_update(AuditLog` detach retained | True |

### Database integration test

FK blocks the user delete (`ForeignKeyViolationError`), the detach succeeds, and the
audit row count is **unchanged** across a user deletion (30 → 30) with the detached row
present. Test data was removed afterwards; `audit_logs` returned to its 29-row
baseline.

### Platform regression (authenticated as admin)

| Check | Result |
|---|---|
| App import — 22/22 `safe_load` modules | none skipped |
| `GET /health` | **200** |
| `GET /api/admin/users` | **200** |
| `GET /api/tefca/dashboard/summary` | 200 |
| `GET /api/tefca/registry/entities` | 200 |
| `GET /api/v1/case-management/info` | 200 (403 anonymous — AUTHZ-01 gate intact) |
| `DELETE /api/user/hard-delete` | **404** — still unmounted, as expected |

---

## 6. Remaining risks and recommended follow-ups

| # | Item | Severity | Type |
|---|---|---|---|
| 1 | **No tamper detection.** No hash chain or signature — a row can still be edited or removed directly in the database with no trace. **This is the main open AU-9 gap.** | Medium | Code + migration |
| 2 | **No tamper prevention.** No trigger, no RLS, no privilege separation; the app role owns the table and holds DDL rights. | Medium | Infra |
| 3 | **Attribution is still destroyed on user deletion** — unavoidable while the FK is `NO ACTION` and the user row is hard-deleted. Better: soft-delete users (`deleted_at`) and keep the FK intact, so audit rows retain a resolvable subject. | Medium | Code + schema |
| 4 | `StateAuditLog` documented "append-only", not enforced (§1.4) | Low | Code |
| 5 | `compliance.py` remains unmounted — its GDPR erasure feature is unavailable. Now safe to mount from an audit standpoint, but its other steps were not reviewed here. | Low | Decision |
| 6 | No audit-log retention/archival policy; `audit_logs` holds 29 rows locally with no expiry or six-year archival mechanism | Medium | Policy |
| 7 | PHI **read** access is still not audited (existing finding AUDIT-READ) | Medium | Code |
| 8 | No alerting on audit anomalies (bulk detachment, gaps in `created_at`) | Low | Monitoring |

### Recommended next step — hash chain (tamper detection)

The highest-value follow-up, and the safest of the three, because it is additive:

1. Add nullable `prev_hash` and `row_hash` columns via Alembic (nullable = no backfill
   required, existing rows unaffected).
2. On insert, compute `row_hash = SHA256(prev_hash ‖ canonical(row fields))` in the
   single audit-write helper.
3. Add an admin-only verification endpoint that walks the chain and reports the first
   break.

Chain-verify then makes any direct edit or deletion **detectable**, which is what AU-9
asks for, without needing to prevent it at the database level. Estimated 2–3 days, per
the register. It touches the audit write path, so it warrants its own task rather than
being bundled into this fix.

---

## 7. Rollback

No schema change, no migration, no trigger, no infrastructure change, no dependency
change.

```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git revert <commit-sha>
```

Reverting restores the previous behaviour exactly. Note what that means: the GDPR flow
would again **delete** audit rows if `compliance.py` were ever mounted, and the admin
deletion path would stop recording `audit_rows_detached`. No data written by this change
needs unwinding — rows pseudonymised while it was live simply stay pseudonymised, which
is the desired end state either way.

---

## 8. Finding register update

`security_findings.md` row **AUDIT-MUT** is updated to record: the **deletion** path is
in `compliance.py`, which is **NOT MOUNTED** and returns 404 — dead code, so live
exposure was loss of attribution only, not loss of records; the **update** path is live
but preserves the record and is **structurally required** by the `NO ACTION` FK (proven
by `ForeignKeyViolationError`); both paths now fixed at the application layer; and
**hash-chain and WORM remain open**, with database triggers explicitly assessed and
deferred with reasons (§3).
