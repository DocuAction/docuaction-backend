# Authoritative Source Connector — Dependency Matrix

**Contract:** 7571MN26F80064 · **Last verified:** 2026-08-01

What each verification source needs before it can contribute to a
classification, and how it affects scoring while it cannot.

---

## Matrix

| Source | Status | Prerequisites | Scoring Impact |
|--------|--------|---------------|----------------|
| **NPPES** | Operational | None — free, key-less public API | **Included** |
| **PECOS** | Operational | None — free, key-less | **Included** |
| **OIG LEIE** | Operational | None — free public CSV | **Included** |
| **SAM.gov** | Not Operational | API key required (free at api.data.gov). Also keyed on **UEI**, which the registry does not hold | **Excluded** until key *and* UEI available |
| **State Registries** | Not Implemented | Connector development. ~50 separate registries, no common API | **Excluded** |
| **IRS TEOS / EDGAR** | Not Implemented | Connector development. Keyed on **EIN**, which the registry does not hold | **Excluded** |
| **TEFCA entity data** | Not Operational | Pending(entity data provided by ONC)with The ONC | **Excluded** |

---

## What the statuses mean

**Operational** — queried on every verification. Contributes to coverage and to
the B1–B4 classification.

**Not Operational** — the connector code exists but a prerequisite is missing
(a credential, an identifier). Reported as `not_checked` with the reason.

**Not Implemented** — no connector has been written. Also reported as
`not_checked` with the reason.

**Excluded** means the source is not counted in confidence scoring — it neither
helps nor hurts. This is deliberate, and it is the single most important
behaviour in this table.

---

## Why "excluded" rather than "counted as missing"

Two failure modes were specifically avoided:

**1. An unbuilt connector must not look like a failing one.**
If `state_registry` and `irs` were counted as available-but-missing, every
verification would report permanently degraded coverage. No entity could ever
reach full coverage no matter how healthy the live sources were, which makes the
platform look broken rather than incomplete. Coverage is therefore measured
against `IMPLEMENTED_SOURCES` (`nppes`, `pecos`, `oig_leie`) and unimplemented
sources are listed separately in the response.

**2. An outage must not look like a finding against a provider.**
The five verification states are kept distinct end to end:

| State | Meaning | Counts against the entity? |
|-------|---------|---------------------------|
| `verified` | Source reached, entity confirmed | — (positive) |
| `not_found` | Source reached, **no record** | **Yes** — a real finding |
| `unavailable` | Source **could not be reached** | No |
| `not_checked` | Source not queried (no connector / no credential) | No |
| `failed` | Query errored (timeout, malformed response) | No |

`not_found` is a statement about the entity. `unavailable` is a statement about
a third party's uptime. Collapsing them would turn someone else's bad minute
into an accusation against a provider.

Note the deliberate wording for unbuilt connectors: **`not_checked`, never
`unavailable`.** "Unavailable" implies a source that normally answers is
temporarily down and will recover — it invites a retry. "Not implemented" needs
a decision, not a retry.

---

## Unblocking each excluded source

### SAM.gov — cheapest to fix
1. Register free at **api.data.gov** for an API key.
2. Set `SAM_GOV_API_KEY` in the App Service configuration.
3. **Still blocked after that:** SAM.gov is keyed on UEI and the registry stores
   NPI/CCN/CLIA/TEFCAID/HCID — no UEI. Either capture UEI at import (the
   identifier table already supports arbitrary types) or the connector cannot
   match. The key alone is necessary but not sufficient.

### TEFCA entity data
Blocked externally on(entity data provided by ONC)with The ONC. No engineering
work available until access is granted.

### IRS
Data is free and reachable today — the ProPublica Nonprofit Explorer API wraps
IRS Form 990 (no key), and the IRS Exempt Organizations Business Master File is
a public bulk download. The blocker is the **identifier**: IRS is keyed on EIN,
which the registry never captures.

Three routes, strongest first:
1. Capture EIN at import (`EIN` column → identifier type `ein`; the model
   already lists `ein`/`tin` as expected types).
2. Bridge **CCN → EIN** via CMS hospital cost reports (HCRIS), which carry both
   on one record. Deterministic, but hospitals only.
3. Name + state fuzzy match against ProPublica — probabilistic, and a wrong
   match is worse than no match, so it would need lower weight and a recorded
   match score.

One correctness trap if IRS is ever built: **IRS exempt data contains only
nonprofits.** A for-profit provider is legitimately absent, so "not found" must
map to *not applicable* — excluded from the divisor — or the connector
systematically penalises for-profit entities for being for-profit.

### State Registries
~50 registries with no common API, no shared schema, and varying access rules.
The highest-effort, lowest-return item on this list. Worth scoping per-state by
review volume rather than attempting national coverage.

---

## Current effective coverage

With three of seven sources operational, a fully successful verification reports
**3 of 3 implemented sources checked**, and separately discloses that
`sam_gov`, `state_registry` and `irs` are not implemented. Reports carry the
same disclosure in their mandatory limitations section, so a reader is never
left to infer coverage that was not achieved.
