# Effort Summary

> Consolidated effort + score movement across the 30/60/90-day roadmap. Read-only. Effort in engineer-days (1 engineer unless noted).

## Effort by timeframe

| Timeframe | Items | Effort (days) | Scores moved |
|---|:--:|:--:|---|
| **Immediate** (this week) | 4 (IMP-001–004) | ~1 | Security (Critical→contained), Healthcare |
| **30-day** (Clusters A/B/C) | 19 | ~37–53 | Security 6.0→7.5, Healthcare 6.0→7.5, Accessibility 6.4→8.0, UX 7.2→8.0, DevOps 5.0→6.5, Performance 5.5→7.0 |
| **60-day** (Clusters D/E) | 9 | ~18–25 | Test Coverage 1.4→6.0, DevOps 6.5→7.5, Security 7.5→8.0, Healthcare 7.5→8.0 |
| **90-day** (Clusters F/G) | 10 | ~32–46 | Healthcare 8.0→8.5, Accessibility 8.0→9.0, Performance 7.0→8.0, Scalability 4.5→7.0, Compliance → audit-ready |
| **TOTAL** | **42** | **~88–126 days** | overall 5.8 → ~8.0 |

## Midpoint estimate
**~90–105 engineer-days** (≈ **4.5–5 months for 1 engineer**, or **~2–2.5 months for a 2-person team**). The immediate + Cluster-A work (the Critical/High and HIPAA blockers) is **~10–15 days** — the highest-ROI slice.

## Effort by category
| Category | Effort (days) | Share |
|---|:--:|:--:|
| Testing (D) | ~12–17 | ~15% |
| Compliance hardening (F) | ~20–28 | ~23% |
| Accessibility/Design System (B) | ~17–24 | ~20% |
| Scale prep (G) | ~12–18 | ~14% |
| Infrastructure (C) | ~11–17 | ~13% |
| PHI protection (A) | ~10–14 | ~12% |
| Governance (E) | ~6–8 | ~7% |

## The leverage table — cheapest fixes, biggest multi-score movement
| Fix | Effort | Scores moved |
|---|:--:|---|
| **Gate/unmount case_management** (IMP-001) | **0.5–2h** | Security (Critical), Healthcare |
| **Page titles on all routes** (IMP-012) | 0.5–1d | Accessibility (a whole Level-A criterion) |
| **Redis layer** (IMP-018) | 3–5d | **Performance + Security + DevOps** (3 scores) |
| **Hex→token migration** (IMP-014) | 4–6d | **Design System + Accessibility + Dark Mode** (3 scores) |
| **CI test gate** (IMP-027) | 1–2d | Test Coverage + de-risks **all** future change |
| **Audit immutability** (IMP-008) | 2–3d | **Healthcare + Compliance** |

## Estimated investment
At a blended ~$1,000/engineer-day, **~$90K–$126K** of engineering effort takes the platform from **5.8 → ~8.0** and clears every Critical/High finding. The **contain-the-Critical slice (~$10K–$15K)** removes the single largest compliance/security liability in the first two weeks. This excludes the BAA (legal/process) and any Azure tier upgrades (HA Postgres, autoscale) which are operating-cost, not engineering.

## Sequencing principle
**Contain (week 1) → de-risk (30-day A/C) → verify (60-day D/E) → harden (90-day F/G).** Never wait for the test suite to close the Critical; never invest in 90-day polish before the test/governance gate exists to protect it.
