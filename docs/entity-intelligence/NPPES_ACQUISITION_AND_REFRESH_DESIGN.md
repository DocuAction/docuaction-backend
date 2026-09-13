# NPPES V2 acquisition and refresh — design (NOT IMPLEMENTED)

No downloader exists in the codebase. This document specifies one so that, when approved, it is built as a controlled job rather than an ad-hoc script. Source of truth for file names and cadence: CMS `download.cms.gov/nppes/NPI_Files.html` and readme v.2 (May 12, 2026), re-read 2026-09-12.

## What CMS publishes

| Artifact | Cadence | Example | Contents |
|---|---|---|---|
| Monthly full V2 bundle | monthly (page currently shows "NPPES Data Dissemination V.2 (August 10, 2026)") | `NPPES_Data_Dissemination_August_2026_V2.zip` | `npidata_pfile_*.csv` (330 cols), `othername_pfile_*.csv`, `pl_pfile_*.csv`, `endpoint_pfile_*.csv`, each with `*_fileheader.csv`; readme and CodeValues PDFs |
| Weekly incremental V2 | weekly, date range in name | `NPPES_Data_Dissemination_083126_090626_Weekly_V2.zip` | same file set, only NPIs changed that week |
| Monthly Deactivated NPI report | monthly | separate zip | NPIs deactivated; not parsed yet |

Version 1 is unsupported since 03/03/2026 and is never acquired.

## Acquisition job (design)

```
SCHEDULE (monthly full + weekly incremental, both explicit)
  → FETCH   bounded download to an intake directory; HTTPS to download.cms.gov only (host allowlist);
            size ceiling; timeout; no redirects off-host; no credential (public data)
  → VERIFY  SHA-256 of the zip recorded; central-directory inspection with intake_safety
            (member allowlist .csv/.pdf/.txt, member count, size, ratio, zip-slip) BEFORE extraction
  → PRESERVE immutable copy of the zip under <intake>/<dataset_version>/ with a manifest
            (bundle name, hashes per member, retrieved_at, readme "Updated" date)
  → HEADER FINGERPRINT hash of each *_fileheader.csv compared with the stored V2 fingerprint;
            any difference = SCHEMA_DRIFT → job status FAILED_SCHEMA_DRIFT, nothing parsed
  → PARSE   parser with explicit ParseReport.status (OK / PARTIAL / FAILED); PARTIAL is surfaced,
            never promoted to OK
  → SCOPE   only NPIs that appear on program-delivered entities are turned into observations
            (the full file is preserved; it is not loaded row-for-row into the database)
  → OBSERVE adapter observations with SourceVersionRef(dataset_version = bundle name,
            retrieved_at, source_file_hash)
  → STATUS  one job record per run: SUCCEEDED / SUCCEEDED_WITH_PARTIAL_FILES / FAILED_*;
            counts of rows read/ok/malformed/skipped per file; nothing silent
```

## Refresh semantics

- A **full** bundle replaces the baseline: observations are re-derived for in-scope NPIs and compared with the prior edition → `SOURCE_VERSION` deltas where the statement is unchanged but the edition is newer, `EVIDENCE` deltas where NPPES itself changed its statement.
- A **weekly** bundle is applied as a patch over the baseline for the NPIs it contains; NPIs absent from the weekly file are unchanged, not removed.
- Deactivation: the main file carries deactivation/reactivation dates and a replacement NPI; these are observed values, not judgments ("the NPI record shows a deactivation date" — never "the organisation closed").
- Retention: every preserved edition is kept (public data, reproducibility); a retention limit is an operational decision later.

## Failure and recovery

| Failure | Behaviour |
|---|---|
| Download fails / times out | job FAILED_FETCH; prior edition remains current; SOURCE_UNAVAILABLE is **not** asserted for entities — the prior edition is still the evidence, with its own date |
| Zip fails inspection | FAILED_INTAKE_SAFETY with the refusal list; file quarantined, not extracted |
| Header drift | FAILED_SCHEMA_DRIFT; a human compares the new header against `schema.py` and the readme before any mapping change |
| File PARTIAL | SUCCEEDED_WITH_PARTIAL_FILES; malformed line numbers listed; observations from good rows carry the edition |
| Half-written preserve | manifest written last; a directory without a manifest is ignored and cleaned on next run |
| Re-run same edition | idempotent: same hashes → no new deltas |

## Explicitly excluded tonight

Any network code, scheduler registration, storage path configuration, or job wiring. The `SourceConnector` protocol in `app.core.ingestion.contracts` is the intended integration point, with `RetrievalMethod.DOWNLOAD`.

## Verified details that the design depends on

- `<UNAVAIL>` placeholder (undocumented; verified in the sample) must be blanked — done in the parser.
- Other-name type code 6 is a pointer to the reference file, not a name type (readme note; verified: field is `<UNAVAIL>` in all 3,127 sample cases) — handled in the adapter.
- Legal Business Name > 70 chars occurs (8 of 8,588 sample organisations) — V2 is required.
- Duplicate reference-file rows occur (same NPI, name, code) — kept; comparison treats same-kind duplicates as one kind.
