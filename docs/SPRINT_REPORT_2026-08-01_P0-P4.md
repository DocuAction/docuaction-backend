# Sprint Report — P0 through P4

**Contract:** 7571MN26F80064 · **Date:** 2026-08-01 · **Gate:** tests pass + /health 200 — met at every deploy.

---

## Headline

| Metric | Before | After |
|--------|--------|-------|
| Backend tests | 245 | **279** (0 failed, 9 skipped) |
| Security score | 81.4 | **83.3** |
| Critical findings | 0 | **0** |
| High findings | 5 | **3** |
| Real NPIs verifying B1 | 2 of 5 | **5 of 5** |
| Deployments | — | dev + prod backend, both SWAs |

---

## TEFCA ARC readiness

| | Status |
|---|---|
| **Task 2** (registry, import, verification) | Operational |
| **Task 3** (rules, sampling, weekly reporting) | Operational |
| **Task 4** (quarterly) | Operational — per-week trend series added |
| **Task 5** (priority review) | Operational |
| **Monday regression test** | **PASS** — 27 cases, `tests/test_monday_workflow.py` |
| **Real NPIs verified** | **5 of 5** classify B1 with 3/3 coverage |
| **Connector matrix** | Documented — `docs/CONNECTOR_DEPENDENCY_MATRIX.md` |

---

## P0 — complete

### P0.1 Real hospital NPIs — 5 of 5 now verify B1

Three supplied numbers failed the CMS check digit outright, so they could not
have been real NPIs and NPPES had no record of them. Resolved all five from the
live NPPES registry (`enumeration_type=NPI-2`):

| | Was | Now |
|---|---|---|
| Johns Hopkins | 1316966918 ✗ | **1477978807** |
| Mayo Clinic | 1063626960 ✗ | **1881018208** |
| Cleveland Clinic | 1649278978 ✗ | **1275791162** |
| Mass General | 1982604013 ✓ | 1821141649 |
| Inova Fairfax | 1205839487 ✓ | 1770626038 |

Fixing them exposed a second problem: **the importer skips existing TEFCAIDs
rather than updating them.** Correct for real imports — an import must never
silently overwrite a curated record — but it meant a fixture seeded with a wrong
NPI kept it forever. Added `refresh_real_npis()`, updating the identifier in
place rather than delete-and-reimport, because these entities are referenced by
`review_records`, `verifications` and `sample_entities`.

All five now: `nppes=verified · pecos=verified · oig_leie=clear → B1 (RULE-001), coverage 3/3`.

### P0.2 `multiple_source_conflict` implemented
Was hardcoded `False`. Two contradictions recognised: NPPES has the provider and
PECOS does not; PECOS shows them enrolled while OIG lists them excluded. **Only
sources that answered can conflict** — if one is unavailable there is a gap, not
a disagreement, and calling that a conflict would manufacture a B3 out of
someone else's downtime.

### P0.3 Coverage now measured against connectors that exist
Counting `state_registry` and `irs` as missing sources reported permanently
degraded coverage for work that was never scheduled — full coverage was
unreachable by construction, which makes the platform look broken rather than
incomplete. Coverage is now `sources_checked / implemented_sources` (3/3), with
unimplemented connectors disclosed separately.

Deliberately `not_checked`, never `unavailable`: "unavailable" implies a source
that will recover and invites a retry; "not implemented" needs a decision.

### P0.4 Connector dependency matrix
`docs/CONNECTOR_DEPENDENCY_MATRIX.md` — per-source status, prerequisites,
scoring impact, and what unblocks each. Key finding: **SAM.gov needs a free
api.data.gov key AND a UEI the registry never captures** — the key alone is
necessary but not sufficient.

### P0.5 Monday regression test — 27 cases
`tests/test_monday_workflow.py`. Deliberately split: pure stages
(classification, sampling, report assembly, the B1..B4 sum identity) run
deterministically in-process; the HTTP surface is asserted for existence and
gating. Driving live third-party registries from a unit test would fail on a
Tuesday because CMS had a slow minute, and a suite that cries wolf gets ignored.
The file states that boundary explicitly rather than pretending to cover it.

Includes a guard against the route-shadowing bug that already happened once, and
a check that production seeding is refused.

### P0.6 Deployed dev then prod, both verified.

---

## P1 — complete except Key Vault (blocked)

**P1.1 Quarterly trend** — per-ISO-week B1–B4 series on quarterly reports. A
quarter reported as one number hides whether the rate is improving inside it.
Undated reviews are surfaced under an explicit "undated" bucket rather than
dropped, so the trend totals cannot silently disagree with the distribution.

**P1.2 Excel export** — `GET /api/tefca/arc/reports/{id}/excel`. Three sheets
(Entity Results, Summary Statistics, Limitations), AGT navy, frozen headers,
auto-filter. Built from the **archived** `report_data`, never recomputed — a
report rendering one set of numbers as HTML and another as Excel would be worse
than no export. The Limitations sheet is mandatory: a reader who opens the Excel
and not the HTML must still see the caveats.

**P1.3 `review_cycles` table** — ties sample → cycle → report so an auditor
asking "which sample backs the Q3 report" gets one row, not a reconstructed join.

**P1.4 Key Vault — BLOCKED.**
```
(Forbidden) Public network access is disabled and request is not from
a trusted service nor via an approved private link.
Vault: docuaction-kv-prod
```
Both vaults exist (`docuaction-kv-prod`, `docuaction-kv-dev`) but are firewalled
to private link. Migration requires running from an approved network or adding
this workstation's IP. Documented and skipped, per the brief.

---

## P2 — complete

**P2.1** Score **81.7 → 83.3**, Highs 5 → 3.
- Fixed the **postcss path-traversal CVE** via `npm audit fix` (4 → 2 npm vulns).
- Suppressed one verified false positive (a compliance *document generator*
  containing the Key Vault pattern as example text — same class as the guard in
  `config.py`).

**P2.2 and P2.3 were already done** in the earlier security sprint — `/costs`
removed from `PUBLIC_OK`, and `/excel` suppression is exact (`/excel-qa` is
correctly flagged again).

### The 3 remaining Highs, and why they stay

1. **`app/api/plans.py:33` — `GET /info`.** Public pricing data. Deliberately
   *not* added to the exact-public set: the rule sees only the decorator path,
   so an entry for `/info` would also whitelist
   `/api/v1/case-management/info`, which is protected and an explicit DAST
   target. Silencing a real finding to clear a cosmetic one is the wrong trade.
2 & 3. **`next` and `sharp` npm advisories.** **`npm audit fix --force` would
   DOWNGRADE Next 16.2.12 → 14.2.35** — a major *downgrade* that would break the
   App Router build entirely. There is no fixed stable release: the advisory
   range covers through 16.3.0-preview.7 and the latest stable is 16.2.12. Not
   applied. Re-check when 16.3.0 ships.

---

## P3 — partially complete

**P3.1 Bulletin frontend — VERIFIED WORKING.** The prod bundle sends
`authHeaders()` on the archive call, targets the correct API host, and carries
no dev-host leak. `/api/v1/bulletin/latest/fcc` reports 93 articles. No fix
needed.

**P3.2 Source pruning — NOT DONE.** Requires probing 276 registered sources and
deactivating dead ones. The finding from the prior sprint stands: 276 registered,
**22 active in 24h, 231 never produced**. The gap is not list size — 84% of
registered sources yield nothing, which the endpoint's own note attributes partly
to sources no collector calls. That is a wiring investigation, not a pruning job,
and it deserves its own session rather than a rushed pass at the end of this one.

**P3.3 Missing RSS — NOT NEEDED.** Verified in the prior sprint: 20 of 21
requested sources are already wired across 755 feed URLs.

---

## P4 — partially complete

**P4.3 Deployment guide — DONE.** `docs/DEPLOYMENT_GUIDE.md`, written from what
actually went wrong: the PowerShell backslash-zip outage, "status 4 is not
proof", CLI-error-is-not-failure, why dev left Oryx, `--clean` teeth, the
semgrep dependency hazard, and the `npm audit fix --force` downgrade trap.

**P4.1 N+1 queries — NOT DONE.**

**P4.2 Password rotation — NOT DONE, and I recommend you do this one manually.**
It needs new credentials written to a file and applied to live accounts. Doing
that unattended at the end of a long session risks locking out the accounts this
work depends on, and the new passwords would land in a file I cannot verify you
received. Worth ten minutes of your time rather than an automated pass.

---

## Deployments

| Target | Result |
|--------|--------|
| dev backend | deployed, verified |
| prod backend | deployed, verified |
| prod SWA | deployed |
| dev SWA | deployed |

Recorded again, because it caught us on **both** environments this session:
**status 4 + active is not proof the new code is serving.** Every deploy needed
an explicit `az webapp restart` before new behaviour appeared. Verify with an
endpoint that exists only in the new build.

---

## Remaining manual items

- [ ] Submit Task 2 to ONC (remove IQVIA, 5 edits)
- [ ] Check OpenAI BAA email
- [ ] Decide Perigon licensing ($250/mo or drop)
- [ ] **Request SAM.gov API key at api.data.gov** — free, and the cheapest
      coverage win available. Note it is necessary but not sufficient: SAM is
      keyed on UEI, which the registry does not capture.
- [ ] Rotate dev passwords (P4.2 — see above)
- [ ] Key Vault migration from an approved network (P1.4)
- [ ] Investigate why 231 of 276 bulletin sources have never produced (P3.2)
