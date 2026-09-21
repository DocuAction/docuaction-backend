# September 2026 ONC snapshot — reconciliation and remediation record

**Status:** evidence gate PASSED (raw-file reconciliation, 2026-09-20); code
remediation on branch `feat/september-2026-onc-snapshot`, rule set 1.3.0,
migration `20260921_september_snapshot`.
**Scope:** numbers and decisions only. No delivered organisation name, OID
list, NPI or address appears in this document; the raw files stay in the
operator's read-only download folder and the evidence folder
(`qa-evidence/september-2026/`, git-ignored).

## 1. Sources

| File | Delimiter | Records | Role |
|---|---|---|---|
| July 20, 2026 snapshot | pipe | 23,566 | The official DEV delivery (ONC-ASTP-2026-08-21); SHA-256 matches `docs/rce/p0_profile_20260720.json` |
| September 2, 2026 snapshot | comma, RFC-4180 quoted | 24,589 | The new monthly file |
| September new-entrants extract | comma | 1,035 | Vendor-supplied list of first-time ids |

All three SHA-256 hashes were recorded before and after reconciliation and
are unchanged (`SOURCE_MANIFEST.json`, `all_sources_unchanged = true`).
The field set is identical across the three files (41 fields, same
schema fingerprint) — the deliveries are comparable.

## 2. The 12-record discrepancy

The expected arithmetic `23,566 + 1,035 − 24 = 24,577` did not match the
delivered 24,589. Set arithmetic on the `id` column (unique in every file:
no duplicate ids in July, September or the extract) resolves it:

| Quantity | Value |
|---|---|
| Distinct ids, July | 23,566 |
| Distinct ids, September | 24,589 |
| Added (in September, not in July) | **1,035** — exactly the new-entrants extract; none of them appears in July |
| Removed (in July, not in September) | **12**, not 24 |
| Common | 23,554 |
| Identity | 23,566 + 1,035 − 12 = 24,589 ✔ |

The 12 removed records are all active Subparticipants under one QHIN with
contiguous OIDs (one isolated OID and a run of eleven consecutive OIDs), which
reads as a single organisational withdrawal rather than twelve independent
events. The "24" in the expected formula was not supported by either file.

Of the 23,554 common ids, 9,031 changed in at least one field and 14,523 are
byte-identical. Field-level change counts (common ids): contact postal code
6,978; address postal code 1,635; purposesofuse 509; NPI 360; active 290
(274 `1→0`, 16 `0→1`); partOf 230; doa 99; organizationNodeType 16.

## 3. The six September changes — verified against the raw files

| # | Change | Observed | Decision in code |
|---|---|---|---|
| A | `active` | Raw values are only `"0"`/`"1"` in both files (inactive 972 → 1,253; 23 inactive among the 1,035 new entrants). The float forms `0.0`/`1.0` come from spreadsheet round-trips, not the RCE. | `normalize_active` accepts 0/1/0.0/1.0; CON-003 holds empty or unsupported values (HIGH); ACT-001 holds an inactive first-time id (HIGH) for analyst confirmation. Raw kept as `active_raw`. |
| B | `organizationNodeType` | September populates 50 rows: no-node 22, initiating-node 21, passthrough-node 7 (July: initiating-node only). | Vocabulary of three; CON-004 holds an unlisted value (HIGH); the field is never hierarchy. |
| C | NAIC / SO-2 | Exactly one `hl7orgrole = payer` record; its NAIC is delivered as a 5-digit text code with a leading zero, integer format. No payer lacks a NAIC. | SO-002: payer without NAIC holds (HIGH); a `NNNN.0` artefact normalises with raw kept; a leading zero is preserved (text, never an integer). The pipeline does not invent `4918`. |
| D | `partOf` `.300 → .700` | 228 Subparticipants moved; `orgManagingOrg` unchanged on all 228 (same QHIN); both Participants exist in July and September, both active; `.300` keeps 19 inactive children. Two further same-QHIN partOf moves. | Promotion pass 2 is now a reconciliation: the old edge gets `end_date = delivery received date`, `status = historical`; the new edge starts at that boundary; both are recorded in `tefca_relationship_observations` (SUPERSEDED / ASSERTED). Cross-QHIN moves, unresolved parents and out-of-order snapshots are refused and recorded. |
| E | `purposesofuse` | 509 substantive changes; 11 distinct tokens; comma-separated inside a quoted cell; two rows carry 9 tokens. | Reader honours RFC-4180 quoting; PUR-001 holds an unlisted token (HIGH); PUR-002 flags a malformed list (MEDIUM) and reports separator normalisation (INFO). |
| F | `doa` | Populated 105 → 205; one OID is cited by 158 rows and is itself a September record (a Participant), so it resolves inside the TEFCA namespace. | DOA-001 checks OID syntax; DOA-002 resolves against the delivery, the registry and the QHIN list, else `EXTERNAL_REFERENCE_UNVERIFIED` (MEDIUM, human). Never treated as a relationship. |
| G | additions / removals | See §2. | `rce_delivery_delta` persists NEW / CHANGED / UNCHANGED / NOT_PRESENT per id; `rce_entity_presence` records ABSENT for the 12 without deactivating them; `arc_stale_marks` flags ARC results whose entity had a MATERIAL change (partOf, QHIN, active, purposes, NPI, CCN) or is absent. Postal-code-only changes mark nothing. |

Also observed: 3 NPI values that are not 10 digits (one test artefact, one
9-digit value, one cell carrying two NPIs) — caught by the existing NPI-002/
NPI-004 rules; the `id` column is 1:1 in every file (SCH-003 now guards it).

## 4. What did NOT change

* Area 1 semantics: every delivered line is stored verbatim (`raw_line`),
  the quote-aware split only changes `parsed`.
* Historical deliveries, ARC results and report artefacts are never rewritten;
  staleness is a mark, resolution is an appended row.
* No TEFCA fact is inferred from IQVIA, NPPES, CCN, names, addresses or
  phones (`source_matching.assert_not_tefca_fact`).

## 5. Migration and runtime impact

* `20260921_september_snapshot` (down: `20260918_pp_verification`): seven new
  tables and one view, SELECT+INSERT grants to the app role, and `UPDATE
  (end_date, status)` on `tefca_entity_relationships` only. No ALTER of a
  runtime-owned table. Validated upgrade/downgrade/upgrade on the isolated
  PostgreSQL.
* Worker: pass 2 of promotion performs the supersession; snapshot effects
  run after promotion inside the existing MATCHING event (no new stage name;
  the stage-event CHECK is untouched). A failure there is recorded and does
  not undo the promotion; the effects are idempotent.
* Rollback target for the code: the merge base of the branch. Rollback of
  the migration: `alembic downgrade 20260918_pp_verification` (drops only the
  new tables; ended relationship rows keep their `end_date`).

## 6. Test evidence

`tests/test_september_snapshot.py` (32) and `tests/test_release1_foundation.py`
(10) on the isolated PostgreSQL; targeted regression over the 41 pipeline
modules: only the 8 pre-existing DB-only failures in
`test_automated_verification*.py` remain (baseline on `main`).
