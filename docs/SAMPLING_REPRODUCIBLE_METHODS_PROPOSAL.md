# Reproducible sampling methods - PROPOSAL (AGT, not COR-accepted)

Status: draft. Pure module `app/tefca_registry/sampling_methods.py`, tests
`tests/test_sampling_methods.py`. No route, command, flag, migration or change to the
deployed official path (`qhin_sampling.py`, `sampling_engine.py`). Nothing here is wired
to runtime; wiring would be a separate flag-gated, default-OFF change after COR/AGT decide.

## Implemented vs proposal

| Item | Source | State |
|---|---|---|
| Three named methods, **no default**; call without a method refused | Task 2 leaves the method unresolved (review file 05 s.3.3, conflict 9; matrix "Sampling plan" row) | Implemented (module) |
| `overall_proportional`: one Cochran+FPC n (383 at N=94,231), largest-remainder allocation | Task 2 Sampling Plan table text | Implemented |
| `per_qhin_cochran`: own n per QHIN, census when n>=N_h (design of the deployed path) | file 05 s.3.3 | Re-expressed, deployed path untouched |
| `floor_allocation`: n_h=min(N_h,max(prop_h,m)); m must be passed explicitly (563, +/-4.87% at m=30 on Sept counts) | file 10 | Implemented; m=30 is an illustration only |
| Pinned population: file name, extract date, row count, file SHA-256, rule set + version, count excluded per rule, unit definition; row_count - excluded must equal eligible units | matrix Appendix E reproducibility rows; file 05 s.3.4 "no frame hash today" | Implemented in the evidence structure; not populated from the DB |
| Deterministic frame order (stratum, unit_id); frame hash | file 05 s.3.4 "input ordering not pinned" | Implemented |
| Seed + per-stratum derivation sha256(seed:method:stratum); selection = lowest sha256(stratum_seed:unit_id) keys (portable, no RNG-version dependence) | file 05 s.3.4 | Implemented |
| Selection manifest (unit id, stratum, order) + manifest hash; evidence hash | Appendix E | Implemented |
| Calculation evidence: z, n0, n0 used, unrounded n, rounding order, proportional trace, allocation, per-stratum worst-case margin, variance, weights, flags (n_h<2) | file 05 s.2, s.4.2 | Implemented |
| Recompute-from-evidence verifier `verify_evidence` (detects edited manifest/evidence, changed or tampered frame) | | Implemented |
| Persisting evidence (table/artifact), export route/CLI | | NOT implemented (would need a migration or artifact store; governed release step) |
| Weighted pooled estimator / CI for results | file 05 s.4.2 items 4-5 | NOT implemented; weights are recorded only |
| Replacement policy, clustering/design effect, Indeterminate handling | file 05 s.4.2 | NOT implemented |

## Rounding order
`ceil_at_end`: n0=384.16 carried unrounded through the FPC, one ceil -> 383 (N=94,231), 379 (N=24,589).
`ceil_n0_first`: n0 -> 385 first -> 384 / 380. The caller must choose; the document must state it.

## Decisions COR/AGT must make (not made here)
1. **Sampling unit** (record vs organisation vs site/location vs endpoint; Participant/Subparticipant nesting). 94,231 "unique connections" is not shown to be the same unit as delivered rows (file 05 s.1 row 1, s.4.1).
2. **Population / frame**: Q&A figure 94,231 versus the COR-delivered extract; eligibility rules (inactive records are not excluded by deployed code).
3. **Reading of "from each QHIN"** (Tasks 3, 4): (a) every QHIN represented -> single overall 383 stratified proportionally; (b) confidence within each QHIN -> per-QHIN (~2,000-class). Floor allocation (563) is AGT's compromise proposal.
4. **Minimum per QHIN** (m; 30 is illustrative) and the census rule.
5. **As-of date** of the frame and who certifies the extract file hash.
6. **Rounding order** and Z (1.96) as the Task 2 text states them.
7. Who owns the seed (recorded, not secret) and whether the seed is fixed at plan time.

## Verification
Synthetic 11-stratum frame with Sept-shaped skew (24,589 synthetic ids). Tests cover:
determinism and input-order independence, seed change, frame tampering, evidence tampering,
FPC, rounding order, largest remainder, census, floor 563 / 4.87%, refusal without method.
