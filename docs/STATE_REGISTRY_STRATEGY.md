# State Registry Verification — Strategy

**Contract:** 7571MN26F80064  ·  **Date:** 2026-08-02  ·  **Git SHA:** `706a2f641f3a48f3dc117f57d579ddc82dbd5686`

## Current Status: Not Implemented

No state registry connector exists. Every verification response reports
`state_registry: not_checked` with the reason "Connector not implemented".

## Approach options

| Option | Description | Effort | Cost |
|--------|-------------|--------|------|
| **A** | Individual state API integrations (50 states) | Very high — most states lack APIs; many require scraping or manual portal access, and formats differ per state | Engineering time only |
| **B** | Commercial aggregator (license verification service) | Medium — one integration, one contract | Varies by vendor and volume |
| **C** | Manual verification with documentation | Low per entity — does not scale beyond a small sample | Analyst time, linear in entity count |
| **D** | Defer until ONC provides guidance | None | None |

## Recommendation: Option D — Defer

State registry verification is not yet required by ONC for initial review
cycles. Continue to report `not_checked — connector not implemented` in all
verification responses and reports, and revisit when ONC provides specific state
verification requirements.

The reasoning is that Options A and B both require knowing **what** must be
verified against state records before the work can be scoped. Building 50
integrations, or buying an aggregator, ahead of a requirement risks verifying the
wrong attribute at material cost. Option C does not scale to the review volumes
this contract anticipates.

## Scoring impact

State registries are **EXCLUDED** from confidence scoring. They neither help nor
penalise an entity.

Coverage is measured against connectors that **exist** (NPPES, PECOS, OIG LEIE).
Counting an unbuilt connector as a missing source would report permanently
degraded coverage for work that was never scheduled — full coverage would be
unreachable by construction, which makes the platform look broken rather than
incomplete.

The status is deliberately `not_checked`, never `unavailable`. "Unavailable"
implies a source that normally answers is temporarily down and will recover,
which invites a retry; "not implemented" needs a decision.

## Disclosure

The gap is disclosed in three places, so a reader cannot miss it:
- every verification response (`state_registry: not_checked` with reason),
- the mandatory limitations section of every report,
- `docs/audit/CONNECTOR_HEALTH_MATRIX.md`.

## Action required

**This strategy should be confirmed with the ONC COR during the next review
meeting.** It is a recommendation pending government concurrence, not a decision
already taken.
