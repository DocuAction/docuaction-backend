# TEFCA ARC — 41-FIELD EXECUTABLE PROCESSING MATRIX

> ## INTERNAL AGT — NOT FOR CLIENT DISTRIBUTION
> Derived from **code, database schema and tests** — not from documentation.
> **No Government row-level values appear here.** All figures are aggregate.

**Contract:** 7571MN26F80064 · HHS/ONC ASTP · **Date:** 2026-08-29
**Delivery assessed:** `ONC-ASTP-2026-08-21` · 23,566 records · 41 columns
**Rule set:** 1.1.0 (32 rules) · **Field map:** 1.0.0

---

## What this document answers

The 41-column *mapping* was verified in Master Step #2 and is not repeated here.
This matrix answers a different question: **for each delivered field, what does
DocuAction actually execute?** — traced through preservation, parsing, DQ,
applicability, curation, canonical mapping, verification, evidence, human review
and reporting.

**Evidence standard:** a field is only marked IMPLEMENTED where a rule function,
a model column, a service path *and* a test exist. Documentation was treated as
intent, never as proof.

## Headline result

| Status | Count |
|---|---:|
| IMPLEMENTED | **18** |
| NO AUTOMATED RULE REQUIRED | **15** |
| ONC METHODOLOGY CONFIRMATION | **8** |
| PARTIALLY IMPLEMENTED | 0 |
| EXTERNAL SOURCE DEPENDENCY | 0 |
| **MISSING** | **0** |
| **Total** | **41** |

Source fields 41 · missing 0 · invented 0 · renamed 0 · duplicated 0.

> **41 source fields does not mean 41 DQ rules.** 15 fields legitimately need
> preservation, canonical mapping or relationship processing and no validation:
> six are empty on every delivered record, three are constant (one distinct
> value across the whole delivery), four are populated on 2–105 records with no
> ONC definition, and two are free-text PII. Inventing checks for them would
> manufacture findings that mean nothing.

---

## The matrix

Legend — **Preservation:** every field is stored verbatim in Area 1
(`rce_source_records.raw_line` + `.parsed`); no DQ rule may alter it.
`*` = rule registered as a deliberate no-op. `d` = delivery-level finding, raised
once per run rather than per record.

| # | Source field | Applicability | Canonical target | Curated column | DQ rule(s) | AUTO_SAFE | Verification source | Evidence dimension | Human-review trigger | Case class | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `id` | REQUIRED | rce_org_oid | rce_org_oid | ID-001 | — | RCE/ONC delivery (authority) | D1_IDENTITY;D5_TEFCA_ALIGNMENT | none | - | IMPLEMENTED | Identity key; 1:1 across all 23,566 records. |
| 2 | `domains` | OPTIONAL | NOT_PROMOTED | — | CON-001* | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | Constant on every record (1 distinct). CON-001 is registered and is a deliberate no-op. |
| 3 | `initiatoronly` | LEGITIMATELY_NULLABLE | rce_attributes | — | — | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | 5 records, 1 distinct, no ONC definition. Preserved verbatim. |
| 4 | `orgManagingOrg` | REQUIRED | managed_by_qhin | org_managing_org | BUS-003;INT-001;INT-003 | — | RCE/ONC delivery (authority) | D5_TEFCA_ALIGNMENT;D6_PROVIDER_ORG_RELATIONSHIP | INT-003 | RELATIONSHIP | IMPLEMENTED | Drives managed_by_qhin; QHIN entities synthesised and marked as such. |
| 5 | `purposesofuse` | LEGITIMATELY_NULLABLE | exchange_purposes | exchange_purposes | CON-002 | — | RCE/ONC delivery (authority) | D5_TEFCA_ALIGNMENT | none | - | IMPLEMENTED | Multi-value parse; T-TREAT/T-TRTMNT variants reported, never merged. |
| 6 | `stateofoperation` | LEGITIMATELY_NULLABLE | rce_attributes | — | — | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | 7 records, 7 distinct. Too sparse and undocumented to drive a rule. |
| 7 | `doa` | LEGITIMATELY_NULLABLE | rce_attributes | — | — | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | 105 records, 32 distinct, no ONC definition. |
| 8 | `transaction` | LEGITIMATELY_NULLABLE | NOT_PROMOTED | — | SCH-002d | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | Empty on every record; reported once by SCH-002 at delivery level. |
| 9 | `delegationRole` | LEGITIMATELY_NULLABLE | rce_attributes | — | — | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | 2 records, 1 distinct. |
| 10 | `organizationNodeType` | LEGITIMATELY_NULLABLE | org_node_type | org_node_type | CON-004 | — | no external authority | — | none | - | IMPLEMENTED | CON-004 guards against reading it as a TEFCA class. |
| 11 | `NPI` | LEGITIMATELY_NULLABLE | npi | npi | NPI-001;NPI-002;NPI-003 | — | NPPES; PECOS/PPEF; OIG LEIE; CMS Revocation | D1_IDENTITY;D2_MEDICARE_ENROLLMENT;D3_EXCLUSION_REVOCATION;D6_PROVIDER_ORG_RELATIONSHIP | NPI-002;NPI-003 | IDENTITY | IMPLEMENTED | Format, CMS Luhn check digit, multi-value. Absence is never adverse. |
| 12 | `NAIC` | LEGITIMATELY_NULLABLE | naic | — | SCH-002d | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | Empty on every record; SCH-002 delivery-level. |
| 13 | `CCN` | LEGITIMATELY_NULLABLE | ccn | — | SCH-002d | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | Empty on every record; SCH-002 delivery-level. |
| 14 | `HCID` | CONDITIONAL | hcid | hcid | ID-002;ID-003 | — | RCE/ONC delivery (authority) | D5_TEFCA_ALIGNMENT | ID-003 | IDENTITY | IMPLEMENTED | Format and duplicate detection; duplicates reported, never merged. |
| 15 | `AAID` | LEGITIMATELY_NULLABLE | aaid | aaid | ID-004* | — | RCE/ONC delivery (authority) | D5_TEFCA_ALIGNMENT | none | - | NO AUTOMATED RULE REQUIRED | ID-004 registered as a deliberate no-op: absence is normal on 68.4% of records. |
| 16 | `TEFCAID` | REQUIRED | tefcaid | tefcaid | ID-005;ID-006 | — | RCE/ONC delivery (authority) | D5_TEFCA_ALIGNMENT | ID-006 | IDENTITY | ONC METHODOLOGY CONFIRMATION | Validated and preserved, but family/group semantics are an open ONC question; uniqueness is NOT imposed. |
| 17 | `active` | REQUIRED | operational_status | operational_status/is_active | CON-003 | — | RCE/ONC delivery (authority) | D5_TEFCA_ALIGNMENT | none | - | IMPLEMENTED | CON-003 vocabulary check; drives operational_status and is_active. |
| 18 | `sequoiaorgtype` | REQUIRED | entity_level | entity_level/sequoia_org_type | BUS-003;CON-004;INT-003;REQ-001 | — | RCE/ONC delivery (authority) | D5_TEFCA_ALIGNMENT | INT-003 | RELATIONSHIP | IMPLEMENTED | REQ-001 + CON-004; drives entity_level and which relationship edges are emitted. |
| 19 | `hl7orgrole` | LEGITIMATELY_NULLABLE | hl7_org_role | hl7_org_role | BUS-001 | — | NPPES taxonomy (applicability input) | D2_MEDICARE_ENROLLMENT | none | - | IMPLEMENTED | BUS-001; also relaxes D2 Medicare applicability for non-provider roles. |
| 20 | `name` | REQUIRED | name | name | BUS-002;REQ-002;FMT-004 | FMT-004 | NPPES; OIG LEIE (name screening) | D1_IDENTITY;D3_EXCLUSION_REVOCATION | BUS-002 | METHODOLOGY | IMPLEMENTED | REQ-002 presence, BUS-002 test-pattern, FMT-004 whitespace. |
| 21 | `alias` | LEGITIMATELY_NULLABLE | display_name | — | SCH-002d | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | Empty on every record; SCH-002 delivery-level. |
| 22 | `phone` | OPTIONAL | rce_attributes | — | FMT-007 | — | no external authority | — | none | - | IMPLEMENTED | FMT-007 added in this pass - the counterpart of FMT-005. |
| 23 | `email` | LEGITIMATELY_NULLABLE | NOT_PROMOTED | — | SCH-002d | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | Empty on every record; SCH-002 delivery-level. |
| 24 | `address_text` | OPTIONAL | rce_attributes | — | CON-005;FMT-004 | FMT-004 | no external authority | — | none | - | IMPLEMENTED | CON-005 guards the literal label carried on 75% of records; FMT-004 whitespace. |
| 25 | `address_line` | REQUIRED | address | address_line | REQ-003;FMT-004 | FMT-004 | USPS (declared, never queried); NPPES/PPEF comparison | D4_ADDRESS | none | - | IMPLEMENTED | REQ-003 completeness; FMT-004 AUTO_SAFE tab normalisation. |
| 26 | `address_city` | REQUIRED | city | address_city | REQ-003;FMT-004 | FMT-004 | USPS (declared, never queried); NPPES/PPEF | D4_ADDRESS | none | - | IMPLEMENTED | REQ-003 completeness; FMT-004. |
| 27 | `address_state` | REQUIRED | state | address_state | FMT-002;FMT-003;REQ-003 | FMT-002 | USPS (declared, never queried); NPPES/PPEF | D4_ADDRESS | FMT-003 | DQ | IMPLEMENTED | FMT-002 AUTO_SAFE case normalisation; FMT-003 ZIP/state; REQ-003. |
| 28 | `address_postalCode` | REQUIRED | zip | address_postal_code | FMT-001;FMT-003;REQ-003 | FMT-001 | USPS (declared, never queried); NPPES/PPEF | D4_ADDRESS | FMT-003 | DQ | IMPLEMENTED | FMT-001 AUTO_SAFE leading-zero restoration; FMT-003; REQ-003. |
| 29 | `address_country` | OPTIONAL | rce_attributes | address_country | — | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | 23,566 populated, 1 distinct value. Constant, exactly like domains. |
| 30 | `partOf` | REQUIRED | sub_participant_of | part_of | BUS-003;INT-002;INT-003 | — | RCE/ONC delivery (authority) | D5_TEFCA_ALIGNMENT;D6_PROVIDER_ORG_RELATIONSHIP | INT-003 | RELATIONSHIP | ONC METHODOLOGY CONFIRMATION | INT-002/003 and BUS-003 implemented; direct-QHIN parentage on 15 records is an open ONC question. |
| 31 | `contact_company` | LEGITIMATELY_NULLABLE | company | — | SCH-002d | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | Empty on every record; SCH-002 delivery-level. |
| 32 | `contact_purpose` | OPTIONAL | contact_purpose | — | — | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | 17,196 populated, 1 distinct value. Constant. |
| 33 | `contact_name` | OPTIONAL | name | — | — | — | no external authority | — | none | - | NO AUTOMATED RULE REQUIRED | PII free text; no deterministic format semantics. |
| 34 | `contact_phone` | OPTIONAL | phone | — | FMT-005 | — | no external authority | — | none | - | IMPLEMENTED | FMT-005 digit-count observation. |
| 35 | `contact_email` | OPTIONAL | email | — | FMT-006 | — | no external authority | — | none | - | IMPLEMENTED | FMT-006 syntax observation. |
| 36 | `contact_address_text` | OPTIONAL | address_text | — | — | — | no external authority | — | none | - | ONC METHODOLOGY CONFIRMATION | Contact-address block is outside current rule scope - ONC question 7. |
| 37 | `contact_address_line` | OPTIONAL | address_line | — | — | — | no external authority | — | none | - | ONC METHODOLOGY CONFIRMATION | Contact-address block outside current rule scope - ONC question 7. |
| 38 | `contact_address_city` | OPTIONAL | address_city | — | — | — | no external authority | — | none | - | ONC METHODOLOGY CONFIRMATION | Contact-address block outside current rule scope - ONC question 7. |
| 39 | `contact_address_state` | OPTIONAL | address_state | — | — | — | no external authority | — | none | - | ONC METHODOLOGY CONFIRMATION | Contact-address block outside current rule scope - ONC question 7. |
| 40 | `contact_address_postalCode` | OPTIONAL | address_postal_code | — | — | — | no external authority | — | none | - | ONC METHODOLOGY CONFIRMATION | 6,978 records carry a value under 5 digits. A zero-restore would mirror FMT-001 but would change 6,978 curated contact addresses; scope is ONC question 7. |
| 41 | `contact_address_country` | OPTIONAL | address_country | — | — | — | no external authority | — | none | - | ONC METHODOLOGY CONFIRMATION | Contact-address block outside current rule scope - ONC question 7. |
---

## Reassessment of the "16 uncovered fields" finding

The August gap assessment reported ~16 of 41 fields without specific DQ-rule
coverage. Re-derived from the rule functions themselves (which fields each rule
actually reads), that number resolves as follows — **it was not 16 defects.**

| Classification | Count | Fields |
|---|---:|---|
| **A. Genuine missing deterministic validation** | **1** | `phone` — **now closed** by FMT-007 |
| **B. No automated rule required** | 7 | `address_country`, `contact_purpose`, `contact_name`, `initiatoronly`, `stateofoperation`, `doa`, `delegationRole` |
| **C. Indirectly covered — the earlier count was wrong** | 2 | `organizationNodeType` (read by CON-004), `hl7orgrole` (read by BUS-001) |
| **D. Relationship / business processing rather than DQ** | 0 | — |
| **E. Verification-driven** | 0 | — |
| **F. ONC methodology confirmation** | 6 | the `contact_address_*` block |
| **G. External source dependency** | 0 | — |
| **Total** | **16** | |

**Correction of record.** Two of the sixteen were never uncovered. `CON-004`
reads `organizationNodeType` and `BUS-001` reads `hl7orgrole`; the earlier count
came from the `validation=` tuples in `field_map.py`, which list the rules a
field is *associated with* and are not the authority on what a rule reads. The
authority is the rule function. Both fields are IMPLEMENTED.

---

## The one deterministic gap, and why it was safe to close

**FMT-007 · `ORG_PHONE_FRAGMENT` · INFORMATIONAL · NO_CORRECTION**

`contact_phone` has had a digit-count observation since 1.0.0 (FMT-005). The
organisation's own `phone` had none — so an identical fragment was recorded in
one field and silently ignored in the other. FMT-007 is an exact mirror of
FMT-005 against `phone`.

It satisfies all ten remediation conditions: the semantics are a digit count, so
nothing is interpreted; the source stays immutable; it never corrects; it is
INFORMATIONAL and can never become an adverse finding; applicability is "only
when populated"; severity and disposition follow FMT-005 exactly; and synthetic
tests prove valid, invalid, blank, boundary and cross-field isolation.

**Read-only Government forecast: it would raise 0 findings on this delivery.**
All 84 populated `phone` values already carry 10 or more digits. The asymmetry
was real; the data has not yet exercised it. It is now closed for future
deliveries.

The rule set is therefore **1.1.0**. Every run records the version and
`rule_config_hash` it executed under, so the delivered population's 36,916
issues remain fully explicable at 1.0.0 — this is a new version, not a rewrite.

---

## What was deliberately NOT implemented

**The contact-address block (6 fields).** 6,978 of 7,191 records carry a
`contact_address_postalCode` under five digits — the same shape FMT-001 repairs
for the organisation address. A mirrored rule was **not** written, for two
reasons that are policy, not engineering:

1. Whether the ARC evaluates the contact-address block at all is **open ONC
   question 7**. Adding a rule would answer it unilaterally.
2. It would create 6,978 AUTO_SAFE corrections to the curated contact address —
   a material change to the delivered representation of 30% of the population,
   made on an assumption nobody has confirmed.

`address_text` shows why this caution is warranted: the field carries a literal
label rather than an address on 75% of records, and CON-005 exists precisely to
stop it being parsed as one. Contact fields deserve the same evidence before a
rule is applied to them.

Also not implemented, as each requires Government authority: TEFCAID uniqueness,
mandatory NPI, direct-QHIN hierarchy as an anomaly, test-pattern exclusion,
TIN/EIN/FEIN verification, and mandatory optional contact fields.

---

## Applicability and verification, per the frozen vocabulary

Confirmed unchanged by this pass:

- `SOURCE_UNAVAILABLE` is **never** translated to `NO_MATCH`. SAM.gov remains
  `SOURCE_UNAVAILABLE`; it is never rendered as clear, pass or no-match.
- A missing NPI is `NOT_APPLICABLE` for Medicare dimensions, never an adverse
  finding. 4,584 records (19.5%) carry none and that is legitimate.
- `NAIC` and `CCN` empty on every record produce one delivery-level observation
  (SCH-002), not 23,566 per-record findings.
- TEFCAID is not assumed row-unique; 43 values are shared across 284 records and
  the family/group reading stays an ONC question.
- **TIN/EIN/FEIN are not among the 41 delivered fields and were not added.**

**Verification sources actually applicable per field** are listed in the matrix.
A connector existing is not sufficient grounds to list it: USPS is *declared* in
the address hierarchy but has never been queried (0 evidence rows), and that is
recorded rather than presented as coverage.

---

## Source preservation — verified for all 41

Every field is preserved verbatim in Area 1. No DQ rule writes to
`rce_source_records`; corrections are written only to `rce_curated_records` with
a `rce_correction_details` row carrying the pre-image, its hash, the rule, the
authority and the actor. Area 1 corpus digest `3af240c30035b17d5d669a2f8ddbd33a`
— `md5(string_agg(record_sha256, '' ORDER BY id))` over `rce_source_records` —
was unchanged throughout this pass.

Only 12 fields are eligible to receive a correction at all
(`_FIELD_TO_CURATED_COLUMN`), and only 3 rules may apply one without a human
(`FMT-001`, `FMT-002`, `FMT-004`). Everything else is recorded and preserved.

---

## Human-review triggers by field

Fields that can raise HUMAN_REQUIRED, with the case classification the review
bridge assigns:

| Field | Rule | Why automation cannot resolve it | Case class |
|---|---|---|---|
| `NPI` | NPI-002, NPI-003 | Choosing among delivered NPIs is an identity decision reserved to ONC | IDENTITY |
| `HCID` | ID-003 | A duplicate identifier is either one organisation twice or a collision — only ONC can say | IDENTITY |
| `TEFCAID` | ID-006 | Family/group semantics unconfirmed | IDENTITY |
| `address_postalCode` / `address_state` | FMT-003 | Nothing in the record establishes which of the two values is wrong | DQ |
| `partOf` / `orgManagingOrg` | INT-003 | Hierarchy interpretation is an ONC question | RELATIONSHIP |
| `name` | BUS-002 | Whether a test-pattern record belongs in the population is population scoping | METHODOLOGY |

A pre-promotion (HELD) record now raises a source-anchored case, so these
triggers reach an analyst even before an entity exists.

---

## Genuine ONC decisions arising from this pass

**No new methodology question was created.** Every ONC dependency this matrix
records maps to one of the five already on the register (TEFCAID semantics,
direct-QHIN parentage, test-pattern records, NPI applicability, EIN/TIN
authority) plus the existing question 7 on contact-address scope. Ordinary
engineering uncertainty was not converted into a Government question.
