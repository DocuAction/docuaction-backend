# Human-in-the-loop and AI/LLM boundary

## Where the system stops

```
SOURCE DATA → INDEPENDENT EVIDENCE → SYSTEM EVIDENCE ASSESSMENT ║ ANALYST REVIEW → ANALYST DETERMINATION → INDEPENDENT QA → CONTRACTUAL CLASSIFICATION
                                                                ║
                                                     the engine stops here
```

Enforced in code and tests:

- `requires_human_review` is `True` on every comparison and every assessment and is not a parameter.
- The assessment vocabulary is closed and guarded at import; forbidden terms raise.
- Explanation templates end with "Human review required." and are tested against a banned-claims list (compliance, fraud, legal identity, current operation, credentialing, analyst decision, QA approval, moved/renamed/closed).
- A prior human determination is echoed (`prior_decision_exists`, `prior_review_reference`) and provably does not change the assessment or its basis.
- No route, job, report or UI reads the assessment; nothing can act on it.

## What a person does that the system cannot

Decides whether a conflict matters under the methodology; weighs evidence the system cannot see (correspondence, site knowledge); records the determination in a contractual category; cites evidence in the record; returns a determination in QA. The system's role is to make the evidence, its provenance and its gaps visible and consistent.

## Where a person must approve before the system changes

Field mapping for any delivered layout (IQVIA); enabling any source (data-rights status DOCUMENTED required); any persistence of restricted content; any external call; any change to explanation templates or rules versions (versioned and reviewed like code).

## AI / LLM boundary

No language model is used anywhere in this capability, and none is added tonight. Specifically:

- Explanations are templates with facts filled from observations; a missing fact renders "unstated". No generated prose.
- Normalisation is deterministic string processing; no embeddings, no fuzzy or learned similarity, no "likely the same organisation".
- Matching is exact or normalised-exact; ambiguity is reported, not resolved.

If an LLM is ever proposed (e.g. to draft an analyst note from the evidence), the boundary would be: it may summarise what the engine already says, with every sentence traceable to a comparison id; it may never introduce a claim absent from the observations, never assign an assessment, never touch a category, and its output is labelled as a draft for the analyst. That proposal does not exist and requires a program-methodology decision first.
