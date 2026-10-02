# Observability, failure and recovery model

Principle: **no silent partial success**. Every stage emits an explicit status; a downstream consumer that sees no status treats the stage as not run.

## Statuses that exist in code today

| Stage | Status field | Values | Where |
|---|---|---|---|
| Parse (per file) | `ParseReport.status` | OK · PARTIAL · FAILED, plus `stopped_at_line`, `rows_read/ok/malformed/skipped`, `header_note`, `notes[]` | `nppes_v2/parser.py` |
| Intake inspection | `ArchiveInspection.accepted` + `refusals[]`; `CsvLimitReport.accepted` + `refusals[]` + `stopped_at_line` | boolean with reasons | `intake_safety.py` |
| Feature gate | `FeatureDisabled` exception naming the boundary | raised, never swallowed | `flags.py` |
| Schema mapping (IQVIA) | `SchemaUnknown` exception; `AdapterStatus.AWAITING_SCHEMA` | raised | `iqvia_onekey/adapter.py` |
| Evaluation | `EntityIntelligenceRun.status` | COMPLETED · COMPLETED_WITH_UNAVAILABLE_SOURCES | `service.py` |
| Assessment | `SystemEvidenceAssessment` incl. SOURCE_UNAVAILABLE and INSUFFICIENT_EVIDENCE | closed vocabulary | `assessment.py` |
| Migration | exit code 2 with "refusing" for non-local URLs | fail closed | `migrations/apply.py` |

## Statuses designed, not built (acquisition job)

SUCCEEDED · SUCCEEDED_WITH_PARTIAL_FILES · FAILED_FETCH · FAILED_INTAKE_SAFETY · FAILED_SCHEMA_DRIFT · FAILED_PARSE. See `NPPES_ACQUISITION_AND_REFRESH_DESIGN.md`.

## Logging rules

- Log identifiers and counts, never observed values (names, addresses) — a licensed source's terms may forbid it and a delivered value is Government data. Tested: no `logging`/`print` in the isolated packages except the migration script's two operator messages.
- Log file names by basename only (`redact_for_log`).
- Every log line for a run carries `run_id`, `canonical_entity_id`, `source_id`, `dataset_version`.

## Metrics to emit when integrated (names fixed now so dashboards can be prepared)

`ei_runs_total{status}` · `ei_assessments_total{assessment}` · `ei_comparisons_total{dimension,signal}` · `ei_parse_files_total{file_kind,status}` · `ei_parse_rows_total{file_kind,outcome}` · `ei_intake_refusals_total{reason}` · `ei_source_unavailable_total{source_id}` · `ei_evaluate_seconds` (histogram).

## Failure → behaviour

| Failure | Behaviour | Never |
|---|---|---|
| Source file missing | SOURCE_UNAVAILABLE observation for that source; other sources still compared; run status COMPLETED_WITH_UNAVAILABLE_SOURCES | treat as MISSING/NO MATCH |
| Header drift | FAILED; nothing parsed; human compares headers | map by position and continue |
| Some rows malformed | PARTIAL; good rows used; bad lines listed | drop rows silently |
| Oversized field / csv error | PARTIAL with `stopped_at_line`; later rows unread and said so | exception mid-file with partial state |
| Flag off at any boundary | FeatureDisabled | fall through with empty results |
| Unknown flag value ("maybe") | treated as False | truthy string enables a feature |
| Migration pointed at remote host | refused, engine never created | prompt, retry, override by default |
| Two sources disagree | CONFLICTING_EVIDENCE with both visible | majority wins |
| Prior human decision present | echoed as reference | rules read it |

## Recovery

All stages are idempotent on identical input (same file hash → same observations and hashes; tested for determinism). Recovery from any failure is "fix the cause, re-run the stage"; no compensating writes exist because no stage writes to the shared platform.
