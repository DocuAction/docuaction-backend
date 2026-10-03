# CSV export row grains — delivery routes (2026-10-03)

Every CSV export of one delivery states, here, exactly what one row is, which
relation and predicate the row count is a `COUNT(*)` over, and how the export
is registered. The row count is never a count of newline bytes: it is the same
`COUNT` executed in the same consistent snapshot (`REPEATABLE READ, READ ONLY`,
on a dedicated connection) that then serves the rows, and it is logged with the
export's SHA-256 and byte count under `csv_export_manifest`, keyed by the
`X-Export-Id` response header.

| Route | One row is… | Count relation / predicate | Delivery | Shape |
|---|---|---|---|---|
| `GET /deliveries/{intake_id}/dispositions.csv` | one **received source line** of the delivery with its *current* disposition (highest sequence per record) | `COUNT(*)` over `_DISPOSITION_SQL` with the same filters (`disposition`, `source_row`, `entity_name`, `npi`); `X-Total-Rows` is that count, `X-Returned-Rows` the rows written — they differ only when the caller's `limit` (default 50,000, max 200,000) truncates the export, and the manifest then records `row_count_reconciles=false` deliberately | materialised | not streamed (pre-existing; a bounded, filterable export) |
| `GET /deliveries/{intake_id}/findings.csv` | one **finding** (`RceIssue`) of the delivery's *current* quality run (`run_selection.current_issues_filter`) | `COUNT(*)` over `RceIssue` with the identical predicate | streamed row by row | `StreamingResponse` |
| `GET /deliveries/{intake_id}/identifier-conflicts.csv` | one **identifier conflict**: a distinct `(entity_id, identifier_type)` pair with its latest decision, grouped from the append-only `TefcaIdentifierDecisionEvent` ledger | `COUNT(DISTINCT (entity_id, identifier_type))` over the events of this intake — the same pairs the grouping produces | materialised | the grouping is done in Python from the ledger (same as the report's data layer), so the export is built in memory; count and SHA-256 are computed over the body actually sent |
| `GET /deliveries/{intake_id}/review-records.csv` | one **review record** (`ReviewRecord`) linked to the delivery — via its `source_record_id` (manual single-entity review) **or** its `entity_id` among the delivery's promoted entities (bulk `arc_pipeline` path) | `COUNT(*)` over `ReviewRecord` with the identical `OR` predicate | materialised | same as above |
| `GET /deliveries/{intake_id}/verification-coverage/{source}/{outcome}/csv` | one **entity** of the delivery at one (source, outcome), de-duplicated across source-record fan-out (`source_record_count` says how many delivered lines it came from) | `count_outcome_entities` over the identical `EVIDENCE_ROWS_CTE_SQL` relation and predicate | streamed | `StreamingResponse` (the reference implementation, 2026-10-03) |

Shared properties, every route:

* **Formula-injection protection** — `csv_engine.neutralise_row` is applied to
  every data row at write time (a leading `=`, `+`, `-`, `@`, tab or CR is
  prefixed with `'`). The persisted value is never changed; only the CSV
  serialisation is. (`findings.csv`, `identifier-conflicts.csv`,
  `review-records.csv` and the verification-coverage export lacked this before
  2026-10-03 — found by grep, fixed.)
* **Embedded newlines** — RFC 4180 quoting; a field with an embedded newline
  is one record. Clients must parse with a CSV reader; counting `\n` is wrong
  (`tests/test_csv_export_reconciliation.py` proves the difference).
* **Snapshot consistency** — the count and the rows are read against the same
  database snapshot. This cannot be done on the request's shared session
  (`SET TRANSACTION ISOLATION LEVEL` must be a transaction's first statement,
  and the auth dependency has already queried on that session), so each export
  opens its own connection, sets `REPEATABLE READ` + `READ ONLY` as its first
  statement, serves count and rows, then rolls back and closes.
* **Manifest** — `csv_export_manifest` log record: `export_id`, `intake_id`,
  `route`, `registered_row_count`, `streamed_row_count`, `row_count_reconciles`,
  `byte_count`, `sha256`, plus the route's own predicate facts. The response
  carries `X-Export-Id` and `X-Returned-Rows`.
