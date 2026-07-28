# Immediate Actions (this week)

> Before the next HHS/ONC interaction involving live PHI. These contain the Critical and rotate exposed credentials. Read-only recommendations — **no fixes applied.** All belong to **Root-Cause Cluster A (PHI Protection)**.

| ID | Action | File(s) | Effort | Scores improved | Why now |
|---|---|---|---|---|---|
| **IMP-001** | **Gate or unmount the Case Management router** — either add `dependencies=[Depends(require_role(...)), <module gate>]` to `cm_router` (`case_management/routes.py:34`), or comment out `safe_load("app.case_management", …)` (`main.py:321`) until it's hardened | `case_management/routes.py:34`, `main.py:321` | 0.5–2h | Security (Critical→closed), Healthcare | The only live, internet-reachable Critical (unauthenticated PHI endpoints) |
| **IMP-002** | **Rotate the exposed API keys** (Anthropic + OpenAI) and DB credential; move real values to Key Vault; keep only placeholders in local `.env` | `.env:2-5` (gitignored, not in git history) | 2h + rotation | Security (High) | Live keys have sat in a working-tree file; treat as potentially exposed |
| **IMP-003** | **Stop unauthenticated + unmasked PHI egress to Anthropic** — require auth on the case-management AI endpoints and run PHI through masking before any external call; **do not send PHI until a BAA is signed** | `case_management/services/ccm_engine.py:25,164`, `discharge_engine.py:19,33` | 2–8h | Security (High), Healthcare, HIPAA | Anonymous callers can currently drive PHI to a third party |
| **IMP-004** | **Route the case-management `voice-to-note` upload through `FileScanner`** + auth | `case_management/routes.py:219` | 1–2h | Security | Unauthenticated upload bypasses the scanner |

## Sequencing
1. **IMP-001 first** (fastest, largest risk reduction) — if the module isn't needed this week, unmounting it is a 0.5h change that closes the Critical outright.
2. **IMP-002** in parallel (independent).
3. **IMP-003 / IMP-004** as the "keep it, but safely" path if Case Management must stay live — these are the durable fixes that let it remain wired.

## Total immediate effort: **~1 engineer-day** (0.5–1.5 days incl. key rotation + testing)

## Guardrail
These are **contain-the-bleeding** actions. The full PHI-protection hardening (expanded name/address masking, BAA process, audit immutability, DB TLS) is the **30-day Cluster A** work in `30_day_plan.md`. Do **not** treat IMP-001 as "done with PHI" — it stops the immediate exposure; the compliance posture still requires the 30-day cluster.

## Production impact of these recommendations: **ZERO** (documented only; the team decides whether/when to apply).
