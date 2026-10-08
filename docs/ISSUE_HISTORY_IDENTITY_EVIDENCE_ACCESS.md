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

## Real-data readiness (not demonstration)

Everything above is exercised on synthetic data. This section is about the real DEV data, read-only facts
supplied by the operator; nothing was queried or written by this work.

What is already in DEV: the July snapshot is loaded as a LEGACY source intake (`onc-snapshot-20260720.csv`,
label ONC-ASTP-2026-08-21, received 2026-08-21, 23,566 records) with a COMPLETE ingestion run under rule set
1.0.0 (36,916 issues) and NO delivery job, NO delivery-delta row and NO per-record check results. The September full
snapshot (intake 4417b334, 24,589 records, received 2026-09-02) has job 0930826c and a COMPLETE run under 1.3.0.
Record IDs: 23,554 in both, 12 July-only, 1,035 September-only. NPI differs for 360 shared IDs (356 blank to value,
2 value to blank, 2 value to different value). NPIs shared by several record IDs: 281 in July, 495 in September.
So real cases C (changed NPI, same record ID) and D (same NPI, different record IDs) exist in the data.

Three statements that must not be confused:
- "July not loaded in DEV" is FALSE: it is loaded as a legacy intake.
- "July not visible in history" is TRUE until this change ships and the operator enables it.
- "July unavailable" is FALSE: the original file exists on the workstation (Downloads copy, 10,042,400 bytes, equals the
  DEV intake and `field_map.PROFILED_SHA256`; the other copy under ONS HHS is a re-save with trailing commas).

What the history builds from: the delivery sequence is built from `rce_source_intakes` inside the caller's authorized
feeds, NOT from delivery jobs; jobs only add `job_ids`. A legacy intake with no job is therefore already a delivery.
The one thing it needs is a feed membership: an intake with no `source_metadata.feed` tag is in no feed (fail closed).
Two ways to give it one: (1) the existing governed `scripts/tag_intake_feed.py` (a data write, operator step), or
(2) the new read-only setting `ISSUE_HISTORY_INTAKE_FEEDS="<intake uuid>:<FEED>,..."`, which maps an UNTAGGED intake
into a feed for history only, never overrides a tag, writes nothing, and still requires the feed to be allowed to the
caller. Default empty.

What the history then shows for the real July and September, with no new data:
- July as a delivery: delivery job "not recorded (legacy intake, no delivery job)", intake id, system receipt time
  (labelled as such), operator receipt date / as-of / verified transmission "not recorded", run id and rule set 1.0.0.
- July findings come from that run's `rce_issues` as recorded under 1.0.0. Every July check lane says "check result not
  persisted"; nothing is inferred as a pass.
- Because July ran under rule set 1.0.0 and September under 1.3.0, a finding seen in both shows "Rule set changed - not
  comparable" and the exact sentence "Issue observed again; persistence or recurrence cannot be established." It can never
  be RECURRING.
- The 12 July-only IDs show September as "absent from this delivery"; the 1,035 September-only IDs list July under
  "earlier deliveries without this record ID". Neither is a pass or a correction.
- NPI state per delivery and changes (cases C and D) work on the stored record NPI; values for reviewer level and above only.
- Caveat not verifiable here: July findings whose rule ids are not among the eight slice rules do not appear in the lanes
  (they are not later-stage findings either). Findings written under 1.0.0 with the slice rule ids appear.

Check results on current DEV: BOTH July (legacy intake) and September (job 0930826c) predate the persisted
check-result table, which does not exist on DEV. The real history will therefore show "check result not persisted" for
both and can never show PASS or RECURRING until NEW deliveries are processed with `ENABLE_RECORD_CHECK_RESULTS` on. The
synthetic screenshots show a September PASS only because the synthetic September was processed with persisted results;
those screenshots are demonstration, not what real DEV will show.

No DEV intake carries a feed tag (all 61 are untagged), so BOTH July and September need `ISSUE_HISTORY_INTAKE_FEEDS`
(or the tag script) before either appears.

Other findings: only about 15.7k of July's 36.9k and 16.0k of September's 36.5k recorded findings fall in the eight lanes.
Every finding of the selected run under any other rule (CON-005, FMT-001, ACT-001, NPI-008 ...) is preserved and listed per
delivery under "Other findings recorded in this delivery (rules not shown as lanes above)" with rule, rule version, field,
severity, finding type and a count; description and original value for reviewer level and above only. They are recorded
only: no comparability or recurrence is computed for them. (Later-stage findings stay in their own block.)

Governed steps only: (1) release of #133 and #72 (migration 20261008 is needed only for FUTURE persisted check
results, not for showing July and September); (2) operator enablement of `ENABLE_ISSUE_HISTORY`, the viewer/reviewer feed
lists, and either the feed tag or `ISSUE_HISTORY_INTAKE_FEEDS`; (3) to persist check results for new runs,
`ENABLE_RECORD_CHECK_RESULTS`. No reprocessing, upload or backfill is needed or performed.

August: no August delivery exists. Its absence is a real gap in the sequence and is shown as such (no August row).
An August comparison needs an actual August delivery processed with persisted check results.
