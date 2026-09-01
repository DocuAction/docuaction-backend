# August 21 2026 ONC Delivery — DEV Rehearsal Record, Team Workflow, and ONC Questions

**Environment:** DEV only (`docuaction-dev`, `docuaction-db-dev`). PROD untouched.
**Delivery:** `ONC-ASTP-2026-08-21`, intake `95d78cf6-e5a2-465c-acdc-6e451e05b672`
**Rehearsal dates:** 2026-08-26 / 2026-08-27

Every figure here was measured against the real delivery. No Government row
contents and no credentials appear in this document.

---

## 1. What the delivery actually is

| Property | Value |
|---|---|
| Records | 23,566 |
| Fields | 41 |
| Delimiter | pipe (`\|`) |
| File SHA-256 | `689472073480b1cc…9e9e8d` |
| Received | 2026-08-21 |
| Deliveries in DEV | 1 (never re-ingested) |
| Source hash mismatches | 0 of 23,566, recomputed in-database |
| Corpus digest | `2644075f…4dfe` — unchanged across the entire rehearsal |

Population: 11,077 Participants, 12,489 Subparticipants, 11 QHINs referenced as
`orgManagingOrg` (the QHINs are external referents — no delivered record
describes them, so promotion synthesises an entity for each and marks it
`rce_qhin_synthesised`). 22,594 active, 972 inactive.

### Identifier reality (the load-bearing measurement)

| Field | Populated | Distinct | Unique? | Worst collision |
|---|---:|---:|---|---|
| `id` (RCE org OID) | 23,566 (100%) | 23,566 | **Yes — 1:1** | — |
| `HCID` | 23,566 (100%) | 23,562 | No | 4 values on 2 rows each |
| `TEFCAID` | 23,566 (100%) | 23,325 | No | one value on **69 rows** |
| `NPI` | 18,982 (80.5%) | 18,675 | No | one value on **12 rows** |
| `AAID` | 7,447 (31.6%) | 7,444 | No | sparse *and* colliding |
| `name` | 23,566 | 23,284 | No | 282 duplicate names |
| `name`+ZIP+state | 23,566 | 23,337 | No | 229 collisions |

`id` is the only safe identity key. Everything else is corroboration.

### Fields delivered empty in every record (6)

`transaction`, `NAIC`, `CCN`, `alias`, `email`, `contact_company` — recorded once
for the delivery (rule SCH-002) rather than 23,566 times. Structurally
delivered, semantically absent.

### Sparse but populated

`stateofoperation` 7, `initiatoronly` 5, `delegationRole` 2,
`organizationNodeType` 2, `hl7orgrole` 60, `phone` 84, `doa` 105. **Sparse is not
a defect** — these look optional by design.

---

## 2. Quality run

Existing completed run, not re-run: `rule_set 1.0.0`, `field_map 1.0.0`,
`COMPLETE`, 23,566 evaluated, 36,916 issues, 31 rules all COMPLETE, none
under-evaluated, and `sum(issues_generated)` across rules equals 36,916 exactly.

| Severity | Count | | Authority | Count |
|---|---:|---|---|---:|
| INFORMATIONAL | 35,147 | | NO_CORRECTION | 35,147 |
| LOW | 1,631 | | AUTO_SAFE | 1,631 |
| MEDIUM | 134 | | HUMAN_REQUIRED | 138 |
| HIGH | 4 | | | |

Zero orphan issues. The single issue with a NULL `source_record_id` is SCH-002,
deliberately delivery-scoped.

### The human queue — 138, by class

| Class | Severity | Count |
|---|---|---:|
| `NPI_MALFORMED` | HIGH | 3 |
| `MULTIPLE_NPI_IN_ONE_FIELD` | HIGH | 1 |
| `ZIP_STATE_MISMATCH` | MEDIUM | 100 |
| `SUBPARTICIPANT_PARENTED_TO_QHIN` | MEDIUM | 15 |
| `TEST_RECORD_SUSPECTED` | MEDIUM | 9 |
| `DUPLICATE_HCID` | MEDIUM | 8 |
| `NPI_CHECK_DIGIT_FAILED` | MEDIUM | 2 |

**Highest training value:** the 4 HIGH cases (each an identity question with no
contractor-side answer), then `SUBPARTICIPANT_PARENTED_TO_QHIN` (hierarchy
interpretation) and `TEST_RECORD_SUSPECTED` (population scoping). The 100
`ZIP_STATE_MISMATCH` cases are the best volume-practice set because USPS can
actually settle them.

### Rule coverage boundary (worth knowing)

Quality rules evaluate the **primary** address block. No rule evaluates any
`contact_address_*` field. Consequence: 6,978 records carry a 4-character
`contact_address_postalCode`, of which 6,977 are one repeated contact block
(Elmwood Park NJ), and none is flagged. This is a **methodology scope question**,
not a code defect — recorded, deliberately not "fixed", because adding rules
would invalidate the 36,916 baseline this rehearsal is built on.

---

## 3. Curation (Area 2)

23,566 curated rows, exactly one per source record — 0 orphans, 0 duplicates,
0 uncurated. 21,932 CLEAN, 1,630 CORRECTED, 4 HELD, 0 REJECTED.

1,631 AUTO_SAFE corrections applied, confined to two deterministic rules:
`FMT-001` ZIP leading-zero restoration (1,627) and `FMT-004` embedded tab in
`address_line` (4). Every correction carries `corrected_by`, the issue it cites,
and `original_value_hash` (the staleness guard).

**AUTO_SAFE is distinguishable from human work in the data, not by convention:**
AUTO_SAFE rows carry `approval_actor = NULL`. A human-approved correction
populates it. The two can produce the same curated value and are told apart by
`correction_authority` + `approval_actor` — never by inspecting the value.

Worked example: source `address_postalCode = "2718"` (East Taunton, MA) stays
`"2718"` in Area 1 forever; the curated row reads `"02718"`. Government value
preserved, corrected value stored separately.

---

## 4. Verification and evidence

Targeted, never bulk. 8 identifiers queried through the application's own
connectors.

| Outcome | Count | Example |
|---|---:|---|
| MATCH | 2 | both NPIs on line 12684 enumerate to *El Dorado Clinic, P.A.* |
| CONFLICT | 1 | line 4816 NPI enumerates to *HCA Health Services of Tennessee*, delivered name *Centennial Medical Center* |
| NO_MATCH | 2 | the two check-digit failures are not enumerated in NPPES |
| NOT_QUERYABLE | 3 | 6- and 9-digit values that cannot be NPIs |

Connectors: NPPES OK, OIG LEIE OK, PECOS OK, **SAM.gov UNAVAILABLE** (`api.sam.gov`
not routing — upstream, not a key problem).

**What the evidence does and does not settle.** The `MULTIPLE_NPI` case is the
clearest teaching example in the whole delivery: NPPES enumerates *both*
delivered NPIs to the same organisation, so the authoritative source corroborates
both and decides neither. Picking one would assert which NPI ONC intended — an
identity decision reserved to ONC. An NPPES match never establishes licensure,
credentialing, TEFCA eligibility, absence of exclusion, or full organisational
identity.

---

## 5. Team workflow

1. **Receive** the ONC delivery. Capture the recovery point first.
2. **Preserve** the original file byte-for-byte; hash it.
3. **Validate format** — delimiter, encoding, schema fingerprint.
4. **Create source intake** — records `sha256`, `received_at`, `received_by`.
5. **Parse records** into immutable Area 1.
6. **Run the quality engine** → issue ledger.
7. **Review issues** — triage by severity and correction authority.
8. **Assign review work** — only NEW/CHANGED/CONFLICT records need attention.
9. **Gather authoritative evidence** for the cases a source can actually settle.
10. **Reconcile** — Government value, curated value, external evidence, decision.
11. **QA** — a different principal than the analyst.
12. **Generate report + artifact**.
13. **Preserve the audit trail**.
14. **Close the delivery.**

### Roles

| Role | Level | May do | May not |
|---|---:|---|---|
| **Analyst** (`senior_analyst`) | 5 | curate, verify, propose dispositions, generate reports, promote | perform the QA sign-off |
| **Reviewer** (`reviewer`) | 4 | approve/reject/waive proposals, promote, make determinations | QA sign-off; admin operations |
| **QA** (`qalead`) | 6 | the QA gate, QA queue | — |
| **Program Manager** | 7 | supersede determinations, deliverable submission | user administration |
| **Admin** | 8 | user and role administration | — |

**Note for whoever provisions the team:** the QA *action*
(`POST /api/tefca/arc/reviews/{id}/qa`) requires `qalead` (level 6). A
Contributor (level 2) is refused with 403 — correctly, by design. Provision QA
staff as `qalead`, not `contributor`.

---

## 6. Questions that genuinely need ONC/RCE guidance

Ordered by how much downstream work they unblock. Everything answerable from
NPPES, LEIE, USPS or PECOS has been deliberately excluded.

1. **Hierarchy.** 15 Subparticipants name a QHIN directly in `partOf` rather than
   an intermediate Participant. Is a direct QHIN parent valid, or should
   `partOf` always name the Participant?
2. **TEFCAID semantics.** 43 TEFCAID values are shared across 284 records, one
   across 69. Please confirm TEFCAID identifies an organisation *family* and must
   not be used as a unique organisation key. Our pipeline already treats `id` as
   the identity key on this assumption.
3. **Test records in the production population.** 9 records match test-name
   patterns. Should they be excluded from the reportable population, and by what
   rule — name pattern, the `active` flag, or an ONC-supplied list?
4. **NPI expectations.** 4,584 records (19.5%) carry no NPI. Is an NPI expected
   for every Participant/Subparticipant, or is absence legitimate for
   non-provider organisations? This decides whether absence is ever a finding.
5. **Duplicate HCID.** 4 HCID values appear on 2 records each. One organisation
   recorded twice, or an identifier collision to correct at source?
6. **Empty columns.** 6 of 41 columns are empty in all 23,566 records. In scope
   for future deliveries, or deprecated?
7. **Contact-address scope.** Should the ARC evaluate the contact address block?
   Today the rules cover the primary address only.

None of these is a "bad data" claim. Items 1, 2, 5 and 6 are interpretation;
3 and 4 are population scoping; 7 is methodology scope.

---

## 7. Known limitations of this rehearsal

- **Maker-checker was not demonstrated end-to-end.** Only one principal at role
  ≥ reviewer could authenticate, so no second principal was available to approve
  the analyst's proposals. The 18 proposed cases are deliberately left PROPOSED
  rather than self-approved.
- **Promotion aborts partway** and must be re-run (see
  `monthly_delivery_model.md` §6). It is resumable and leaves the database
  consistent, but it does not complete in one pass, and the synchronous endpoint
  cannot finish a full delivery inside the App Service request ceiling.
- **Separation of duties is enforced only for `QA_REQUIRED` issues.** All 138
  human-gated issues here are `HUMAN_REQUIRED`, for which one reviewer suffices
  and the proposer is not excluded from approving.
- **`rce_issues.resolved_by` is overwritten at each transition**, so the analyst
  who proposed a correction is not recoverable from that table once a reviewer
  acts on it.
