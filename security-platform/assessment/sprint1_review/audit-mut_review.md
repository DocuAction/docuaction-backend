# Branch Review — AUDIT-MUT

**Backend:** `sprint1/audit-mut-log-integrity` @ `4893f1f` (**stacked on `9e041df`** — tip of the stack)
**Frontend:** none
**Finding:** AUDIT-MUT — Medium · OWASP A08 · CWE-778 · NIST AU-9
**Risk rating: LOW**

> **Finding ID.** Requested as "AU-01 (High)". No `AU-01` exists in the register; the
> audit-immutability row is **`AUDIT-MUT`, severity Medium**. `AU-9` is the NIST control
> it maps to — the likely source of the confusion. The evidence supports *splitting* the
> row rather than raising its severity (§4).

---

## 1. Files changed

`4893f1f` — 3 files, **+382 / −6**. **284 additions are documentation**, leaving ~98 lines
of code across two files.

| File | + | − | Purpose |
|---|--:|--:|---|
| `app/api/compliance.py` | 82 | 4 | `_PERSONAL_DETAIL_KEYS` + `_redact_personal_data()`; pseudonymise instead of delete; corrected response contract |
| `app/api/admin_users.py` | 16 | 2 | Retain the detach, document why it must not become a delete, record `audit_rows_detached` |
| `docs/compliance/AUDIT_LOG_INTEGRITY.md` | 284 | 0 | **NEW** — control record incl. the trigger assessment |

---

## 2. Why each change was necessary

### `app/api/compliance.py`

The GDPR erasure flow ran `for log_entry in logs: await db.delete(log_entry)` — destroying
audit records outright. That encodes the wrong side of a genuine legal conflict:

- **HIPAA** §164.312(b) with **45 CFR §164.316(b)(2)** requires audit records retained for
  **six years**.
- **NIST AU-9** requires audit information protected from unauthorised deletion.
- **GDPR Article 17(3)(b)** expressly **exempts** erasure where processing is necessary for
  compliance with a legal obligation; Art. 17(1) covers *personal data*, not the fact that
  a security event occurred.

**Correct resolution: retain the record, erase the personal data inside it.** Also worth
naming: deleting a user's audit rows would silently destroy the evidence of any prior
attack *against* that account.

`_redact_personal_data()` blanks a defined key set (`email`, `full_name`, `phone`, `ip`,
`mrn`, `ssn`, `date_of_birth`, `npi` and variants) **plus** any string value containing the
subject's email, catching an address embedded in otherwise non-personal free text.
Non-dict payloads cannot be selectively redacted, so they are dropped wholesale rather
than risk retaining personal data. It returns a **new** dict so SQLAlchemy reliably detects
the change on a `JSON` column — mutating in place is a known silent-no-op trap there.

### `app/api/admin_users.py`

The `UPDATE audit_logs SET user_id = NULL` is **retained**, because it is structurally
required, not careless: `audit_logs_user_id_fkey` is **`NO ACTION`**, so a user row cannot
be deleted while an audit row references it. Proven empirically — attempting the delete
without detaching raises `ForeignKeyViolationError`.

Added: a comment explaining that constraint and explicitly forbidding conversion to
`sa_delete`, plus the `rowcount` recorded as `audit_rows_detached` in the deletion's own
audit entry — so **the trail explains its own attribution gap**. A later reviewer can see
that N records lost their subject, when, and at whose hand.

---

## 3. Database schema changes

**NONE — and deliberately so.** No migration, no DDL, no trigger, no new column, no index.
This is an application-layer change only.

### Why no database trigger (asked explicitly)

**1. A trigger would have broken production immediately — the decisive reason.** A
`BEFORE UPDATE OR DELETE` trigger raising on `audit_logs` breaks the **live**
`DELETE /api/admin/users/{user_id}` path, because that path *must* null `user_id` for the
`NO ACTION` FK. Add the trigger before fixing the application and admin user deletion
starts failing with a database error. **The application layer had to be fixed first, and
this branch is that prerequisite.**

**2. It would be a speed bump, not a control.** The app's role (`docuaction`, not
superuser) **owns** the table and executes `ALTER TABLE` DDL on every startup
(`main.py:126-131`), so the same credential that writes audit rows can `DROP TRIGGER`. It
defends against application-logic bugs — worth having — but not against a compromised
application or a privileged operator, which is what AU-9 contemplates. Presenting a
self-droppable trigger as WORM would overstate the control.

**3. Real immutability needs privilege separation or an external sink** — an append-only
role with `INSERT`/`SELECT` but no `UPDATE`/`DELETE`, or shipping to Azure Monitor /
immutable blob storage with a retention lock. Both are infrastructure.

**Recommended sequence:** this change → hash-chain (detection) → privilege separation or
external sink (prevention) → optionally a trigger as defence-in-depth once the admin path
no longer needs `UPDATE`.

### Data changes

**None written by this branch.** Rows pseudonymised while it is live stay pseudonymised —
which is the desired end state either way, so nothing needs unwinding on rollback.

---

## 4. API behaviour changes

| Path | Change | Reachable today? |
|---|---|---|
| `DELETE /api/user/hard-delete` | audit rows now **pseudonymised, not deleted**; response body changed (§5) | **NO — 404.** `app/api/compliance.py` is **not mounted** in `main.py`; the route is absent from the 278-route app. Verified at runtime. |
| `DELETE /api/admin/users/{user_id}` | unchanged behaviour; the resulting audit entry now carries `audit_rows_detached: N` | **Yes** |
| Everything else | unchanged | — |

**This is why the finding should be split rather than escalated.** The destructive path is
dead code; live exposure was loss of *attribution*, not loss of *records*. It was fixed
anyway because mounting `compliance.py` would have made the platform delete audit history
on request — the same "loaded gun" reasoning applied to `api/security.py` in DP-02.

Response-shape note for any future consumer: `audit_logs_deleted` is **retained** for API
compatibility and now truthfully reports `0`; `audit_logs_pseudonymised` is added
alongside it. No key was removed from the report.

---

## 5. GDPR / privacy impact

**The user-facing notification text changed, and that is a compliance improvement, not
cosmetic.**

| | Before | After |
|---|---|---|
| `data_to_be_deleted` | included **"All audit log entries"** | that line **removed** |
| `data_to_be_retained` | *(absent)* | **new** — states audit entries are retained but pseudonymised, with identity, email and IP erased |
| `retention_basis` | *(absent)* | **new** — cites HIPAA §164.312(b), 45 CFR §164.316(b)(2), GDPR Art. 17(3)(b), NIST AU-9 |

Promising deletion while retaining the rows would **misrepresent the erasure to the data
subject** — a compliance problem in its own right, and arguably worse than the retention
it concealed. The endpoint now discloses exactly what is kept and why.

**Net privacy position:** the data subject's personal data *is* erased from audit records
(identity, email, IP, and personal keys inside `details`). What is retained is the
non-personal fact that an event occurred, at a time, against a resource. That satisfies
Art. 17 in substance while meeting the retention obligation — and it is disclosed rather
than assumed.

---

## 6. Backward compatibility

**No break.**

| Consumer | Impact |
|---|---|
| Frontend | None — `/api/user/*` routes do not exist; nothing calls them |
| Admin UI | None — `DELETE /api/admin/users/{id}` behaves identically; only the audit entry gains a field |
| Report consumers | None — no key removed; `audit_logs_deleted` still present, value now `0` |
| Stored data | None written |
| Other modules | None — `_redact_personal_data` is module-private and new |

---

## 7. Rollback procedure

```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git revert 4893f1f
```

No schema, migration, trigger, config, data, or dependency change to unwind.

**Be clear on what reverting restores:** the GDPR flow would again **delete** audit rows if
`compliance.py` were ever mounted, and the admin path would stop recording
`audit_rows_detached`. Rows already pseudonymised stay pseudonymised — no data unwind
needed.

**Stack caveat:** `4893f1f` is the **tip** of the stack, so reverting it is the cleanest of
the four and conflict-free. Its files (`compliance.py`, `admin_users.py`) are not touched
by any other Sprint 1 commit.

---

## 8. Risk rating: **LOW**

| Factor | Assessment |
|---|---|
| Blast radius | One unmounted endpoint + one field added to an existing admin audit entry |
| Direction | Preserves data that was previously destroyed; strictly less destructive |
| Regression potential | Very low — the changed endpoint is unreachable; the reachable change is additive |
| Data risk | None written; the pseudonymisation path is only reachable via an unmounted route |
| Reversibility | Complete, cleanest of the four |
| Verification | Redaction unit-tested incl. non-dict payloads; static check confirms no destructive `AuditLog` op remains; **DB integration test: audit row count unchanged (30 → 30) across a user deletion, FK violation confirmed, test data removed**; 22/22 modules, `/health` 200, admin users 200, hard-delete still 404 |

### Still open — not downgraded

- **No tamper detection.** No hash chain or signature — a row can still be edited or
  removed directly in the database with **no trace**. This is the main remaining AU-9 gap.
- **No tamper prevention.** No trigger, no RLS, no privilege separation.
- **Attribution is still destroyed on user hard-delete** while the FK is `NO ACTION`.
  Soft-deleting users (`deleted_at`) would keep the FK intact and preserve a resolvable
  subject.
- `StateAuditLog` in `enterprise_models.py` is *documented* "immutable append-only" with
  nothing enforcing it — a comment, not a control.
- No audit retention/archival mechanism for the six-year obligation; no alerting on audit
  anomalies (bulk detachment, `created_at` gaps).
- PHI **read** access still unaudited (separate open finding AUDIT-READ).
