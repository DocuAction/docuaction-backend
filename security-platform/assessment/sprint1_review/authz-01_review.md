# Branch Review — AUTHZ-01

**Backend:** `sprint1/authz-01-case-management-auth` @ `4879e3e`
**Frontend:** `sprint1/authz-01-case-management-auth` @ `7c76208`
**Finding:** AUTHZ-01 — Critical · OWASP A01 · CWE-306 · NIST AC-3
**Risk rating: LOW**

> **Topology correction.** This is the **first commit in a four-commit linear stack** on
> the backend, not a standalone branch. See `merge_plan.md` §1 — the four Sprint 1
> branches are not independent, contrary to an earlier statement of mine.

---

## 1. Files changed

### Backend — `4879e3e` (1 file, +15 / −2)

| File | + | − | Nature |
|---|--:|--:|---|
| `app/case_management/routes.py` | 15 | 2 | 4 functional lines (import, `Depends`, `dependencies=`), 11 explanatory comment |

### Frontend — `7c76208` (1 file, +10 / −3)

| File | + | − | Nature |
|---|--:|--:|---|
| `src/app/case-management/page.js` | 10 | 3 | `authHeaders()` helper + applied at 3 fetch call sites |

**Total: 2 files, +25 / −5.**

---

## 2. Why each change was necessary

### `app/case_management/routes.py`

Three edits, one purpose.

1. **`Depends` added to the `fastapi` import** — mechanically required by (3).
2. **`from app.core.security import get_current_user`** — the module had **no import
   from `app.core.security` at all**. Every other router in the application imports its
   auth dependency from there; this one was authored as a standalone drop-in (its own
   docstring reads *"Add to main.py: safe_load(...)"*) and was mounted without being
   retrofitted.
3. **`dependencies=[Depends(get_current_user)]` on the `APIRouter`** — the security
   change. Router-level rather than per-endpoint for a specific reason: it covers all 22
   endpoints in one place and **cannot be forgotten when a 23rd is added**. Twenty-two
   decorators would have been 22 chances to miss one.

`get_current_user` also enforces account-disabled / pending-approval /
session-revocation state on every request, so the gate does more than check a signature.

The 11 comment lines record why it is router-level and explicitly forbid moving it to
per-endpoint decorators — the failure mode is silent, so the reasoning needed to survive
in the file.

### `src/app/case-management/page.js`

The page sent **no `Authorization` header on any of its three live calls**
(`notes/voice-to-note`, `billing/determine-code`, `info`). Backend-only auth would have
turned the entire Case Management page into a 403. The `authHeaders()` helper reads the
`'token'` key — the dominant convention in this app (16 uses vs 3 for the legacy
`govcon_token`) — and is applied at each call site.

---

## 3. Database schema changes

**NONE — verified, not assumed.**

No migration added, no DDL, no model change. Related verification from the finding
investigation: the 6 `cm_*` tables defined in `app/case_management/models.py` are **not
deployed** — `models.py` is never imported (only `.routes` is), no Alembic migration
references them, and a live query returned `tables starting 'cm': NONE` out of 51 public
tables. This branch does not change that.

---

## 4. API behaviour changes

**Exactly as expected in the review brief, with one count correction.**

| | Before | After |
|---|---|---|
| `/api/v1/case-management/*` — **22 endpoints** (not 12) | reachable anonymously | **403** without a token, **401** with an invalid token, **200** with a valid one |
| Every other endpoint | — | **UNCHANGED** |

The brief says 12 endpoints; the actual count is **22**. The Phase 0 range `:189–:652`
omitted `/dashboard/stats`, `GET /patients`, `/patients/{id}` and `/info`.

Response code note: unauthenticated requests return **403**, not 401, because
`HTTPBearer(auto_error=True)` rejects a *missing* credential before any handler runs. A
malformed/expired token yields 401 from `decode_token`. Both are correct; a client
distinguishing them should treat 403-with-no-header as "not logged in".

Full per-endpoint verification (all 22 gated, static dependency resolution:
`UNPROTECTED: NONE`) is in `remediation/AUTHZ-01_remediation.md` §4.

---

## 5. Frontend behaviour changes

| | Before | After |
|---|---|---|
| `/case-management` page load | `GET /info` with no headers | `GET /info` with `Authorization: Bearer <token>` |
| Voice → CCM note submit | `POST` with `Content-Type` only | `+ Authorization` |
| Billing calculator | `POST` with `Content-Type` only | `+ Authorization` |
| Logged-out user | page rendered, calls succeeded anonymously | calls return 403; page renders, `info` fetch fails silently (existing `.catch(() => {})`) |

Build verified: `npm run build` exit 0, static export produced, and the client bundle
contains `Authorization:\`Bearer ${t` and `getItem("token")`. The SSR chunk correctly
omits them — `typeof window !== 'undefined'` is statically false server-side, so the
branch is tree-shaken there. These are client-side fetches in event handlers and
`useEffect`, so that is correct.

---

## 6. Backward compatibility

**No break — with one caveat the brief's expectation does not cover.**

| Consumer | Impact |
|---|---|
| Frontend `/case-management` page | **Would have broken** on backend-only deploy. Fixed by the companion frontend commit. **This is why the two must ship together.** |
| Other backend modules | None — nothing imports `case_management`. Only string module-ids in `admin_users.py:40,51` for the area-access catalogue. |
| External API consumers | None known. The endpoints were undocumented (`ENABLE_OPENAPI` defaults false) and return stub data. |
| Database / stored data | None. |

The brief states *"Expected: No — case_management had no consumers."* That is right for
**backend** consumers but **wrong for the frontend** — there was exactly one consumer,
and it is the reason this is a two-repo change. Anyone reading only the backend diff
would deploy a regression.

---

## 7. Rollback procedure

Backend and frontend are independent commits and can be reverted separately, **but
order matters.**

```bash
# Backend (restores the vulnerable-but-working state)
cd "C:/Imran_Coding projects/DocuAction/backend"
git revert 4879e3e
# or, without a revert commit:
git checkout e3f9e5b -- app/case_management/routes.py

# Frontend (only if you also reverted the backend)
cd "C:/Imran_Coding projects/DocuAction/frontend"
git revert 7c76208
```

**Revert the backend FIRST, or both together.** Reverting the frontend alone re-breaks
the page (auth still enforced, header no longer sent). Reverting the backend alone is
safe — an unused `Authorization` header is harmless.

No schema, migration, config, data, or dependency change to unwind.

**Stack caveat:** `4879e3e` is the base of the stack. Reverting it while DP-02 is merged
leaves DP-02's `routes.py` change (the meeting-minutes `phi_map`) in place — harmless,
but `git revert 4879e3e` may conflict on `routes.py`. Revert in reverse stack order if
both must go.

---

## 8. Risk rating: **LOW**

| Factor | Assessment |
|---|---|
| Blast radius | 22 endpoints in one module with one known consumer |
| Direction of change | Restrictive — closes access, cannot grant it |
| Regression potential | Low; the one at-risk consumer is fixed in the same change and build-verified |
| Data risk | None — no schema or data touched |
| Reversibility | Complete, two `git revert`s, no state |
| Verification depth | 22/22 routes statically confirmed gated; runtime 403/401/200 confirmed; 92 TEFCA routes and 22/22 modules confirmed intact; `/health` 200 |

**Residual risk to accept knowingly:** any authenticated user reaches these endpoints
regardless of granted areas, because `users.allowed_modules` is **never enforced
server-side** anywhere in the application — a platform-wide gap, not one this branch
introduces. A `viewer` can also generate and sign notes. Both are logged as Sprint 2
items and were explicitly deferred by you.
