# ONC Delivery Ingestion Runbook — NEXT SESSION

For the August 21, 2026 ONC/ASTP-provided files. **Nothing in this runbook has
been executed. The files have not been accessed, uploaded, copied or ingested.**

This runbook contains no ONC file contents and must never be updated to include
any.

## Boundary

These files are **not** CMS PPEF, NPPES, SAM.gov, OIG LEIE, IRS/TIN, PECOS,
synthetic DEV entities, QA fixtures or mock records. They are Government-provided
source data and are handled only through the Area 1 delivery path below.

Nothing here runs without Imran's explicit go-ahead at step 10.

## Before you start

PROD is ready: container digest-pinned, runtime role `docuaction_app`, Area 1
owned by `docuaction_owner` and append-only, `PPEF_BULK_INGEST_ENABLED=false`,
`STARTUP_SCHEMA_MUTATION_ENABLED=false`. Area 1 currently holds **0** records.

**Area 1 is append-only and immutable. A row written in error cannot be deleted.**
Every dry-run step below exists because of that single fact.

## Sequence

1. **Identify the files.** Locate the ONC deliveries on the local machine. Do not
   open, copy or move them.
2. **Inventory metadata only** — filename, extension, byte size. No contents.
3. **SHA-256 each file locally.** Record the hashes; they are the identity used
   at every later step and the thing reconciliation depends on.
4. **Preserve originals unchanged.** Do not re-save, convert in place, or let a
   spreadsheet application rewrite them.
5. **Determine sensitivity / CUI handling** before anything leaves the machine.
   If the data is CUI, confirm the handling requirements apply to Area 1 storage
   and to any report derived from it.
6. **Format check.** The pipeline accepts **delimited text only** — CSV, TSV or
   pipe-delimited. It does **not** parse `.xlsx`, `.xls`, or any binary
   container; `reject_if_binary` refuses those before a single byte is written,
   by content signature rather than by file extension.
   *If the ONC files are Excel:* export each sheet to CSV or tab-delimited text,
   preserve the original `.xlsx` alongside unchanged, and record both hashes.
   Note which artifact was ingested and which is the original of record.
7. **Validate expected schema.** Compare each file's header row against
   `RCE_FIELDS` / `EXPECTED_SCHEMA_FINGERPRINT` in
   `app/tefca_registry/rce/field_map.py`. A fingerprint mismatch does not reject
   the delivery — it is recorded and the intake is flagged, deliberately, so a
   changed schema is never silently discarded. Expect to reconcile the field map
   before accepting a drifted delivery.
8. **Map each file to its ingestion component.** One delivery per file through
   `POST /api/tefca/rce/deliveries`.
9. **Dry run on DEV first.** Ingest into DEV and report accepted / rejected /
   duplicate counts, encoding anomalies, and any schema-drift flag. DEV is
   disposable; PROD Area 1 is not.
10. **STOP. Obtain explicit GO from Imran** before any PROD ingestion.

## PROD ingestion (only after step 10)

11. **Ingest through the controlled application path** — the authenticated
    endpoint, never direct SQL:

        POST /api/tefca/rce/deliveries
             ?delivery_label=<label>
             &delimiter=comma|pipe|tab        # declare it; do not rely on detection
             &received_date=2026-08-21        # the RECEIPT date, not today

    Requires `contributor` or above. The uploader's authenticated identity is
    recorded as `received_by`; it cannot be supplied by the caller.
    The bytes are malware-scanned, then preserved to immutable storage **before**
    parsing, so the original survives even if parsing fails.

12. **Verify the immutable source record.** `rce_source_intakes` holds one row per
    delivery with `original_filename`, `sha256`, `file_size_bytes`, `delimiter`,
    `encoding`, `encoding_anomaly`, `headers`, `schema_fingerprint`,
    `record_count`, `received_at`, `received_by`. `rce_source_records` holds one
    row per delivered line with its own `record_sha256`.
13. **Verify provenance** — the file SHA-256 recorded matches step 3 exactly.
14. **Verify audit records** for the ingesting principal.
15. **Verify Area 1 protections still hold** — `docuaction_owner` owns the four
    protected tables; `docuaction_app` cannot UPDATE (beyond approved workflow
    columns), DELETE, TRUNCATE, ALTER, DROP, or SET ROLE.
16. **Reconcile source rows → accepted / rejected / duplicate.** Line count in
    must equal row count out; `verify_line_count` aborts the intake rather than
    committing a partial Area 1. A byte-identical re-delivery is **accepted and
    linked**, not rejected — ONC may legitimately resend.
17. **Verify no unintended external activity.** Ingestion calls no connector:
    NPPES, SAM.gov, OIG LEIE, PECOS and CMS PPEF are **not** invoked by upload,
    parsing, curation, quality or reconciliation. They run at review/evidence
    time only. Confirm PPEF remains 0 records / 0 snapshots / 0 jobs.
18. **Produce post-ingestion evidence** — counts, hashes, intake ids, and the
    Area 1 privilege re-check. No file contents.

## Rollback

There is no delete path for Area 1, by design. If a delivery is ingested in
error the remedy is PITR restore of `docuaction-db-geo` to the recorded
pre-ingestion point — so capture that recovery point before step 11, exactly as
the database baseline did.

## Known gaps to settle before step 11

- **Excel is not parseable** by the pipeline (step 6). Decide the conversion and
  which artifact is the original of record.
- **Source organization** ("ONC / ASTP") has no first-class column; it is carried
  in `source_metadata` (JSONB) alongside the client IP. Adequate, but note it.
- **Six sensitive PROD settings remain plaintext** app settings (Key Vault
  migration blocked on vault network access). Not an ingestion blocker.
