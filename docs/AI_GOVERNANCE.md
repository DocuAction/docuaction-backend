# AI Governance — Entity Resolution

**Contract:** 7571MN26F80064 · **Scope:** `app/tefca_registry/entity_resolver.py`
· **Status:** AI disabled by default; system fully operational without it

This document covers the one place in the TEFCA registry pipeline where a large
language model may be consulted: adjudicating whether two entity records refer to
the same real-world organization, in the residual cases where deterministic
methods cannot decide.

It does not cover the bulletin intelligence classifier, which is a separate
subsystem with its own controls.

---

## 1. Human oversight — AI is advisory, never decisive

**A reviewer is the decision of record in every mode.** The resolver returns a
recommendation; it does not write a verdict into the registry, and no code path
allows an AI response to change an entity's status without a human acting on it.

| Mode (`AI_ENTITY_RESOLUTION`) | Behaviour |
|---|---|
| `disabled` **(default)** | No AI call is ever made. Resolution is deterministic only. |
| `advisory` | AI may be consulted on inconclusive pairs. The result **never** sets a match verdict (`is_match` stays `None`); it is context for the reviewer. |
| `production` | AI may set a proposed verdict, but `requires_manual_review` remains `True` — a human still confirms. |

An unrecognised value fails **closed** to `disabled`. A typo in an environment
variable cannot silently enable AI in a pipeline that produces compliance
evidence.

**AI is reached only as step 4 of 4.** The resolver runs, in order: exact
identifier match (NPI/TEFCAID), USPS address normalization, Jaro-Winkler name
similarity, and only then — if and only if those disagree — AI. A decisive
identifier or agreeing deterministic signals short-circuit before any call is
made. In practice the AI path is the exception, not the rule.

---

## 2. Prompt management — versioned

The system prompt is a module constant, versioned as `PROMPT_VERSION`
(currently `entity-resolution/v1`), and the version is written into every audit
record. A prompt change requires a version bump, so any historical decision can
be traced to the exact instruction set that produced it.

The prompt instructs the model to be conservative — to return low confidence
rather than guess when evidence is ambiguous — and states explicitly that its
output is a recommendation for a human reviewer.

---

## 3. Audit logging — full input and output

Every AI call produces a record containing:

| Field | Purpose |
|---|---|
| `model_id` | Which model answered |
| `prompt_version` | Which instruction set was in force |
| `input` | The exact payload sent (public fields only — see §7) |
| `output` | The raw model response |
| `confidence` | Parsed confidence, clamped to [0, 1] |
| `threshold_applied` | Which band the confidence fell into |
| `timestamp` | UTC, ISO-8601 |
| `latency_ms` | Measured round-trip |
| `software_version` | Build that made the call |
| `error` | Populated when the call failed |

**Failed calls are logged too.** An outage is recorded with its error rather than
silently omitted, so the trail shows what was attempted, not only what succeeded.

### Confidence thresholds

| Confidence | Disposition |
|---|---|
| ≥ 0.95 | Recommendation surfaced to the reviewer |
| 0.70 – 0.94 | Mandatory manual review; recommendation shown as context only |
| < 0.70 | **Discarded entirely** — not shown, not recorded as a finding |

Below 0.70 the recommendation is dropped rather than downgraded. A
low-confidence guess must not reach a reviewer wearing the appearance of
evidence; the audit record still captures that the call was made and what it
returned.

---

## 4. Periodic validation — monthly accuracy review

Sample AI-consulted resolutions monthly and compare each recommendation against
the reviewer's final decision. Record agreement rate, false-positive rate
(AI proposed a match the reviewer rejected), and false-negative rate. Because
every call is audit-logged with its input, the review set is reconstructable
without additional instrumentation.

A material drop in agreement is grounds for reverting `AI_ENTITY_RESOLUTION` to
`disabled` pending investigation. That reversion is a single environment
variable and requires no code change or deployment.

---

## 5. Performance monitoring

`latency_ms` and `error` on every record support monitoring without extra
plumbing. The metrics that matter: call volume (how often the deterministic
steps fail to decide), p50/p95 latency, error rate, and the distribution across
the three confidence bands. A rising share of sub-0.70 responses indicates the
model is being asked questions it cannot answer — a prompt or scoping problem,
not a capability one.

---

## 6. Fallback — the system works fully without AI

The default configuration performs **no AI calls at all**, and this is the tested
path. If the provider is unreachable, misconfigured, returns unparseable output,
or is switched off mid-run, the resolver logs the failure and returns the
deterministic result. There is no code path in which an AI problem blocks or
fails a verification run.

No SDK is installed. The resolver depends on an injected client satisfying a
two-method interface, so the dependency set is unchanged by this feature. This
is deliberate: `DEPLOYMENT_GUIDE.md:143-149` records an incident in which
installing a single package moved 11 pinned dependencies including
`fastapi 0.140.13 → 0.115.0` — the exact boundary where auth failures return 403
instead of 401 — and invalidated a full test run. Adding an SDK is a separate,
independently verified change.

Jaro-Winkler is implemented locally for the same reason; it adds no dependency
and is deterministic under test.

---

## 7. Data controls — public data only, never PHI

Outbound payloads are built from an **allowlist**, not a blocklist:

```
PUBLIC_FIELDS = ("name", "address", "npi", "entity_type", "state", "tefcaid")
```

Any field not on that list is dropped before the payload is constructed. A new
column added to the entity model later — including one carrying PHI — cannot
leak by default, because inclusion is opt-in rather than exclusion opt-out.

**Never sent:** protected health information, patient data, SSNs, dates of
birth, clinical codes, or any free-text field that could carry them.

NPI is included deliberately: it is a provider identifier, publicly searchable
in NPPES, and is not patient PHI (consistent with the disposition recorded in
`SPRINT_REPORT_2026-07-31.md:128-129`).

The allowlist is enforced in `_public_payload()` and covered by
`tests/test_entity_resolver.py::test_audit_record_is_complete_and_carries_only_public_data`,
which asserts that seeded PHI-shaped fields appear in neither the outbound prompt
nor the audit record.

---

## Outstanding

- **No Business Associate Agreement is in place with any AI vendor.** This is an
  open item in `SESSION_STATE.md`. Until one exists, `AI_ENTITY_RESOLUTION`
  should remain `disabled` in any environment handling real data, regardless of
  the public-data-only control above.
- Monthly validation is defined here but not yet scheduled.
- The threshold values (0.95 / 0.70) are initial settings and should be tuned
  against observed reviewer agreement once volume permits.
