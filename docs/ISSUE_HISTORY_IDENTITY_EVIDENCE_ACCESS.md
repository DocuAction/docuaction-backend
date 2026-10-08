# Cross-delivery issue history: identity, evidence, access, and what the data supports

Feature flags (all default OFF): `ENABLE_RECORD_CHECK_RESULTS`, `ENABLE_ISSUE_HISTORY`,
`ISSUE_HISTORY_FEEDS_VIEWER`, `ISSUE_HISTORY_FEEDS_REVIEWER` (empty = nothing visible).
Endpoint: `GET /api/tefca/rce/entities/by-oid/{oid}/issue-history` (read only).

## What July to September history can and cannot show today (verified in code and migrations, not DEV)

- Per-delivery data that exists: `rce_source_intakes` (id, `received_at` system time, `delivery_label`,
  `source_metadata` JSON, `headers`), `rce_source_records` (`source_rce_id`, `npi`, `parsed`),
  `rce_ingestion_runs` (`rule_set_version`), `rce_issues`, delivery job `received_date` (operator entered, nullable).
- No column or code writes a snapshot/as-of date or a verified transmission date. Nothing in the codebase
  reads or writes such a key in `source_metadata`. The history therefore shows both as "not recorded".
- Per-record check results (`rce_record_check_results`) exist only for runs made AFTER the write flag is on and
  the migration `20261008_record_check_results` is applied. Earlier runs show "check result not persisted";
  nothing is back-filled and no pass is invented. An older delivery with findings but no stored result shows its
  findings and no pass.
- Consequence: with today's data a July to September comparison can show the findings each delivery's rule set
  recorded, NPI state per delivery, decisions, and gaps. It can establish RECURRING only if a comparable,
  completed, persisted check passed in an intermediate delivery that really exists. Without an August delivery it
  says: "Issue observed again; persistence or recurrence cannot be established."
- Real July data would need: an actual July delivery registered in the same feed tag, processed with the write flag
  on (or a governed re-run) so check results are persisted, the same record-ID scheme as September, and a recorded
  as-of date and verified transmission date source if those two fields are to be anything but "not recorded".
  Real July/August data is not on DEV; the demonstration uses synthetic cases.

## Identity rules

1. Link by the exact record ID (string equality, no trimming/case folding) inside the caller's authorized feeds.
2. Exactly ONE matching record per delivery. Zero = "No record with this record ID"; more than one = "no record was
   chosen"; neither is silently resolved. Records with no record ID are counted per delivery and stated as unlinkable.
3. A changed NPI under the same record ID stays in one history (`npi.change` = CHANGED / ADDED / REMOVED, compared
   with the named nearest earlier delivery that has a determinate NPI state). Original values are kept.
4. The same NPI under a DIFFERENT record ID (or no record ID) is never merged: it is listed under
   `candidate_associations` with status UNCONFIRMED and none of that record's history.

## Evidence rules

- Recurring only when an intervening comparable check, whose rule execution completed successfully and was persisted,
  passed. A pass from a rule execution that did not complete is an error, not a pass.
- Not comparable (never credited): rule version, requirement declaration, rule scope, schema (lane field absent),
  INT-002 registry state or unrecorded coverage, an entirely empty reference source, skipped / errored / undeclared
  / not-applicable rules, absent or duplicate record, missing or unsupported stored result.
- Absence of a finding is never a pass. `sequence_gaps` lists failed intakes, entity absent or re-keyed, no completed
  run, duplicate record ID, rule error/skip, unavailable source.
- Dates are shown separately: `received_at` (system), operator-entered receipt date, as-of, verified transmission;
  anything missing says "not recorded".

## Access

- Floor: role `viewer` (existing role model). Every 403 writes an audit row. Feed scope: role lists
  (`ISSUE_HISTORY_FEEDS_*`) intersected with the account's `feed:<TAG>` entries in `users.allowed_modules`
  (narrowing only). Hidden, unknown and unauthorized all answer one identical 404 body; each read writes one audit
  row (structure only).
- Submitted NPI values and previous values, decision rationale and actor identities: role `reviewer` and above
  (the existing reviewer level). Roles below (viewer, contributor, manager) get the redacted history: NPI state words
  and change words, never values. There is no list, count or export surface; logs and audit rows carry no values.

## Open decisions

Account-level `feed:<TAG>` design; whether the reviewer level is the right value-visibility line; a source for the
as-of and verified transmission dates; strict vs relaxed schema comparability.
