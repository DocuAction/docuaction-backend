# Development report examples

> ## DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY — NOT ONC FINDINGS
>
> **The Government entity CSV has not been delivered or imported.**
> `is_running_mock()` is **TRUE**. Every figure in every file in this directory
> is a validation result computed over development data. **None of these files
> may be sent to the COR**, forwarded, or used as a deliverable example in any
> Government-facing context.

Generated 2026-08-23 from the canonical report engine (`app/reports/generator.py`)
against the development database, at commit `24ae032`.

## What these are for

They exist to prove the reporting machinery works end to end — that a report
renders, carries real provenance, cites its evidence version, and announces its
own classification. They are evidence about the *engine*, not about any entity.

## The files

| File | Report type | Bytes |
| --- | --- | --- |
| `executive.html` | COR executive summary | 245,792 |
| `verification.html` | Verification detail — evidence appendix | 326,097 |
| `verification_brief.html` | Verification brief | 193,014 |
| `data_quality.html` | Data quality | 307,913 |
| `intake.html` | Source / provenance appendix | 253,276 |

`manifest.json` records, for each file, its SHA-256, the report's
`data_payload_hash`, the review cycle, the source file hash and the data
classification.

Most of each file's size is the inlined WOFF font, which is embedded so the
document is self-contained — a report that renders differently depending on what
is installed on the reader's machine is not a reliable record.

## What to check when reading one

1. **The banner is the first thing in `<body>`.** Not in a meta tag, not in a
   comment, not below the header. Both a sighted reader and a screen reader
   should meet it before any number.
2. **The provenance table at the foot names a real source.**
   `689472073480b1cc…` is the SHA-256 of the Area-1 delivery, reproducible by
   hashing the file at `uploads/rce_deliveries/`. Before Phase 7 this field read
   `cafe`.
3. **The review cycle is `DEV-CYCLE-phase6-bulk-1.1.0-689472073480`.** Derived
   deterministically from the evidence rule version and the source hash, and
   prefixed so it cannot be mistaken for a contract cycle label. Before Phase 7
   it was null.
4. **Nothing claims a finding.** These reports present observations, the
   evidence version behind them, and what remains blocked on methodology.

## Report families NOT represented here

The contract's deliverable families — weekly (D3.1), final (D3.2), bi-weekly
(D4.1), quarterly (D4.2/D5.2) and priority status (D5.1) — are implemented in
`app/Tefca/reporting.py` and served from `/api/tefca/reports/*`. They are **not**
in this directory because they are not yet produced by the canonical engine.
See §5 of `../phase7_contract_reporting_matrix.md` for why that migration was
scoped but not executed in Phase 7.

## Regenerating

Report content is not deterministic across runs — the generation timestamp and
report ID advance — so a regenerated file will differ from the committed one in
those fields. What must not change is the `data_payload_hash` for the same
underlying evidence: that is the integrity anchor, and it is what
`manifest.json` records.
