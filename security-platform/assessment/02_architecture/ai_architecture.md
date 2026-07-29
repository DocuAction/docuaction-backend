# AI Architecture Review (Section 2J)

DocuAction is an AI-heavy platform: **~15 backend modules call LLMs**. This review is source-code-only.

## AI integrations & models

| Provider | Models (by frequency in code) | Used for |
|---|---|---|
| **Anthropic Claude** | `claude-sonnet-4-20250514` (primary, 23×), `claude-haiku-4-5` / `-4-5-20251001` (9+6×), plus refs to sonnet-4-5/4-6, opus-4-6 | classification, extraction, summarization, briefing generation, case-management engines, migration mapping, decision intel |
| **OpenAI** | `whisper-1` (8×) audio transcription; `gpt-4o-mini` (1×) | meeting/audio transcription; one chat path |

Model IDs are largely **hardcoded** across modules (with `ANTHROPIC_SONNET_MODEL` config in a couple of places) → **model-version drift risk** (some references are to non-standard IDs like `claude-sonnet-4-6`/`opus-4-6` that should be verified against the current Anthropic model list).

## Request handling
- **Client:** `httpx` (async) directly to `api.anthropic.com/v1/messages` and OpenAI endpoints.
- **Timeouts:** present and varied (15–180s across call sites; `httpx.Timeout(15/30)`, `timeout=20/120/180`). No single global standard.
- **Retries:** `tenacity` used in TEFCA connectors; not uniformly applied to all AI calls.
- **Context sizing / token budgeting:** no centralized token-budgeting or truncation layer observed — each caller assembles its own prompt; large documents/entities may be sent wholesale. **Cost + context-limit risk.**
- **Prompt management:** prompts are **inline string literals** in engine modules (no central prompt registry/versioning).

## AI security

| Concern | Observation |
|---|---|
| **PHI sent to AI?** | **Likely yes, unminimized.** Document-intelligence, case-management (patients/discharge/care-plans), and healthcare-claims engines pass domain text to Claude; audio (potential PHI) goes to Whisper. No evidence of PHI redaction/de-identification before the AI call. **This is the single most important AI-compliance finding.** |
| **Output validation** | Mixed. TEFCA verification is **deterministic (non-AI)** — good. AI outputs elsewhere (extraction/classification) are stored; validation rigor varies by module; `pandera` is used for migration dataframe validation. |
| **Prompt injection** | **Present risk** — user-supplied document/entity/email text flows into prompts across modules; no dedicated prompt-injection guardrails observed. |
| **Auditability** | AI *decisions* in TEFCA are auditable (deterministic + audit log). AI calls in other modules are not consistently audited (no per-call AI audit record observed). |
| **Human-in-the-loop** | TEFCA has explicit human review (analyst queue, reviewer roles). AI-generated content in bulletin/case-mgmt/migration is more automated — HITL varies. |
| **Hallucination mitigation** | TEFCA findings are rule-based (no hallucination surface). AI-generated summaries/briefings are not independently verified. |

## AI compliance (HIPAA / TEFCA)
- **BAA:** A HIPAA Business Associate Agreement with Anthropic/OpenAI is **required** if PHI is sent. Not verifiable from code — **must be confirmed** (compliance gap to flag in Part 10). Anthropic/OpenAI both offer BAAs on eligible tiers; usage must be on a BAA-covered account with zero-retention where applicable.
- **TEFCA:** The TEFCA **verification engine is deterministic**, so its compliance decisions are explainable/auditable (strong). AI is used around the edges (not for the compliance verdict), which is the right design.
- **Overridability:** TEFCA decisions are human-overridable (analyst workflow). AI outputs elsewhere are editable by users.

## Recommendations (documented only)
1. **PHI minimization/de-identification layer** before any AI call from PHI-bearing modules (Healthcare, Case Mgmt, Documents, Audio). Highest priority AI item.
2. **Confirm/execute BAAs** and configure **zero-data-retention** on AI accounts.
3. **Centralize** model IDs (config), prompts (registry/versioning), timeouts, retries, and add **per-call AI audit logging** + token-budget guards.
4. Add **prompt-injection guardrails** (input framing, output constraints) for user-text-in-prompt paths.
5. Verify the non-standard model IDs (`claude-sonnet-4-6`, `opus-4-6`) resolve to real, intended models.
