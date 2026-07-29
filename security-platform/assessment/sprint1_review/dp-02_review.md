# Branch Review — DP-02

**Backend:** `sprint1/dp-02-phi-egress-minimization` @ `da9ae7c` (**stacked on `4879e3e`**)
**Frontend:** none
**Finding:** DP-02 — High · OWASP A04/A01 · CWE-200 · NIST AC-4 / SC-8
**Risk rating: MEDIUM** (the only branch in Sprint 1 that alters generated clinical content)

---

## 1. Files changed

`da9ae7c` — 6 files, **+624 / −15**. Of those additions, **281 are documentation** and
**41 are a pure comment block**, leaving ~302 lines of functional code.

| File | + | − | Purpose |
|---|--:|--:|---|
| `app/case_management/services/phi_deidentify.py` | 188 | 0 | **NEW** — exact-value redact/restore module (≈95 lines are the docstring and inline caveats) |
| `app/case_management/services/discharge_engine.py` | 54 | 6 | `phi_map` chokepoint in `_call_claude` + 4 call sites + `Optional` import |
| `app/case_management/services/ccm_engine.py` | 53 | 8 | `phi_map` chokepoint in `_call_claude` + 6 call sites + import |
| `app/api/security.py` | 41 | 0 | **Comment only** — DO-NOT-MOUNT header enumerating false attestations |
| `app/case_management/routes.py` | 7 | 1 | `phi_map` for `/meetings/generate-minutes`, which calls `_call_claude` directly |
| `docs/compliance/AI_EGRESS_PHI.md` | 281 | 0 | **NEW** — control record |

---

## 2. Why each change was necessary

### `phi_deidentify.py` (new)

The prescribed remediation — "run (expanded) `mask_pii` before egress" — is a **provable
no-op**. Against the exact prompt `extract_clinical_facts` builds, `mask_pii` redacts
**0 items**: `PATTERNS` contains **no name pattern at all** (HIPAA Safe Harbor identifier
#1) and its DOB pattern requires a keyword prefix *and* `MM/DD/YYYY`, so it misses the
bare ISO dates `discharge_engine.py:366` interpolates (#3). Applying it as written would
have emitted a `pii_count` log line that reads like a working control while doing nothing.

A new module was needed because the engines already **know** the real identifier values
from `patient_context`, enabling exact-value substitution — reliable, with no false
negatives on unusual formats and no heuristic mangling of clinical vocabulary.

`app/services/pii_masking.py` was deliberately **not modified**: extending it would put
the documents path (`ai_engine.py:251`, its only call site) at risk for no benefit here.

### `ccm_engine.py` / `discharge_engine.py`

Redaction is placed in `_call_claude` — the **single egress point in each module** — not
at the 12 call sites. Two places instead of thirteen, and a newly added call site cannot
forget it. Each public function passes `phi_map=build_phi_map(patient_context)`.

`Optional` was added to `discharge_engine`'s imports (it had no `typing` import).

### `routes.py`

`/meetings/generate-minutes` imports `_call_claude` **directly**, bypassing the engine
wrappers, so it must supply its own `phi_map`.

### `api/security.py`

Comment only, no behaviour change. The module publicly attests
`"pii_masking_active": True` and `"HIPAA": "BAA available, PHI/PII masking before AI
processing"`. Verified **NOT MOUNTED** (`/api/security/status` → 404), so these are dead
code rather than a live misrepresentation — but mounting it would publish false
compliance claims on an endpoint whose own docstring advertises "no auth required for
transparency." The header enumerates each unverified claim and the evidence needed first.
The stale "Railway.app on Google Cloud Platform" line was left in place per instruction.

---

## 3. Database schema changes

**NONE.** No migration, no DDL, no model change, no data written or migrated.

---

## 4. API behaviour changes

**No request or response *contract* changes.** Same paths, same auth, same status codes,
same response shapes. What changed is what leaves the process.

| Path | Change |
|---|---|
| 9 endpoints that reach an AI engine | Outbound Anthropic payloads no longer contain the patient's name, MRN, DOB, SSN, or phone. Returned note text is unchanged for the clinician — tokens are restored before the response. |
| `/billing/determine-code`, `/billing/cpt-reference`, `/education/topics`, all GET stubs | No AI call — untouched |
| Everything outside `case_management` | Untouched |

**Observable effects a reviewer should know about:**

1. **New log line** `phi_identifiers_masked: N` at INFO (count only, never values — logging
   values would recreate DP-01).
2. **Generated note content can differ subtly**, because the model now reasons over
   `[PATIENT]` rather than a real name. This is the material risk — see §8.
3. **No latency change of note** — redaction is a handful of regex substitutions on
   strings already being serialised for a network call.

Evidence: `httpx.AsyncClient.post` was monkeypatched to capture real outbound payloads.
`voice_to_ccm_note` → 3 requests, `generate_discharge_summary` → 3, `generate_sdoh_assessment`
→ 1; in all of them first name, last name, MRN, DOB and phone were absent while
`metformin`, `blood sugars 180 to 250`, `E11.9` and `CHF` were present, and the returned
note read `"CCM NOTE for Sarah Johnson (MRN 4478812, DOB 1951-03-14)..."` — correctly
restored.

---

## 5. Frontend behaviour changes

**NONE.** No frontend file touched; API contracts unchanged, so no frontend work is
required.

---

## 6. Backward compatibility

**No break.**

| Consumer | Impact |
|---|---|
| Frontend | None — response shapes identical |
| Other backend modules | None — `phi_map` defaults to `None`, in which case `_call_claude` behaves byte-for-byte as before |
| Stored data | None — nothing persisted (the `cm_*` tables are not deployed) |
| Anthropic API contract | None — same endpoint, same headers, same model IDs |
| Compliance posture | **Improves**, and the `security.py` guard prevents a future regression |

`phi_map=None` producing the original behaviour exactly is the property that makes both
rollback and partial adoption safe.

---

## 7. Rollback procedure

```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git revert da9ae7c
```

No schema, migration, config, data, or dependency change to unwind.

**To disable the control without reverting the commit** (useful if note quality
regresses in review and you want the code retained): stop passing `phi_map` at the call
sites, or have `build_phi_map()` return `{}`. Every redaction path is skipped when the
map is falsy, restoring pre-branch behaviour with a one-line change.

**Stack caveat:** `da9ae7c` sits on `4879e3e`. Reverting DP-02 alone is clean. Reverting
AUTHZ-01 while DP-02 is present may conflict in `routes.py` — revert in reverse stack
order.

---

## 8. Risk rating: **MEDIUM**

The only Sprint 1 branch that can change clinical output, so it does not get a Low.

| Factor | Assessment |
|---|---|
| Blast radius | 9 AI-calling endpoints in one module |
| Direction | Reduces data egress; cannot increase it |
| **Content risk** | **Real** — see the over-redaction case below |
| Data risk | None — nothing persisted |
| Reversibility | Complete; also disable-able without a revert |
| Verification | Real outbound payloads intercepted; guards unit-tested; 22/22 modules, `/health` 200, AUTHZ-01 gate intact |

### The accepted trade-off, stated plainly

Matching is **case-insensitive**, so a surname colliding with clinical vocabulary is also
replaced clinically. A patient named **Stone** turns `kidney stone` into
`kidney [PATIENT_LAST]` in the prompt, which can make the generated note subtly wrong.
Also affects Rash, Long, Short, Gray, Bell, Cross, Marsh, Back, Head.

Chosen deliberately: over-redaction is **visible** to the clinician at the mandatory
`requires_review` gate (no note in this module can be signed unreviewed), whereas a PHI
leak is invisible and irreversible. The module documents how to invert the trade-off
(make names case-sensitive) if a deployment prefers.

**Reviewer action:** if the patient population plausibly includes such surnames, spot-check
a generated note for one before merging.

### Guards verified against corruption

- 3-character minimum — a name like `Al`/`Jo` is never substituted, so `Also`, `ALT`,
  `Jones-criteria` are untouched (verified: text byte-identical).
- Longest-value-first — `Sarah Johnson` consumed before `Johnson`, so no interleaved
  `[PATIENT_FIRST] [PATIENT_LAST]` fragments.
- Word boundaries only where the value is alphanumeric, so `(512) 555-0143` still matches.
- `None` / `{}` / identifier-free context → empty map → text byte-identical.

### Residual risk — NOT downgraded, per your instruction

**DP-02 is not closed.** The clinical narrative — symptoms, diagnoses, medications, the
raw transcript — is still transmitted in full and is still PHI under HIPAA. It cannot be
masked without destroying the feature. The controlling safeguard is a **signed BAA plus
zero-retention confirmation: contractual, not code.** Also still leaked: third-party
names in transcripts (`her daughter Emily`, `Dr. Alan Reyes`, `Austin Regional Clinic`),
dictated format variants, and `generate_government_case_document` (takes `case_facts`,
so no known values to substitute). No audit-log entry records PHI egress at all.
