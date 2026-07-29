# Developer guide

## Adding a project

Copy `config/projects/docuaction.json`, change `name`, `targets` and `gate_policy`.
Nothing else is required. That is the whole portability story - a new AGT application
is a config file, not a code change.

## Adding a DAST test

Test modules take an `APISecurityTester` (live) or a `StaticTester` (source / local
DB) and emit `Evidence` via `generate_evidence` / `record`. Register the module in
`dast/runner.py` (`SUITES`) or `dast/phase2_runner.py`.

Outcomes carry meaning - use them precisely:

| Outcome | Use when |
|---|---|
| `PASS` | The control demonstrably behaved correctly |
| `FAIL` | A weakness was demonstrated |
| `WARN` | Suspicious, not conclusive |
| `SKIP` | Preconditions absent (no credentials, route 404, our own 429) |
| `STUB` | Implemented but never executed |
| `ERROR` | The test itself failed |

**A 404, a 401, or a self-inflicted 429 is a SKIP, not a PASS.** If a payload never
reached the parser, you learned nothing about the parser. This distinction accounted
for the majority of false positives caught during development.

## Lessons paid for already

- Resolve module-local auth wrappers before flagging an endpoint unauthenticated
  (`require_ats_access` wraps `get_current_user`) - 37 false positives.
- Reflection is only XSS if the response is rendered as markup; JSON echo is not.
- Normalise volatile fields (`request_id`, UUIDs) before comparing two response bodies.
- FHIR resource names must be matched case-sensitively; `Coverage` collides with the
  ordinary English word.
- `_rehydrate` must not inherit the base scan's `started_at`, or a consolidated scan
  sorts as older than the one it replaced.
