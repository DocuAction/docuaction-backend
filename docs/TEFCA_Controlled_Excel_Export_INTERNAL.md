# TEFCA ARC — Controlled Excel Export

**Internal engineering record. Not a Government deliverable.**
Contract 7571MN26F80064 · Step #17 · 30 August 2026

---

## 1. The one sentence this document exists to protect

**DocuAction is the system of record. The workbook is a controlled export.**

Everything below follows from that. The workbook is a *snapshot* of what
DocuAction holds, taken under a stated classification, hashed, registered, and
handed over. Nothing typed into the spreadsheet comes back. There is no import
path from it, no reconciliation from it, and no decision that reads it.

What the workbook must therefore never become:

| Not | Because |
|---|---|
| the operational workflow | analyst and QA decisions are events in an append-only log, not cells |
| a second database | a copy that can be edited is a second answer to the same question |
| the source of an analyst or QA decision | the maker–checker chain lives in DocuAction and is auditable there |
| a replacement for Area 1 | Area 1 is immutable; a spreadsheet is the opposite of immutable |
| a way to change Government data | there is no write path, and there will not be one |
| a way around RBAC or the audit trail | production is role-gated and registered; see §6 |

---

## 2. What is in it

Ten sheets, in this order. The order is fixed in `SHEET_ORDER` and asserted by
test, because a reviewer who learns the shape of one workbook must find the same
shape in the next.

| # | Sheet | What it is | Scope |
|---|---|---|---|
| 1 | README | what this file is, what it is not, and its classification | — |
| 2 | Source_Data | the 41 delivered fields, in delivered order, as delivered | the delivery |
| 3 | Curated_Data | records curation actually changed, before and after | the delivery |
| 4 | Processing_Status | one row per delivered record: status, findings, promotion | the delivery |
| 5 | Data_Quality | every finding, under the rule-set version that raised it | the delivery |
| 6 | Verification | observations from authoritative sources, verbatim | the delivery's entities |
| 7 | Relationships | canonical TEFCA relationships as promoted | the delivery's entities |
| 8 | Review_Status | workflow state per case; no staff identities | the delivery's entities |
| 9 | Data_Mapping | the 41-field mapping, read from `FIELD_SPECS` | — |
| 10 | Export_Metadata | hashes, counts, reconciliation, versions | — |

**Sheets 6–8 are scoped through the entities the delivery promoted**, resolved
once in `_entities_from()`. See §4 — this was a defect, and it was not a small
one.

---

## 3. Excel is opinionated, and every opinion it has is a data change

Left alone, Excel reads `01234` as the number 1234, `1234567890123456` as
`1.23457E+15`, `03/04` as a date, and `=cmd|' /c calc'!A0` as a formula to
evaluate. Each of those silently alters a Government value *after* it has left
DocuAction, where nothing will ever notice.

`xlsx_engine` is therefore mostly refusal:

* **every source cell is written as text**, so a delivered identifier stays the
  string that was delivered;
* **a formula-shaped value is stored as a string and flagged literal.** Two
  separate mechanisms, both needed: openpyxl *types* a leading `=` as a formula,
  so `data_type` is forced back to `"s"`; and `quotePrefix` is set so Excel will
  not re-evaluate it on open. Neither touches the value — `quotePrefix` lives in
  the cell's **style**, not its data, so reading the cell back returns exactly
  what was delivered. The engine does **not** prepend an apostrophe to the
  value; an earlier draft did, and that was itself a data change.
* **nothing is trimmed, padded, rounded or reformatted.** The single
  transformation applied to a source string is the removal of control characters
  Excel cannot store at all, and those are not visible data.

Timestamps: Excel has no concept of a timezone and openpyxl refuses an aware
datetime outright. Aware values are converted to UTC and the marker dropped —
the instant is preserved — and the sheets name their timestamp columns as UTC so
nothing is lost but the suffix.

---

## 4. The defect this gate found

**Three of the ten sheets ignored the delivery they were handed.**

`_verification`, `_relationships` and `_review_status` each took `intake_id` and
never used it. Verification observations, relationships and review cases hang
off the **entity**, not off the delivery, and a query over those tables that
does not say which entities it means returns the whole registry.

On the seven-record synthetic fixture, the Relationships sheet exported
**36,231 rows** — the entire registry, including the Government population,
into a workbook classified `DEVELOPMENT_TEST`.

It was found by profiling, not by reading. The certification suite took 8m20s
for nineteen tests; the cause was 291,531 cells being written for a seven-record
delivery. The scoping fix took the suite to 28 seconds.

Fixed by resolving scope **once**, before any sheet is built, so every
entity-keyed sheet is bounded by the same delivery:

```python
scope = await _entities_from(db, intake.id)   # canonical_entity_id of this
                                              # delivery's curated records
```

An unpromoted record contributes no entity. That is not a silent drop: it is
visible on Processing_Status as `Promoted = No`, which is the sheet whose job it
is to say so — and a test asserts that a delivery which promoted nothing
produces three **empty** entity sheets rather than the whole registry.

### Two smaller ones, found the same way

* **The mapping sheet numbered fields from zero.** `FIELD_SPECS[i].ordinal` is a
  zero-based column index — correct for intake, wrong under a heading a reviewer
  reads as "field 1 of 41". The index is unchanged; the presentation counts from
  one, and asserts the two agree so a gap in `FIELD_SPECS` cannot pass silently.
* **The engine re-derived every cell's style.** openpyxl interns each style
  assignment into a workbook-wide table and *hashes* the Font/Border/Alignment
  recursively on every assignment. Measured at 1,976 cells/second. The workbook
  uses about a dozen distinct styles, so each is now resolved once through the
  public attributes and copied thereafter: **5,331 cells/second**, 2.7×, with
  the style still defined by the same assignments.

---

## 5. Classification is a property of the data

The export never accepts a classification from its caller. `WorkbookExportRequest`
has no such field, and a test asserts it never grows one. Labelling an authorised
Government export `DEVELOPMENT_TEST` strips the handling the label exists to
require; stamping `GOVERNMENT` onto development data tells a reader they are
looking at findings. Both are wrong, in different directions.

`source_provenance._classification()` reads `data_state_sync()`, which is the
**synchronous fallback**: it holds no database handle, so by construction it can
never return `GOVERNMENT` and always answers `DEVELOPMENT_TEST`. That is the
right default for a helper that cannot check, and the wrong answer for a route
that can. The export route has `db`, so it asks `resolve_data_state()`, which
checks the intake itself. The fail-safe direction is unchanged: `GOVERNMENT`
requires a controlled intake satisfying every condition in
`_authorised_government_intake`, and anything else stays `DEVELOPMENT_TEST`.

**Latent defect recorded, not fixed here.** `_classification()` is still used by
every other report path, where the same blindness applies. Correcting it changes
the classification of D1.1 through D5.1 and belongs in its own scoped change,
not in an export gate.

> **Closed by Step #17C.** Investigated, classified as an actual defect, and
> corrected at the source: `authoritative_source_provenance` now resolves the
> classification against the intake. `_classification()` remains the honest
> fallback for callers with no session, and the export route's private copy of
> the resolver is gone. The current snapshot is still DEVELOPMENT_TEST — the
> authorisation marker is still absent, and that is a governance decision.

---

## 6. Controls

| Control | How |
|---|---|
| Authentication | every reports route carries a role dependency; a test walks the router and fails on any that does not |
| Authorisation | producing an export requires `qalead`; reading a report requires `viewer` |
| Integrity | bytes are stored content-addressed, registered with `rendered_sha256`, and **re-hashed before every download** |
| Provenance | the registry row carries `source_artifact_sha256` (the delivered file) and `report_data_hash` (what the workbook says) |
| Versioning | `artifact_version` per report and content type; nothing is replaced |
| Classification | resolved from the intake, never from the caller |
| Secrets | asserted absent: a test reads every string cell of a rendered workbook and fails on connection strings, keys, tokens, hosts or tracebacks |

**Why `qalead` and not `viewer`.** Reading a report inside DocuAction keeps the
platform's controls around it. A workbook is a file that leaves, and once it has
left, RBAC, the audit trail and the immutable source are all behind it. The floor
is the role that already carries independent responsibility for what may be
relied upon.

The bytes are **not** returned by the production endpoint. It registers the
artifact and returns a link to `/artifacts/{report_id}/download`, which re-hashes
before serving — one download path, and it is the verified one. A test asserts
the registered path constructs no `Response`.

There was one extension map for the store key and a second for the download
filename, and neither knew about `.xlsx`. They are now one map,
`ARTIFACT_SUFFIXES`, and a test asserts the route uses it.

---

## 7. The preview

Ten rows a sheet, returned directly, **registered nowhere**. It has no registry
row, no version, and no hash of record, and it says so on its own face: the
identifier ends in `-PREVIEW` and every sheet carries a note above the header.
A truncated file that could be mistaken for the export would be worse than no
preview at all.

---

## 8. Scale, measured

Read-only counts against the delivered population, and a shape-equivalent render
(no Government data was rendered):

| Sheet | Rows | Columns | Cells |
|---|--:|--:|--:|
| README | 32 | 2 | 64 |
| Source_Data | 23,566 | 41 | 966,206 |
| Curated_Data | 1,631 | 9 | 14,679 |
| Processing_Status | 23,566 | 9 | 212,094 |
| Data_Quality | 36,916 | 13 | 479,908 |
| Verification | 43 | 8 | 344 |
| Relationships | 36,032 | 8 | 288,256 |
| Review_Status | 43 | 9 | 387 |
| Data_Mapping | 41 | 9 | 369 |
| Export_Metadata | 35 | 2 | 70 |
| **Total** | | | **1,962,377** |

Measured on a 1,765,657-cell render of the same shape:

| | |
|---|---|
| Time | **406 s** (6m 46s) — 4,345 cells/s |
| File size | **6.3 MB**, extrapolating to ~7 MB |
| Peak Python heap | **617 MB**, extrapolating to ~690 MB |

Two consequences.

**Rendering is off the event loop.** At this size a synchronous render would not
slow the other requests in the process, it would stop them. `render_workbook`
runs in a worker thread.

**Seven minutes is still not a request.** A blocking HTTP call of that length
will hit gateway timeouts and gives the caller no progress. A background job
with a status endpoint is the right shape.

> **Superseded by Step #17C.** The export is now queued and run by a poller;
> the request returns a receipt in about 21 ms. The workbook design below is
> unchanged. See `TEFCA_Controlled_Excel_Export_Operationalization_INTERNAL.md`.

`write_only` mode was considered and rejected: it streams rows without holding
the sheet, but cannot set freeze panes, filters, column widths, or per-cell
number formats — and those are precisely what stop Excel retyping a delivered
identifier. Correctness first, with the cost measured rather than assumed.

---

## 9. Certification

Thirty-three tests, all against a **synthetic** delivery inside an outer
transaction that is rolled back. Fixture OIDs sit under an unassigned `9.99.999`
arc, names are prefixed, and no real NPI appears. The Government population is
never exported by the suite.

The file-facing tests **reopen the produced XLSX with the parser** and assert on
what Excel would actually hold — not on the code that produced it:

* Source_Data is the 41 fields, in order, checked against an independently
  written list;
* a leading zero survives, a long identifier does not become scientific
  notation, a date-like string stays a string;
* a formula-shaped value is stored as a string, carries the literal marker, and
  **no cell anywhere in the workbook has formula type**;
* a value that is *not* formula-shaped does **not** carry the marker;
* a source recorded as `unavailable` is exported as unavailable — never clear,
  never a pass;
* a finding keeps the rule-set version that raised it, not today's;
* only a QA-approved review is reportable;
* the three entity sheets are bounded by the delivery, asserted from both ends.

---

## 10. Mutation testing

Sixteen mutations, one control each, and **each names the test that must catch
it** — a stronger claim than "something went red", because it proves the
specific guard works rather than that an unrelated assertion happened to trip.
**16/16 detected.** Files are restored byte-identically in a per-mutation
`finally`, an outer `finally`, and again through `atexit`.

Three mutations were MISSED on the first pass, and each exposed a real gap:

* **a `.strip()` on delivered text passed every test**, because nothing in the
  fixture carried surrounding whitespace. A delivered value now arrives padded
  on purpose, and the export must hand it back padded — trimming a Government
  value is precisely the silent change this gate exists to prevent.
* **two scope mutations were invisible**, because the scope test used a delivery
  that promoted nothing: with an empty scope the guard returns before the filter
  is ever reached, so the test proved the guard and not the filter. A second
  synthetic delivery — promoted, related, verified and reviewed — now gives the
  first delivery's workbook something real it must exclude, and the proof no
  longer depends on the database happening to hold anyone else's rows.
* **the scrubber test read the handler's source as text** and passed with the
  scrubber removed, because the word still appeared in an import inside the
  function. It now parses the route and asserts the returned `artifact` value is
  a call to `public_artifact`.

In every case the test was strengthened; no mutation was weakened.

---

## 11. Accessibility

The measures the library can actually carry, and no claim beyond them:

* **meaningful sheet names** in a fixed, logical order — a reader who learns one
  workbook finds the same shape in the next;
* **one labelled header row per sheet**, frozen, with an autofilter over the
  data; a test asserts no column is unlabelled;
* **no merged cells anywhere.** Merges break a screen reader's reading order and
  a filter's column, and a test fails on any;
* **panes freeze on rows only**, never columns, so the identifier column is
  never scrolled out from under the reader;
* **no colour carries meaning on its own.** The header fill is decoration; every
  status, result and outcome is a word. A source that could not answer reads
  `unavailable`, not amber;
* **wrapped text and sized columns** on the free-text fields, capped so one long
  value cannot make a column wider than the screen;
* **consistent date and time formatting**, stated as UTC;
* **no macros, no VBA, no external workbook links, and no cell of formula type**
  anywhere in the file — all asserted by reopening the produced workbook.

**Section 508 certification is not performed and is not claimed.** It remains a
separate Government activity. Manual review still required: colour contrast
against the reviewer's own Excel theme, screen-reader navigation on the
reviewer's assistive technology, and print/reading order at their zoom level.

---

## 12. Operational generation procedure

1. Sign in with a role of `qalead` or above. Reading reports does not require
   it; producing a controlled export does.
2. **Reports → Data Review Workbook**.
3. *Preview, first 10 rows* to confirm the shape. The preview is not registered
   and is not the export; it says so on every sheet.
4. *Produce workbook*. The classification shown is resolved from the intake, not
   chosen. At the delivered population this takes several minutes.
5. The panel then states the export identifier, delivery, generation time,
   workbook and artifact versions, status, file type, and row counts.
6. *Download workbook*. The bytes are re-hashed against the registry before they
   are served; a mismatch fails the download rather than serving the file.
7. Earlier versions stay available at `GET /api/reports/artifacts/{report_id}`;
   nothing is ever replaced.

Reproducibility: the same immutable snapshot produces the same
`report_data_hash`, which excludes the generation timestamp and the generator's
identity — two workbooks built from the same snapshot say the same thing even
though they were produced at different moments. Re-finalising byte-identical
content returns the existing registration rather than creating a second one; a
changed snapshot creates a new **version** (semantics B of §25).

---

## 13. Not done, and why

* **No Government workbook was generated.** The delivered intake carries no
  `government_authorized` marker, so DocuAction's own resolver reports
  `MOCK_TEST` / `NO_AUTHORISED_GOVERNMENT_INTAKE` while the database holds the
  ONC/RCE snapshot with the clean Area-1 SHA-256. The flag and the content
  disagree. That is ambiguous, and the instruction for ambiguity is to forecast
  and not generate. See the Step #17 report §AC.
* ~~**No background job.** Forecast only; see §8.~~ **Built in Step #17C.**
* ~~**`_classification()` not corrected platform-wide.** See §5.~~
  **Corrected in Step #17C.**
