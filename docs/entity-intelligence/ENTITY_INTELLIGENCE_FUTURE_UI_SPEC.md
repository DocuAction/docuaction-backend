# Future UI specification — "Independent evidence" panel (NOT BUILT)

No frontend code exists or is planned for this sprint. This spec exists so a later sprint builds the panel against the engine's real outputs instead of inventing new semantics.

## Placement

A collapsible panel inside the existing analyst entity review page, below the delivered record and above the determination controls. Hidden entirely unless `ENTITY_INTELLIGENCE_ENABLED` is true for the environment **and** the user's role is permitted to see evidence (existing RBAC roles; no new role).

## Header

- Label: **System evidence assessment** with the value as a neutral chip (no green/red semantics for CORROBORATES/CONFLICT — use the design-system "informational" tone for every value; conflict is a reason to look, not a colour-coded failure).
- Sub-line: "Not a determination. Not a contractual category. Human review required." (verbatim from the engine note; never editable).
- Run metadata: run id, service/rules versions, status (`COMPLETED` / `COMPLETED_WITH_UNAVAILABLE_SOURCES`), timestamp.

## Section 1 — Comparisons (per dimension)

One row per `ComparisonResult`: dimension · source · signal (as text, not icon) · explanation (template text, verbatim) · "show evidence" expander listing the matched/candidate observations with value, role, source date, authority class, provenance (edition, file hash prefix, record reference). Conflicts are never collapsed by default.

## Section 2 — What changed

One row per `HistoricalDelta` where `delta_type != UNCHANGED`, grouped by scope: **Delivered value changed** · **Evidence changed** · **Source edition changed**. Wording verbatim from the engine. Unchanged rows available behind "show unchanged".

## Section 3 — Open questions

The engine's `open_questions[]`, as a plain list. These are prompts for the analyst, not tasks, and not required fields.

## Section 4 — Prior decision

If `prior_decision_exists`: "A prior review is recorded: <reference id>, <date>" with a link to the existing record. Never the outcome as a banner; never pre-fills anything.

## Source attribution strip

Per source shown: owner, delivery path ("RCE-provided", "CMS public file", …), dataset edition and date, data-rights class. Sources whose `attribution_required` is true render their required attribution here.

## Prohibited affordances

- No button that writes a determination from the assessment.
- No "accept evidence" / "apply" action.
- No score, percentage, gauge or confidence bar.
- No colour mapping assessment → category colours used elsewhere in the app.
- No edit of explanation text.

## Accessibility and theming

Follows the enterprise design tokens and light/controlled-dark policy already adopted; every state is conveyed in text. Government branding remains OFF by default.

## Learning Center

A new module ("Reading independent evidence") is required before the panel ships, per the feature→LMS traceability rule. Out of scope tonight; LMS frozen at 1.2.0.
