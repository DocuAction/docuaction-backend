# Post-Certification Operational Readiness

**Classification:** INTERNAL ENGINEERING / MANAGEMENT · 2026-08-23
Commit `934c696` · Evidence `phase6-bulk-1.1.0`

---

## Part F — dataset provenance (verified, not re-ingested)

**No data was re-ingested.** The certified Area-1 lineage:

| | |
| --- | --- |
| Source filename | `onc-snapshot-20260720.csv` |
| Stored artefact SHA-256 | `689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d` |
| Intake hash | identical — re-verified |
| Schema fingerprint | `1cd655e9120dc9d0d6a52697ea470519b138fe0f9334af6f69467f3485ade3d0` |
| Source records | 23,566 · promoted 23,562 · **held 4** |
| Intake timestamp | 2026-08-21 18:05:19 by imran@agtbi.com |
| Field-map version | 1.0.0 · vocabulary 1.1 · rule `phase6-bulk-1.1.0` · triage 1.1.0 · address rules 1.0.0 |
| Content digest | `24524f70c370d6c42a2b03d5385295a5` |

### The two classifications, kept apart

**1. DEVELOPMENT DATASET IDENTITY — VERIFIED.** The user identified this file as
their ONC CSV. Its 41-column header was supplied before ingestion and hashes to
the locked schema fingerprint. Byte-for-byte lineage from the Box download to
Area 1 is proven.

**2. CONTRACTUAL DELIVERY PROVENANCE — NOT VERIFIED.** There is no documented ONC
sender, no government transmittal, and no control total. User recognition
establishes which dataset was used for development. It does not manufacture a
contractual chain of custody.

**Re-ingestion required: NO.** The Area-1 artefact is the earlier, cleaner,
hash-verified original. The later copy at `C:\ONS HHS\ONC CSV file\` is an
Excel round-trip derivative — same 23,566 records, but comma-padded and with 330
`address_text` values altered. **It must not replace the current artefact.**

---

## Part O — SAM.gov gap

| Question | Answer |
| --- | --- |
| Credential present? | **No.** `SAM_GOV_API_KEY` is not set |
| Connector ready? | Yes — applicability, lookup and disposition paths implemented |
| Applicability implemented? | Yes — `UNKNOWN_PENDING_METHODOLOGY`, blocked by D4 |
| Population requiring SAM? | **23,566** — the whole delivery |
| Disposition when unavailable | `SOURCE_UNAVAILABLE` → Layer-3 `UNAVAILABLE` → triage `SOURCE_LIMITATION` |
| Never becomes | `NO_MATCH_OBSERVED`. Asserted by `test_source_unavailable_is_not_no_match` |
| Report disclosure | Methodology §23; every report lists it as a source limitation |
| Methodology decision required | **D4** — whether unavailability affects classification or only readiness |

**To activate SAM when credentialing is available:**
1. Obtain a SAM.gov API key under the contract.
2. Store it in Key Vault as `SAM-GOV-API-KEY`; add the app-setting reference.
3. Set `SAM_GOV_API_KEY`; no code change — `sam_state()` already branches on it.
4. Re-run enrichment under a **new** rule version. Do not overwrite
   `phase6-bulk-1.1.0`; the unavailable observations are a true record of that run.
5. Obtain the D4 decision before treating any SAM result as classification-bearing.

---

## Part P — human operations checklist

### Analyst
1. Log in; confirm your role is `reviewer`.
2. Open your assigned case from the queue (highest priority, then oldest).
3. Confirm the evidence belongs to this entity **by organisation OID**.
4. Open every cited observation and its source edition.
5. Check for a methodology-pending condition — if present, **do not determine it**.
6. Record a written rationale that addresses the evidence, not the observation label.
7. Select the determination.
8. Submit to QA. **You cannot approve your own work.**

### QA reviewer
1. Log in; confirm your role is `qalead`.
2. Open the submitted case.
3. Inspect the evidence **independently** — do not read the rationale first.
4. Work the QA checklist; C3 (no unsupported conclusion), C5 (observations are not
   entities) and A1 (OID linkage) are the ones most often missed.
5. Record APPROVE, RETURN or ESCALATE with a reason.
6. Confirm reportability changed as expected — and that a RETURN withdrew it.

### Program manager
1. Queue depth and age, analyst and QA.
2. Priority-review SLA banding: `at_risk` at ≤2 days, `overdue` past due.
3. **Priority capacity against the contractual average of 20/month plus surge.**
4. Source outages and credential gaps.
5. QA backlog — QA is the only path to reportability and therefore the bottleneck.
6. Report schedule: weekly, bi-weekly, quarterly, and the 120-day retrospective.
7. Outstanding COR decisions; nothing downstream of them can complete.

---

## Part R — certification gates re-run

| Gate | Result |
| --- | --- |
| Deterministic full regression | **1,756 passed, 38 skipped, 0 failed** |
| Reconciliation | 18/18 |
| Area-1 digest | `24524f70…` unchanged |
| Source artefact hash | matches |
| Original evidence digest | `84384bcd…` unchanged (164,962 / 39,749) |
| Corrected evidence | 188,528 / 116,218 unchanged |
| Historical determinations | 43, 0 reportable, 0 decision events |
| Report reconciliation | 0 unexplained differences across 8 metrics |
| Excluded | `tests/test_bulletin.py` (8) — pre-existing live-network defect |

No engineering was changed to influence any of these numbers.
