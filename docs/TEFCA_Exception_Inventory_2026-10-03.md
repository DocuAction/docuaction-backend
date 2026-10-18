# TEFCA ARC — Exception inventory (generated, reproducible) — 2026-10-03

Produced by `scripts/exception_inventory.py` (this commit). This document records the method,
the provenance of every count, and embeds the generated reports verbatim. Aggregates only — no
delivered value, name, identifier or address appears anywhere below.

## 1. Method

The inventory is read from the database tables that already hold each population, never from
report tables or prior summaries, and keeps three populations in three sections that are never
summed:

| Population | Table(s) | Grain reported |
|---|---|---|
| Processing failures | `rce_delivery_stage_events` (stage × status × attempt), `rce_delivery_jobs`, `rce_rule_execution_history` | events, jobs, executions; failure classes |
| Data-quality findings | `rce_issues` joined to the quality-rule registry (`quality_rules.ALL_RULES`: category, stage, version, description = cause) | findings **and** unique affected records, delivery-level findings, deliveries, open / QA-approved |
| Verification outcomes | `tefca_dimension_evidence` (dimension × source × disposition), `tefca_verifications` (source × status) | rows **and** distinct entities; per-source distinct entities by outcome with `REVIEW` reported separately from `NOT_FOUND` |
| Analyst / review cases | `review_records` (bucket × rule × rule version), `review_decision_events`, `sample_entities` | cases, distinct entities, human-resolved, QA-reportable, assigned |

Stage attribution of a ledger row uses the rule registry's `stage` and the issue-code namespace
(`DQ-` quality engine, `VR-` verification-time findings). "Cause" is the registered rule
description. A population may be scoped to one intake (`--intake`) — the promoted population of
that delivery (`rce_curated_records.canonical_entity_id`), the same population definition
`verification_coverage.py` uses.

With `--real-file`, the script reads the delivered file through the application's own reader
(`rce.reader.read_delivery`, declared delimiter `|` exactly as ingestion declares it) and computes
the six NPI assessments separately: syntax (format + check digit), type fitness (NPPES Entity Type
Code), identity matching (name bands + practice-address comparison), own-NPI presence requirement
(the internal policy predicate, kept apart from the legal test), representative-provider evidence
(NPI-1 on an organisation record; deactivated NPIs), covered-provider eligibility (NPPES taxonomy
prong + PPEF enrolment prong). Evidence is the locally held, pinned NPPES Data Dissemination
edition index (`var/authoritative/nppes_index.json`, member `npidata_pfile_20050523-20260809.csv`)
and the CMS PPEF enrolment extract of 2026-07-17 — offline, reproducible, no live lookups.

## 2. Reproducible commands (local disposable Postgres 5499 only)

```
cd <sam-fix-be worktree>
export SECRET_KEY=<58+ chars> ALLOWED_HOSTS=* DATABASE_URL=postgresql+asyncpg://docuaction_owner:docuaction_owner@127.0.0.1:5499/test_sam
python scripts/exception_inventory.py --database-url postgresql+asyncpg://docuaction_owner:docuaction_owner@127.0.0.1:5499/test_report_full_scale \
   --intake 9216339b-e1d7-49b2-bf43-30cfa0346952 --label SYNTHETIC-24563-test_report_full_scale --out <evidence dir>
python scripts/exception_inventory.py --database-url postgresql+asyncpg://docuaction_owner:docuaction_owner@127.0.0.1:5499/test_sam \
   --label SYNTHETIC-shared-test_sam --out <evidence dir>
python scripts/exception_inventory.py --skip-db --label REAL-ONC-2026-07-20-profiled-copy --out <evidence dir> \
   --real-file <onc-snapshot-20260720.csv, sha256 689472...e9e8d> \
   --nppes-index <backend/var/authoritative/nppes_index.json> \
   --ppef-enrollment <backend/var/authoritative/PPEF_ENROLLMENT__PPEF_Enrollment_Extract_2026.07.17.csv>
```

Outputs (Markdown + JSON) are in `qa-evidence/2026-10-02-sam-trace-and-reporting-plan/lanes/S/`
(outside git) and embedded below.

## 3. Provenance and representativeness — stated plainly

- **`test_report_full_scale`** (intake `9216339b…`, 24,563 records) is the SYNTHETIC full-scale
  benchmark population. It proves throughput and reconciliation; its bucket distribution is not
  representative (every entity is B3 because synthetic NPIs resolve nothing in NPPES; SAM has no
  key so every SAM row is UNAVAILABLE). It carries **51,626 review cases for 24,563 entities**:
  the benchmark re-ran `verify_and_classify` over the same population (chunked-correctness and
  pipelining attempts), so every entity has two cases and the 20,949 `DEFAULT-UNMATCHED`/
  version-0 cases are the pipelining attempt's contention cycle (every source UNAVAILABLE) — the
  behaviour reverted in `da-ra-be@fa4a853`. Rule set there is v2 (that DB was built from a worktree
  without v3).
- **`test_sam`** is the SYNTHETIC shared e2e database (34 one-to-eight-record deliveries); rule
  set v3. Its 16 B4 cases are the SAM e2e scenarios (confirmed exclusion / ambiguous match) by
  construction.
- **`REAL-ONC-2026-07-20`** counts are computed from the real delivered file — the copy whose
  SHA-256 equals `field_map.PROFILED_SHA256` (`689472…e9e8d`, 10,042,400 bytes). A second copy of
  the same file under `C:\ONS HHS\ONC CSV file\` (11,519,804 bytes, sha256 `07b3399d…`) carries
  64 trailing commas on every line (a spreadsheet re-save) but parses to the identical 23,566
  records with identical counts (diffed field-by-field: no differences). The profiled copy is the
  one cited. **Processing failures: none observed on either local database** — the mechanism
  exists and records nothing because nothing failed; this is not evidence about DEV.
- The real-delivery NPI counts are from a pinned NPPES edition (2026-08-09) and a PPEF extract
  (2026-07-17); live NPPES may differ for same-day changes. The address comparison uses
  `address_comparison.compare_to_nppes` (street line/city/state; postal code is not in the index
  so it is uncompared) and therefore is not the same figure as the Phase 6 population run's
  live-API conflict count (10,426); both are stated, neither is "the" number until the
  methodology decision D4 (address materiality) is taken.

## 4. Observation recorded for the owners of the classifier wiring (not changed here)

While building the verification-outcome section it was necessary to keep `REVIEW` and `NOT_FOUND`
apart, because `arc_pipeline._DISPOSITION_TO_STATE` collapses both to the classifier state
`not_found`. On the bulk path, SAM is always screened by legal name (no RCE field carries a UEI)
and LEIE by organisation name whenever the record has no NPI (19.45% of the real delivery); the
evidence layer deliberately grades those clean name screens `NOT_FOUND` (weaker than `PASS`). Under
`SEED_RULES_V3`, `RULE-005` lists `sam_gov == not_found` and `oig_leie == not_found` as B4
disqualifiers, so a clean name screen and a potential debarment are indistinguishable to the
classifier and both classify B4 (reproduction: `lanes/S/repro_v3_name_screen_not_found_disqualifies.out`
— case A, clean SAM name screen: v2 → B1/RULE-001, v3 → B4/RULE-005; case B, no-NPI entity with a
clean LEIE name screen: v3 → B4/RULE-005). Neither local database exhibits it (SAM is UNAVAILABLE
in both without a key), so the inventory tables above are unaffected; it is recorded here so the
inventory's `review` column is read as "potential match pending analyst" and its `not_found`
column as "screened, nothing listed", which is what the evidence rows mean.

## 5. Embedded generated reports

---

### Embedded report: `inventory_SYNTHETIC-24563-test_report_full_scale.md`

#### Exception inventory — SYNTHETIC-24563-test_report_full_scale

Generated by `scripts/exception_inventory.py`. Aggregates only; no delivered values. Three populations are reported in three sections and are never summed: processing failures (a stage errored), verification outcomes (what a source said) and analyst / review cases (human-facing queue items). Data-quality findings carry both the finding count and the unique affected records.

#### Database `test_report_full_scale` — intake `9216339b-e1d7-49b2-bf43-30cfa0346952`

Population entities in scope: **24563**. Deliveries:
| intake_id | delivery_label | record_count |
|---|---|---|
| 9216339b-e1d7-49b2-bf43-30cfa0346952 | SYNTHETIC-CERT-concurrency-test-bf3526cac1 | 24563 |

Classifier rule versions present:
| rule_code | version | bucket | priority | is_active | retired_date |
|---|---|---|---|---|---|
| RULE-001 | 1 | B1 | 10 | False | 2026-10-02 |
| RULE-001 | 2 | B1 | 10 | True | None |
| RULE-002 | 1 | B1 | 20 | False | 2026-10-02 |
| RULE-002 | 2 | B1 | 20 | True | None |
| RULE-003 | 1 | B2 | 30 | False | 2026-10-02 |
| RULE-003 | 2 | B2 | 30 | True | None |
| RULE-004 | 1 | B3 | 40 | False | 2026-10-02 |
| RULE-004 | 2 | B3 | 40 | True | None |
| RULE-005 | 1 | B4 | 5 | False | 2026-10-02 |
| RULE-005 | 2 | B4 | 5 | True | None |

##### 1. Processing failures (stage attempts that did not complete)
_(no rows)_

Failure classes (non-completed, non-skipped stages):
_(no rows)_

Delivery jobs by state/stage:
_(no rows)_

Rule executions by status (a rule that errored is a processing failure, not a finding):
| execution_status | executions | rules | with_error |
|---|---|---|---|
| COMPLETE | 40 | 40 | 0 |

##### 2. Data-quality findings — rule → stage → cause → findings → unique records
| rule | issue_type | severity | stage | category | rule_version | cause | authority | findings | unique_records | delivery_level | deliveries | open | qa_approved |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BUS-002 | TEST_RECORD_SUSPECTED | MEDIUM | QUALITY | BUS | 1.0.0 | Test-artefact detection | HUMAN_REQUIRED | 24563 | 24563 | 0 | 1 | 24563 | 0 |
| BUS-003 | PARTICIPANT_PARENT_IS_QHIN | INFORMATIONAL | QUALITY | BUS | 1.0.0 | Participant parent is its QHIN | NO_CORRECTION | 24563 | 24563 | 0 | 1 | 24563 | 0 |
| CON-003 | ACTIVE_FORMAT_NORMALIZED | INFORMATIONAL | QUALITY | CON | 1.3.0 | active flag (0/1, 0.0/1.0 normalised) | NO_CORRECTION | 24563 | 24563 | 0 | 1 | 24563 | 0 |
| CON-004 | NODE_TYPE_IS_NOT_HIERARCHY | INFORMATIONAL | QUALITY | CON | 1.3.0 | organizationNodeType vocabulary; never hierarchy | NO_CORRECTION | 24563 | 24563 | 0 | 1 | 24563 | 0 |
| CON-005 | ADDRESS_TEXT_IS_A_LABEL | INFORMATIONAL | QUALITY | CON | 1.0.0 | address_text is a label, not an address | NO_CORRECTION | 24563 | 24563 | 0 | 1 | 24563 | 0 |
| DOA-001 | DOA_NOT_AN_OID | MEDIUM | QUALITY | DOA | 1.3.0 | Delegated-authority reference is an OID | HUMAN_REQUIRED | 24563 | 24563 | 0 | 1 | 24563 | 0 |
| FMT-003 | ZIP_STATE_MISMATCH | MEDIUM | QUALITY | FMT | 1.0.0 | ZIP/state consistency | HUMAN_REQUIRED | 17463 | 17463 | 0 | 1 | 17463 | 0 |
| INT-002 | PART_OF_UNRESOLVED | MEDIUM | QUALITY | INT | 1.3.0 | partOf resolves (delivery, registry or QHIN) | HUMAN_REQUIRED | 24563 | 24563 | 0 | 1 | 24563 | 0 |
| SCH-002 | COLUMN_EMPTY_IN_DELIVERY | INFORMATIONAL | QUALITY | SCH | 1.0.0 | Columns delivered entirely empty (dataset-level) | NO_CORRECTION | 1 | 0 | 1 | 1 | 1 | 0 |

By severity:
| severity | findings | unique_records |
|---|---|---|
| INFORMATIONAL | 98253 | 24563 |
| MEDIUM | 91152 | 24563 |

Unique records with at least one finding: **24563**. Findings per record distribution:
| findings_on_record | records |
|---|---|
| 7 | 7100 |
| 8 | 17463 |

By delivery:
| intake_id | findings | unique_records | rules |
|---|---|---|---|
| 9216339b-e1d7-49b2-bf43-30cfa0346952 | 189405 | 24563 | 9 |

##### 3. Verification outcomes — dimension × source × disposition
| evidence_dimension | source | disposition | rows | unique_entities | generations |
|---|---|---|---|---|---|
| ADDRESS | CMS_PPEF_PRACTICE_LOCATION | NOT_FOUND | 51626 | 24563 | 51626 |
| ADDRESS | NPPES | NOT_FOUND | 30677 | 24563 | 30677 |
| ADDRESS | NPPES | UNAVAILABLE | 20949 | 20949 | 20949 |
| ADDRESS | ONC_RCE_SUBMITTED | MATCH | 51626 | 24563 | 51626 |
| EXCLUSION_REVOCATION | CMS_REVOCATION | PASS | 51626 | 24563 | 51626 |
| EXCLUSION_REVOCATION | OIG_LEIE | PASS | 51626 | 24563 | 51626 |
| EXCLUSION_REVOCATION | SAM_GOV | UNAVAILABLE | 51626 | 24563 | 51626 |
| IDENTITY | CMS_PPEF_ENROLLMENT | CORROBORATED | 30677 | 24563 | 30677 |
| IDENTITY | CMS_PPEF_ENROLLMENT | NOT_FOUND | 20949 | 20949 | 20949 |
| IDENTITY | NPPES | NOT_FOUND | 30677 | 24563 | 30677 |
| IDENTITY | NPPES | UNAVAILABLE | 20949 | 20949 | 20949 |
| MEDICARE_ENROLLMENT | CMS_PPEF_ENROLLMENT | NOT_FOUND | 51626 | 24563 | 51626 |
| PROVIDER_ORG_RELATIONSHIP | CMS_PPEF_REASSIGNMENT | NOT_APPLICABLE | 51626 | 24563 | 51626 |
| PROVIDER_ORG_RELATIONSHIP | ONC_RCE_DIRECTORY | NOT_FOUND | 51626 | 24563 | 51626 |
| TEFCA_ALIGNMENT | ONC_RCE_DIRECTORY | PASS | 51626 | 24563 | 51626 |

Per-source distinct entities by outcome (source-state dimensions only; REVIEW is reported separately from NOT_FOUND — a potential match is not 'nothing found'):
| source | attempted | verified | not_found | review | unavailable | failed | insufficient | not_applicable |
|---|---|---|---|---|---|---|---|---|
| CMS_PPEF_ENROLLMENT | 24563 | 24563 | 24563 | 0 | 0 | 0 | 0 | 0 |
| CMS_PPEF_REASSIGNMENT | 24563 | 0 | 0 | 0 | 0 | 0 | 0 | 24563 |
| CMS_REVOCATION | 24563 | 24563 | 0 | 0 | 0 | 0 | 0 | 0 |
| NPPES | 24563 | 0 | 24563 | 0 | 20949 | 0 | 0 | 0 |
| OIG_LEIE | 24563 | 24563 | 0 | 0 | 0 | 0 | 0 | 0 |
| ONC_RCE_DIRECTORY | 24563 | 24563 | 24563 | 0 | 0 | 0 | 0 | 0 |
| SAM_GOV | 24563 | 0 | 0 | 0 | 24563 | 0 | 0 | 0 |

Connector audit rows (`tefca_verifications`):
| source | verification_status | rows | unique_entities |
|---|---|---|---|
| rce_arc_pipeline | verified | 51626 | 24563 |

##### 4. Analyst / review cases — bucket × rule × version
| bucket | rule | rule_version | cases | unique_entities | human_resolved | qa_reportable | assigned |
|---|---|---|---|---|---|---|---|
| B3 | DEFAULT-UNMATCHED | 0 | 20949 | 20949 | 0 | 0 | 0 |
| B3 | RULE-004 | 2 | 30677 | 24563 | 0 | 0 | 0 |

Entities with more than one review case (repeat cycles): **24563**.

Decision events (analyst determinations / QA actions):
_(no rows)_

Sample entities by review status:
_(no rows)_

---

### Embedded report: `inventory_SYNTHETIC-shared-test_sam.md`

#### Exception inventory — SYNTHETIC-shared-test_sam

Generated by `scripts/exception_inventory.py`. Aggregates only; no delivered values. Three populations are reported in three sections and are never summed: processing failures (a stage errored), verification outcomes (what a source said) and analyst / review cases (human-facing queue items). Data-quality findings carry both the finding count and the unique affected records.

#### Database `test_sam` — all deliveries

Population entities in scope: **80**. Deliveries:
| intake_id | delivery_label | record_count |
|---|---|---|
| 44dfd424-4d8d-4441-b56a-c669e018fe0b | SYNTHETIC-CERT-concurrency-test-1d43f38e77 | 8 |
| ef3a5905-7247-4a7d-892f-4067f8ce81ac | SYNTHETIC-CERT-concurrency-test-a502b0cab8 | 6 |
| 46cf459f-c228-4829-9d3c-495e049be9a2 | SYNTHETIC-TRACE-sam-asym-4d7257636e | 1 |
| 28b0f0b4-0043-4882-9beb-ef31ee609acc | SYNTHETIC-TRACE-sam-asym-d041843109 | 1 |
| c8350726-921b-4c70-9340-1d24d1021c68 | SYNTHETIC-TRACE-sam-e2e-0701d42f6a | 1 |
| 7f6afbc6-a211-44e2-906a-7666a1316a9a | SYNTHETIC-TRACE-sam-e2e-0a6863157e | 1 |
| 4492d48e-245d-4d33-a7ad-f8f084db4082 | SYNTHETIC-TRACE-sam-e2e-0b13245ec5 | 1 |
| d956d3e9-fc23-49c0-b30e-3365fb71669f | SYNTHETIC-TRACE-sam-e2e-0b63f1f62b | 1 |
| 5b14d8a7-8172-4c26-806d-16ae6c945c83 | SYNTHETIC-TRACE-sam-e2e-197af73511 | 1 |
| e1fe1d59-667e-471b-82ee-8642811edd91 | SYNTHETIC-TRACE-sam-e2e-1b14ce936a | 1 |
| dff773bb-6de6-40fe-8d1b-0237d24f8f94 | SYNTHETIC-TRACE-sam-e2e-1ba3f88a9d | 1 |
| aca901d9-b7be-4461-899c-b4a6217aee23 | SYNTHETIC-TRACE-sam-e2e-2db1028f8b | 1 |
| 33381ce3-ae73-4bd1-9d7c-3f4a8f611ed0 | SYNTHETIC-TRACE-sam-e2e-342aa6a506 | 1 |
| 9b7f34f9-6753-4bf6-a271-1da7a555c8ee | SYNTHETIC-TRACE-sam-e2e-37a5d12e7a | 1 |
| 9ab15315-d6ef-4344-89bc-c98d0487a28d | SYNTHETIC-TRACE-sam-e2e-41d224db97 | 1 |
| 8151e626-bcc4-4e48-b489-7b121a2b1d2c | SYNTHETIC-TRACE-sam-e2e-43532fce64 | 1 |
| 44a2807f-fffb-46cd-9330-619ef26ad3dd | SYNTHETIC-TRACE-sam-e2e-47b340306f | 1 |
| 0a8eb496-aa7a-4f39-8681-f4e91c032acc | SYNTHETIC-TRACE-sam-e2e-681f8051c8 | 1 |
| 0e1f1990-be09-415c-a231-dcd692bc7243 | SYNTHETIC-TRACE-sam-e2e-68a2307474 | 1 |
| af828380-2437-4c98-9a9d-fb250e0ed51e | SYNTHETIC-TRACE-sam-e2e-768b21bc05 | 1 |
| a5a0e0e5-a5b0-45e5-b6bb-54a4e3cd0571 | SYNTHETIC-TRACE-sam-e2e-78c457774b | 1 |
| 3539de49-68d0-46b6-8b7f-41db05b4ab3e | SYNTHETIC-TRACE-sam-e2e-8058caaa40 | 1 |
| c2da7a9a-d345-4304-b3cf-c61011a51c1e | SYNTHETIC-TRACE-sam-e2e-89ede0f8af | 1 |
| 23472d64-d523-454c-b542-aeb8e042c451 | SYNTHETIC-TRACE-sam-e2e-9bc6c2e03c | 1 |
| 55e425a2-a843-4255-97b5-debfb98d68d9 | SYNTHETIC-TRACE-sam-e2e-bfde58d267 | 1 |
| 6db402f2-936d-4499-af6b-4b701e7686cb | SYNTHETIC-TRACE-sam-e2e-c2952a28d3 | 1 |
| ed9598d5-c7f8-40ac-91e8-b10a045e7a14 | SYNTHETIC-TRACE-sam-e2e-c3eed82b1c | 1 |
| bce22aec-b7c0-40c0-a35e-74bc3af974f5 | SYNTHETIC-TRACE-sam-e2e-ccea73e452 | 1 |
| 5ef6824d-646c-4d7f-a83d-5a22e9a38d2f | SYNTHETIC-TRACE-sam-e2e-d68118846a | 1 |
| 6c34cfe9-15a6-4a4d-ad23-ec391cb92d77 | SYNTHETIC-TRACE-sam-e2e-da258cc82c | 1 |
| 322393ab-c472-4000-a872-dc1e9ba71798 | SYNTHETIC-TRACE-sam-e2e-e25dd05ac1 | 1 |
| ac15175e-28a1-4223-aa36-17c55cc2b249 | SYNTHETIC-TRACE-sam-e2e-e56d74df59 | 1 |
| 555a73fa-311a-4dfa-b414-ee12ce01db5a | SYNTHETIC-TRACE-sam-e2e-e690e8b1d3 | 1 |
| 33b28463-216a-4a1a-8610-3a993c783121 | SYNTHETIC-TRACE-sam-e2e-ff9fe5e369 | 1 |

Classifier rule versions present:
| rule_code | version | bucket | priority | is_active | retired_date |
|---|---|---|---|---|---|
| RULE-001 | 1 | B1 | 10 | False | 2026-10-02 |
| RULE-001 | 2 | B1 | 10 | False | 2026-10-02 |
| RULE-001 | 3 | B1 | 10 | True | None |
| RULE-002 | 1 | B1 | 20 | False | 2026-10-02 |
| RULE-002 | 2 | B1 | 20 | False | 2026-10-02 |
| RULE-002 | 3 | B1 | 20 | True | None |
| RULE-003 | 1 | B2 | 30 | False | 2026-10-02 |
| RULE-003 | 2 | B2 | 30 | False | 2026-10-02 |
| RULE-003 | 3 | B2 | 30 | True | None |
| RULE-004 | 1 | B3 | 40 | False | 2026-10-02 |
| RULE-004 | 2 | B3 | 40 | False | 2026-10-02 |
| RULE-004 | 3 | B3 | 40 | True | None |
| RULE-005 | 1 | B4 | 5 | False | 2026-10-02 |
| RULE-005 | 2 | B4 | 5 | False | 2026-10-02 |
| RULE-005 | 3 | B4 | 5 | True | None |

##### 1. Processing failures (stage attempts that did not complete)
_(no rows)_

Failure classes (non-completed, non-skipped stages):
_(no rows)_

Delivery jobs by state/stage:
_(no rows)_

Rule executions by status (a rule that errored is a processing failure, not a finding):
| execution_status | executions | rules | with_error |
|---|---|---|---|
| COMPLETE | 1360 | 40 | 0 |

##### 2. Data-quality findings — rule → stage → cause → findings → unique records
| rule | issue_type | severity | stage | category | rule_version | cause | authority | findings | unique_records | delivery_level | deliveries | open | qa_approved |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BUS-002 | TEST_RECORD_SUSPECTED | MEDIUM | QUALITY | BUS | 1.0.0 | Test-artefact detection | HUMAN_REQUIRED | 14 | 14 | 0 | 2 | 14 | 0 |
| BUS-003 | PARTICIPANT_PARENT_IS_QHIN | INFORMATIONAL | QUALITY | BUS | 1.0.0 | Participant parent is its QHIN | NO_CORRECTION | 46 | 46 | 0 | 34 | 46 | 0 |
| CON-003 | ACTIVE_FORMAT_NORMALIZED | INFORMATIONAL | QUALITY | CON | 1.3.0 | active flag (0/1, 0.0/1.0 normalised) | NO_CORRECTION | 46 | 46 | 0 | 34 | 46 | 0 |
| CON-004 | NODE_TYPE_IS_NOT_HIERARCHY | INFORMATIONAL | QUALITY | CON | 1.3.0 | organizationNodeType vocabulary; never hierarchy | NO_CORRECTION | 46 | 46 | 0 | 34 | 46 | 0 |
| CON-005 | ADDRESS_TEXT_IS_A_LABEL | INFORMATIONAL | QUALITY | CON | 1.0.0 | address_text is a label, not an address | NO_CORRECTION | 46 | 46 | 0 | 34 | 46 | 0 |
| DOA-001 | DOA_NOT_AN_OID | MEDIUM | QUALITY | DOA | 1.3.0 | Delegated-authority reference is an OID | HUMAN_REQUIRED | 46 | 46 | 0 | 34 | 46 | 0 |
| INT-002 | PART_OF_UNRESOLVED | MEDIUM | QUALITY | INT | 1.3.0 | partOf resolves (delivery, registry or QHIN) | HUMAN_REQUIRED | 46 | 46 | 0 | 34 | 46 | 0 |
| SCH-002 | COLUMN_EMPTY_IN_DELIVERY | INFORMATIONAL | QUALITY | SCH | 1.0.0 | Columns delivered entirely empty (dataset-level) | NO_CORRECTION | 34 | 0 | 34 | 34 | 34 | 0 |

By severity:
| severity | findings | unique_records |
|---|---|---|
| INFORMATIONAL | 218 | 46 |
| MEDIUM | 106 | 46 |

Unique records with at least one finding: **46**. Findings per record distribution:
| findings_on_record | records |
|---|---|
| 6 | 32 |
| 7 | 14 |

By delivery:
| intake_id | findings | unique_records | rules |
|---|---|---|---|
| 44dfd424-4d8d-4441-b56a-c669e018fe0b | 57 | 8 | 8 |
| ef3a5905-7247-4a7d-892f-4067f8ce81ac | 43 | 6 | 8 |
| 23472d64-d523-454c-b542-aeb8e042c451 | 7 | 1 | 7 |
| 28b0f0b4-0043-4882-9beb-ef31ee609acc | 7 | 1 | 7 |
| 322393ab-c472-4000-a872-dc1e9ba71798 | 7 | 1 | 7 |
| 33381ce3-ae73-4bd1-9d7c-3f4a8f611ed0 | 7 | 1 | 7 |
| 33b28463-216a-4a1a-8610-3a993c783121 | 7 | 1 | 7 |
| 3539de49-68d0-46b6-8b7f-41db05b4ab3e | 7 | 1 | 7 |
| 4492d48e-245d-4d33-a7ad-f8f084db4082 | 7 | 1 | 7 |
| 44a2807f-fffb-46cd-9330-619ef26ad3dd | 7 | 1 | 7 |
| 46cf459f-c228-4829-9d3c-495e049be9a2 | 7 | 1 | 7 |
| 555a73fa-311a-4dfa-b414-ee12ce01db5a | 7 | 1 | 7 |
| 55e425a2-a843-4255-97b5-debfb98d68d9 | 7 | 1 | 7 |
| 5b14d8a7-8172-4c26-806d-16ae6c945c83 | 7 | 1 | 7 |
| 5ef6824d-646c-4d7f-a83d-5a22e9a38d2f | 7 | 1 | 7 |
| 6c34cfe9-15a6-4a4d-ad23-ec391cb92d77 | 7 | 1 | 7 |
| 0a8eb496-aa7a-4f39-8681-f4e91c032acc | 7 | 1 | 7 |
| 7f6afbc6-a211-44e2-906a-7666a1316a9a | 7 | 1 | 7 |
| 8151e626-bcc4-4e48-b489-7b121a2b1d2c | 7 | 1 | 7 |
| 9ab15315-d6ef-4344-89bc-c98d0487a28d | 7 | 1 | 7 |
| 9b7f34f9-6753-4bf6-a271-1da7a555c8ee | 7 | 1 | 7 |
| a5a0e0e5-a5b0-45e5-b6bb-54a4e3cd0571 | 7 | 1 | 7 |
| ac15175e-28a1-4223-aa36-17c55cc2b249 | 7 | 1 | 7 |
| aca901d9-b7be-4461-899c-b4a6217aee23 | 7 | 1 | 7 |
| af828380-2437-4c98-9a9d-fb250e0ed51e | 7 | 1 | 7 |
| bce22aec-b7c0-40c0-a35e-74bc3af974f5 | 7 | 1 | 7 |
| c2da7a9a-d345-4304-b3cf-c61011a51c1e | 7 | 1 | 7 |
| c8350726-921b-4c70-9340-1d24d1021c68 | 7 | 1 | 7 |
| d956d3e9-fc23-49c0-b30e-3365fb71669f | 7 | 1 | 7 |
| dff773bb-6de6-40fe-8d1b-0237d24f8f94 | 7 | 1 | 7 |
| e1fe1d59-667e-471b-82ee-8642811edd91 | 7 | 1 | 7 |
| ed9598d5-c7f8-40ac-91e8-b10a045e7a14 | 7 | 1 | 7 |
| 6db402f2-936d-4499-af6b-4b701e7686cb | 7 | 1 | 7 |
| 0e1f1990-be09-415c-a231-dcd692bc7243 | 7 | 1 | 7 |

##### 3. Verification outcomes — dimension × source × disposition
| evidence_dimension | source | disposition | rows | unique_entities | generations |
|---|---|---|---|---|---|
| ADDRESS | CMS_PPEF_PRACTICE_LOCATION | NOT_FOUND | 46 | 46 | 46 |
| ADDRESS | NPPES | NOT_FOUND | 46 | 46 | 46 |
| ADDRESS | ONC_RCE_SUBMITTED | MATCH | 46 | 46 | 46 |
| EXCLUSION_REVOCATION | CMS_REVOCATION | PASS | 46 | 46 | 46 |
| EXCLUSION_REVOCATION | OIG_LEIE | PASS | 46 | 46 | 46 |
| EXCLUSION_REVOCATION | SAM_GOV | PASS | 8 | 8 | 8 |
| EXCLUSION_REVOCATION | SAM_GOV | REVIEW | 16 | 16 | 16 |
| EXCLUSION_REVOCATION | SAM_GOV | UNAVAILABLE | 22 | 22 | 22 |
| IDENTITY | CMS_PPEF_ENROLLMENT | CORROBORATED | 14 | 14 | 14 |
| IDENTITY | CMS_PPEF_ENROLLMENT | NOT_FOUND | 32 | 32 | 32 |
| IDENTITY | NPPES | NOT_FOUND | 14 | 14 | 14 |
| IDENTITY | NPPES | PASS | 32 | 32 | 32 |
| MEDICARE_ENROLLMENT | CMS_PPEF_ENROLLMENT | NOT_FOUND | 46 | 46 | 46 |
| PROVIDER_ORG_RELATIONSHIP | CMS_PPEF_REASSIGNMENT | NOT_APPLICABLE | 46 | 46 | 46 |
| PROVIDER_ORG_RELATIONSHIP | ONC_RCE_DIRECTORY | NOT_FOUND | 46 | 46 | 46 |
| TEFCA_ALIGNMENT | ONC_RCE_DIRECTORY | PASS | 46 | 46 | 46 |

Per-source distinct entities by outcome (source-state dimensions only; REVIEW is reported separately from NOT_FOUND — a potential match is not 'nothing found'):
| source | attempted | verified | not_found | review | unavailable | failed | insufficient | not_applicable |
|---|---|---|---|---|---|---|---|---|
| CMS_PPEF_ENROLLMENT | 46 | 14 | 46 | 0 | 0 | 0 | 0 | 0 |
| CMS_PPEF_REASSIGNMENT | 46 | 0 | 0 | 0 | 0 | 0 | 0 | 46 |
| CMS_REVOCATION | 46 | 46 | 0 | 0 | 0 | 0 | 0 | 0 |
| NPPES | 46 | 32 | 14 | 0 | 0 | 0 | 0 | 0 |
| OIG_LEIE | 46 | 46 | 0 | 0 | 0 | 0 | 0 | 0 |
| ONC_RCE_DIRECTORY | 46 | 46 | 46 | 0 | 0 | 0 | 0 | 0 |
| SAM_GOV | 46 | 8 | 0 | 16 | 22 | 0 | 0 | 0 |

Connector audit rows (`tefca_verifications`):
| source | verification_status | rows | unique_entities |
|---|---|---|---|
| irs | not_checked | 1 | 1 |
| nppes | verified | 1 | 1 |
| oig_leie | verified | 1 | 1 |
| pecos | verified | 1 | 1 |
| rce_arc_pipeline | verified | 46 | 46 |
| sam_gov | not_checked | 1 | 1 |
| state_registry | not_checked | 1 | 1 |

##### 4. Analyst / review cases — bucket × rule × version
| bucket | rule | rule_version | cases | unique_entities | human_resolved | qa_reportable | assigned |
|---|---|---|---|---|---|---|---|
| B1 | RULE-001 | 3 | 1 | 1 | 0 | 0 | 0 |
| B2 | RULE-003 | 3 | 16 | 16 | 0 | 0 | 0 |
| B3 | RULE-004 | 3 | 14 | 14 | 0 | 0 | 0 |
| B4 | RULE-005 | 3 | 16 | 16 | 0 | 0 | 0 |

Entities with more than one review case (repeat cycles): **1**.

Decision events (analyst determinations / QA actions):
_(no rows)_

Sample entities by review status:
_(no rows)_

---

### Embedded report: `inventory_REAL-ONC-2026-07-20-profiled-copy.md`

#### Exception inventory — REAL-ONC-2026-07-20-profiled-copy

Generated by `scripts/exception_inventory.py`. Aggregates only; no delivered values. Three populations are reported in three sections and are never summed: processing failures (a stage errored), verification outcomes (what a source said) and analyst / review cases (human-facing queue items). Data-quality findings carry both the finding count and the unique affected records.

#### NPI — six separate assessments (delivered file, offline evidence)

File `onc-snapshot-20260720.csv` sha256 `689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d`; 23566 physical lines, 23566 parsed to 41 fields, 0 field-count mismatches (preserved, not mapped). None of the six assessments decides another.

##### 1. Syntax
- **records**: 23566
- **npi_present**: 18982
- **npi_absent**: 4584
- **valid**: 18976
- **invalid**: 6
- **invalid_reasons**:
  - **NPI must be exactly 10 digits (got 6)**: 1
  - **NPI must be exactly 10 digits (got 9)**: 2
  - **NPI ...5472 fails Luhn check digit validation**: 1
  - **NPI must contain digits only (got 22 characters)**: 1
  - **NPI ...8355 fails Luhn check digit validation**: 1
- **distinct_valid_npis**: 18669
- **npis_shared_by_more_than_one_record**: 281
- **records_carrying_a_shared_npi**: 588
- **basis**: 45 CFR 162.406 format + CMS check digit (app.services.npi_validator); absence is assessment 4, not a syntax outcome

##### 2. Type fitness
- **distinct_valid_npis_resolved_in_nppes**: 18669
- **distinct_valid_npis_not_in_nppes**: 0
- **entity_type_by_distinct_npi**:
  - **NPI-1 individual**: 1500
  - **NPI-2 organization**: 17146
  - **<blank>**: 23
- **entity_type_by_record**:
  - **NPI-1 individual**: 1504
  - **NPI-2 organization**: 17449
  - **<blank>**: 23
- **basis**: NPPES Entity Type Code is the type authority (45 CFR 162 Type 1 / Type 2); every delivered record is an organisation (sequoiaorgtype), so NPI-2 is the fitting type

##### 3. Identity matching
- **records_with_nppes_record_to_compare**: 18976
- **name_similarity_bands (validation_engine thresholds 0.90/0.70/0.50/0.30)**:
  - **COMPLETELY_DIFFERENT (0.30-0.50)**: 2353
  - **UNRESOLVABLE (<0.30)**: 2210
  - **DBA_VS_LEGAL (0.50-0.70)**: 1975
  - **ABBREVIATION (0.70-0.90)**: 2216
  - **PUNCTUATION (0.50-0.70)**: 80
  - **MATCH (>=0.90)**: 10119
  - **NOT_COMPARED (name missing on one side)**: 23
- **practice_address_comparison (address_comparison.compare_to_nppes, LOCATION only; postal code absent from the index -> uncompared)**:
  - **CONFLICT**: 8584
  - **NORMALIZED_MATCH**: 3299
  - **EXACT_MATCH**: 7070
  - **INSUFFICIENT_DATA**: 23
- **address_conflicting_fields**:
  - **line**: 8330
  - **city**: 1770
  - **state**: 207
- **basis**: NPPES is the identity authority; name bands are the legacy engine's similarity model (methodology Decision D5 pending for the dimension layer); a CONFLICT is REVIEW, never FAIL (address_evidence.py)

##### 4. Own-NPI presence requirement
- **policy_predicate**: quality_rules.npi_required(): sequoiaorgtype in {Participant, Subparticipant} AND hl7orgrole populated AND not in ['HIE/HIO', 'agency', 'payer']
- **records_where_policy_predicate_is_true**: 56
- **of_which_without_npi (NPI-001 NPI_REQUIRED, HIGH)**: 56
- **records_with_blank_hl7orgrole**: 23506
- **blank_role_records_without_npi (NPI-001 NPI_NOT_SUPPLIED, INFO — never held)**: 4524
- **by_sequoiaorgtype**:
  - **Participant**:
    - **records**: 11077
    - **without_npi**: 716
  - **Subparticipant**:
    - **records**: 12489
    - **without_npi**: 3868
- **by_hl7orgrole**:
  - **<blank>**:
    - **records**: 23506
    - **without_npi**: 4524
  - **HIE/HIO**:
    - **records**: 1
    - **without_npi**: 1
  - **agency**:
    - **records**: 2
    - **without_npi**: 2
  - **diagnostics**:
    - **records**: 4
    - **without_npi**: 4
  - **payer**:
    - **records**: 1
    - **without_npi**: 1
  - **provider**:
    - **records**: 52
    - **without_npi**: 52
- **legal_basis_note**: 45 CFR 162.410 requires an NPI of a covered health care provider; no delivered field states covered-provider status, so the affirmative legal requirement is assessment 6, not this predicate. The predicate is AGT policy (internal).

##### 5. Representative-provider evidence
- **records_whose_npi_is_an_individual (NPI-1 on an organisation record -> ENTITY_TYPE_MISMATCH, validation_engine.py; EXCEPTION never auto-match, source_matching.py)**: 1504
- **distinct_individual_npis_involved**: 1500
- **npis_deactivated_without_reactivation**: 23
- **records_carrying_a_deactivated_npi**: 23
- **basis**: an individual's NPI is representative-provider evidence, kept distinct from the organisation's own identity (45 CFR 160.103 provider vs workforce)

##### 6. Covered-provider eligibility
- **prong_1_is_a_health_care_provider**:
  - **by_distinct_npi**:
    - **PROVIDER_ORGANIZATION**: 3538
    - **UNKNOWN**: 13454
    - **INDIVIDUAL_PROVIDER**: 1500
    - **PAYER**: 44
    - **PUBLIC_HEALTH_AGENCY**: 133
  - **by_record**:
    - **PROVIDER_ORGANIZATION**: 3659
    - **UNKNOWN**: 13631
    - **INDIVIDUAL_PROVIDER**: 1504
    - **PAYER**: 44
    - **PUBLIC_HEALTH_AGENCY**: 138
  - **basis**: applicability._taxonomy_category on NPPES primary taxonomy (unambiguous NUCC prefixes only; everything else UNKNOWN)
- **prong_2_conducts_standard_transactions (PPEF enrolment as the only affirmative signal)**:
  - **available**: True
  - **file**: PPEF_ENROLLMENT__PPEF_Enrollment_Extract_2026.07.17.csv
  - **distinct_valid_npis_with_ppef_enrolment**: 17431
  - **of_which_enrolled_as_organisation (ORG_NAME populated)**: 16031
  - **records_with_ppef_enrolment**: 17727
  - **scan_seconds**: 28.1
  - **basis**: CMS PPEF public enrolment extract (quarterly); presence is an affirmative Medicare-relevance signal; absence is never a negative one
- **resolvable_in_principle_population (records with a valid NPI resolved in NPPES)**: 18976
- **irreducibly_unresolved_from_delivered_fields (records with no valid NPI)**: 4590
- **basis**: 45 CFR 160.103 / 162.408 / 162.410: a covered health care provider conducting standard electronic transactions must obtain and use an NPI. No delivered field states either prong; NPPES taxonomy and PPEF enrolment are the available affirmative signals. Unresolved is a statement about the evidence, not a finding.

NPPES evidence: {"available": true, "source": "NPPES", "edition_member": "npidata_pfile_20050523-20260809.csv", "npis_in_index": 18671, "note": "NPPES Data Dissemination monthly file, indexed to the delivery's NPIs by scripts/phase6_population_enrichment.py; a pinned edition, not a live lookup"}
