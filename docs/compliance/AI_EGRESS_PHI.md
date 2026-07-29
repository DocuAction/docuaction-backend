# PHI Egress to Third-Party AI — Control Record (finding DP-02)

**Status:** PARTIALLY FIXED — direct identifiers masked in code; clinical narrative
requires a BAA (contractual, not code).
**Date:** 2026-07-26 · **Sprint:** 1 — Critical & High Security Remediation
**Branch:** `sprint1/dp-02-phi-egress-minimization`
**OWASP** A04/A01 · **CWE** 200 · **NIST** AC-4 / SC-8

---

## 1. What was verified

### Every Anthropic egress path in the application

| Path | Masking before fix | PHI? |
|---|:--:|---|
| `case_management/services/ccm_engine.py` `_call_claude` — 6 invocations | none | Yes — patient name at 6 interpolation sites + full clinical narrative |
| `case_management/services/discharge_engine.py` `_call_claude` — 6 invocations | none | Yes — patient name + `date_of_birth` interpolated bare (`- Age: {date_of_birth}`) |
| `case_management/routes.py` `/meetings/generate-minutes` — imports `_call_claude` directly | none | Yes — patient name + raw meeting transcript |
| `api/meeting_routes.py:145` | none | Yes if clinical — raw `transcript_text`. **NOT in scope of this fix** |
| `services/ai_engine.py:251` | `mask_pii` — the only call site in the app | Document text |
| `api/migration_routes.py:478` | none | **No** — schema metadata only (`table.field (type)`) |
| `bulletin_intelligence/engine.py` — 9 calls | none | **No** — public news content |

`grep -rn "mask_pii\|pii_masking"` returned exactly **one** functional call site
(`ai_engine.py:251`). The case management engines never imported it.

**Egress multiplier:** `POST /notes/voice-to-note` makes **3** Anthropic calls
(extract → generate → critique); the critique call re-sends the entire generated
note. `generate_discharge_summary` also makes 3.

### The assessment's recommended fix was a provable no-op

The finding said to "run (expanded) `mask_pii` before egress." Measured against the
exact prompt `extract_clinical_facts` builds, `mask_pii` redacts **0 items**:

| Input | Redacted by `mask_pii` |
|---|:--:|
| `- Name: Sarah Johnson` | **0 — survives** (no name pattern exists in `PATTERNS`) |
| `- Age: 1951-03-14` (the literal `discharge_engine.py` shape) | **0 — survives** |
| `Date of birth 1951-03-14` | **0 — survives** |
| `- MRN: 4478812` | 1 ✓ |
| `DOB: 03/14/1951` | 1 ✓ |
| `SSN 442-88-1234`, phone, email, credit card, IP | 1 ✓ each |

`PATTERNS` has **no name pattern at all** — HIPAA §164.514(b)(2) Safe Harbor
identifier **#1** — and its DOB pattern requires a keyword prefix *and*
`MM/DD/YYYY`, so it misses the bare ISO dates these engines interpolate
(identifier **#3**). Applying it as specified would have emitted a `pii_count` log
line that reads like a working control while redacting nothing.

---

## 2. Root cause

Both engines were authored as self-contained drop-ins with their own `_call_claude`
and their own `HEADERS`, bypassing `app/services/ai_engine.py` — the one path built
with a masking step. There was no shared egress chokepoint, so the masking control
was never structurally reachable. `mask_pii` itself was written for
financial/contract documents (SSN, credit card, routing, passport, IP) and was
never extended for clinical identifiers.

---

## 3. What was implemented

### Exact-value replacement, not pattern matching

New module `app/case_management/services/phi_deidentify.py`. The engines already
know the real identifier values from `patient_context`, so the module substitutes
**those specific strings** rather than guessing with regex — no false negatives on
unusual formats, and no heuristic mangling of clinical vocabulary.

| Function | Purpose |
|---|---|
| `build_phi_map(patient_context)` | `{real_value: token}` from `first_name`, `last_name`, `mrn`, `date_of_birth`, `ssn`, `phone`, plus the combined `"First Last"` form |
| `redact(text, phi_map)` | Longest-value-first substitution → `(text, distinct_count)` |
| `restore(text, phi_map)` | Token → real value, for the generated note |
| `log_masked(count, ctx)` | Emits `phi_identifiers_masked: N` — **count only, never values** |

Safety properties, all verified:

- **Longest-first ordering** — `Sarah Johnson` is consumed before `Johnson`, so the
  prompt never contains interleaved `[PATIENT_FIRST] [PATIENT_LAST]` fragments.
- **Minimum length 3** (`_MIN_VALUE_LEN`) — a 2-char name (`Al`, `Jo`) is never
  substituted, so it cannot corrupt `Also`/`ALT`/`Jones-criteria`.
- **Word boundaries** applied only when the value starts/ends alphanumeric, so
  `(512) 555-0143` still matches (a `\b` next to punctuation never would).
- **No-op on empty input** — `None`, `{}`, and an identifier-free context all yield
  an empty map and leave text byte-identical.

### Enforced at the chokepoint, not the call sites

`_call_claude` in each engine gained an optional `phi_map`: it redacts `system` +
`user` before egress and restores the response before returning. Redaction logic
therefore exists in **2 places, not 13**, and a newly added call site cannot forget
it. Each public function passes `phi_map=build_phi_map(patient_context)`.

Coverage — 11 of 12 invocations carry a `phi_map`:

| File | Invocations | Covered |
|---|:--:|:--:|
| `ccm_engine.py` | 6 | 6 |
| `discharge_engine.py` | 6 | 5 |
| `routes.py` (meeting minutes) | 1 | 1 |

The single uncovered call is `generate_government_case_document`
(`discharge_engine.py:363`), which takes `case_facts` — not `patient_context` — so
there is no known set of identifier values to substitute. Exact-value replacement
requires the values up front; free-form `case_facts` does not supply them. An
inline comment records this; it is an open item in §6.

### Not done deliberately

`app/services/pii_masking.py` was **not modified**. Extending it would have put the
documents path (`ai_engine.py:251`) at risk for no benefit to this fix. The new
module is additive and independent.

---

## 4. Validation evidence

### End-to-end egress interception — the decisive test

`httpx.AsyncClient.post` was monkeypatched to capture the real outbound payloads,
then `voice_to_ccm_note` and `generate_discharge_summary` were driven with PHI in
both `patient_context` and the transcript.

```
outbound Anthropic requests captured: 3   (extract → generate → critique)

PHI LEAK CHECK on the ACTUAL outbound payloads
  first name   not present
  last name    not present
  MRN          not present
  DOB          not present
  phone        not present
  RESULT: no direct identifiers in any outbound payload

clinical content still transmitted (expected — this is the product)
  metformin                  present
  blood sugars 180 to 250    present
  E11.9                      present

RETURNED note (restored for the clinician)
  CCM NOTE for Sarah Johnson (MRN 4478812, DOB 1951-03-14). Reports elevated
  glucose; metformin lapse addressed.
```

`generate_discharge_summary`: 3 outbound calls, `Marcus` / `Delgado` / `9911234` /
`1948-07-02` all absent, `CHF` present, summary restored correctly.
`generate_sdoh_assessment`: 1 outbound call, no leak.

### Redaction fidelity

```
IN : - Name: Sarah Johnson / - MRN: 4478812 / - Age: 1951-03-14
     Spoke with Mrs. Johnson today. SARAH reports blood sugars 180-250.
     Call 512-555-0143. ... Her daughter Emily drives her. Seen by Dr. Alan Reyes.

OUT: - Name: [PATIENT] / - MRN: [MRN] / - Age: [DOB]
     Spoke with Mrs. [PATIENT_LAST] today. [PATIENT_FIRST] reports blood sugars
     180-250. Call [PHONE]. ... Her daughter Emily drives her. Seen by Dr. Alan Reyes.

identifiers replaced: 6 · clinical content: all preserved
case-insensitive match confirmed (SARAH caught)
round-trip restore: OK
```

### Platform regression sweep

| Check | Result |
|---|:--:|
| App import — all 22 `safe_load` modules | `Loaded:` for each, **no `Skipped`** |
| `GET /health` | **200** |
| `GET /api/v1/case-management/info` (no token) | **403** — Critical #1 gate intact |
| `GET /api/v1/case-management/info` (authenticated) | **200** |
| `/billing/cpt-reference`, `/education/topics` (auth) | 200 |
| `GET /api/tefca/dashboard/summary` | 200 |
| `GET /api/tefca/registry/entities` | 200 |
| `GET /api/v1/tefca/connectors/status` | 200 |
| `GET /api/admin/users` | 200 |
| `POST /api/auth/login` (bad creds) | 401 |
| `GET /api/security/status` | **404** — still unmounted after the guard edit |

---

## 5. Zero-retention header — investigated, does not exist

The remediation plan asked for an `anthropic-beta` zero-retention header on all
Anthropic calls **if Anthropic supports it**. It does not.

**Zero data retention is an organisation-level configuration on the Anthropic
account, not a per-request header.** There is no `anthropic-beta` value that
requests zero retention. Confirmed against the current Anthropic API reference: the
documented `anthropic-beta` values cover features (fast mode, task budgets,
compaction, context management, files, skills, managed agents, server-side
fallback, MCP client, code execution, computer use, advisor, cache diagnosis,
mid-conversation tool changes) — none are retention-related. Retention appears only
as an org-level property, e.g. a model that "is not available under zero data
retention" returns `400 invalid_request_error` for an org whose retention
configuration does not meet its requirement.

**Therefore no header was added.** Inventing an `anthropic-beta` value would be
actively harmful — unrecognised beta values are rejected.

**This requirement is contractual.** To make the platform's zero-retention and
no-training claims true and evidenced:

1. Execute a **HIPAA Business Associate Agreement** with Anthropic covering PHI.
2. Obtain the **zero-retention / no-training addendum** and confirm it is applied
   to the production organisation and API key.
3. File both as ATO evidence and reference them here.
4. Only then correct and mount `app/api/security.py` (see §6).

Until step 3 is complete, **DP-02 remains open** for clinical narrative content.

---

## 6. Residual risk and open items

| # | Item | Note |
|---|---|---|
| 1 | **Clinical narrative still sent to Anthropic** | Symptoms, diagnoses, medications, lab values and the raw transcript are transmitted in full. This is PHI under HIPAA even with the name removed, and it cannot be masked without destroying the feature. **Requires the BAA in §5. DP-02 is not closed.** |
| 2 | **Third-party names not removed** | Relatives (`her daughter Emily`), providers (`Dr. Alan Reyes`), facilities (`Austin Regional Clinic`) — not in `patient_context`, so exact-value replacement cannot see them. Would need NER, which is heuristic and can mangle clinical text. |
| 3 | **Format variants not removed** | A DOB stored `1951-03-14` but dictated "March 14th 1951" is not matched. Exact-value replacement only covers the stored form. |
| 4 | **Known over-redaction** | Matching is case-insensitive, so a surname colliding with clinical vocabulary is also replaced clinically — a patient named Stone turns `kidney stone` into `kidney [PATIENT_LAST]`, which can make the note subtly wrong. Also affects Rash, Long, Short, Gray, Bell, Cross, Marsh, Back, Head. **Accepted deliberately:** over-redaction is visible at the mandatory `requires_review` gate; a PHI leak is invisible and irreversible. The module documents how to switch names to case-sensitive if a deployment prefers the opposite trade-off. |
| 5 | **`generate_government_case_document` uncovered** | Takes `case_facts`, not `patient_context` — no known values to substitute. Needs either a typed identifier field on the request model or a documented "no PHI in case_facts" contract. |
| 6 | **`api/meeting_routes.py:145` uncovered** | Sends raw `transcript_text` unmasked. Outside this fix's scope; same treatment applies if those transcripts are clinical. |
| 7 | **Engines use raw `httpx`, not the Anthropic SDK** | Both engines hand-roll `POST https://api.anthropic.com/v1/messages`. The `anthropic` SDK is already a dependency (`bulletin_intelligence/engine.py` uses `AsyncAnthropic`). Migrating would centralise retries, error typing, and future headers — but it is a refactor, deliberately not bundled into a security fix. |
| 8 | **`app/api/security.py` false claims** | Guarded with a DO-NOT-MOUNT header (§7), not deleted. |
| 9 | **Stale Railway reference** | `security.py` still describes hosting as "Railway.app on Google Cloud Platform" post-Azure. Explicitly left for a future sprint per instruction. |
| 10 | **No audit logging of PHI egress** | No `audit_logs` entry records that PHI was sent to a third party. Relates to AUDIT-READ. Worth adding: who, when, which patient, which endpoint. |

---

## 7. `app/api/security.py` — guarded, not mounted

The module was verified **NOT MOUNTED** (`/api/security/status` → 404), so its
attestations are dead code rather than a live misrepresentation. Rather than delete
it, a prominent DO-NOT-MOUNT header was added enumerating each unverified claim:

- `"pii_masking_active": True` and `"HIPAA": "BAA available, PHI/PII masking before
  AI processing"` — false as a general statement (one `mask_pii` call site
  app-wide; the clinical narrative is still transmitted).
- `"data_retention_by_ai_providers": "Zero retention — ..."` and
  `"no_training_data": True` — no code-level backing and none possible (§5);
  purely contractual, needs the BAA as evidence.
- `"provider": "Railway.app on Google Cloud Platform"` — stale post-Azure.
- SOC 2 / FedRAMP / GDPR / CCPA / state-AI-act rows — unverified.

The header also notes that before mounting, the endpoint needs **authentication** —
its own docstring currently advertises "Public endpoint — no auth required for
transparency," and an unauthenticated endpoint enumerating security posture is
itself an information-disclosure surface.

---

## 8. Rollback

No schema change, no migration, no config change, no dependency change.

```bash
cd "C:/Imran_Coding projects/DocuAction/backend"
git revert <commit-sha>
```

Reverting restores the previous behaviour exactly: `phi_map` defaults to `None`, in
which case `_call_claude` is byte-for-byte the original function. To disable the
control without reverting the commit, stop passing `phi_map` at the call sites — the
redaction path is skipped entirely when it is falsy.

---

## 9. Finding register update

`security_findings.md` row **DP-02** now reads **Partially Fixed** — direct
identifiers masked before egress; clinical narrative requires a BAA (contractual,
not code). The register also records the correction that `mask_pii` redacts 0 items
from the actual case-management prompt, so the original remediation text
("run (expanded) `mask_pii` before egress") should not be re-attempted as written.
