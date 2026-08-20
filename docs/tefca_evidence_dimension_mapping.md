# ONC/HHS/RCE Field Mapping and CMS Evidence Dimensions

Companion to `cms_pecos_integration_spec.md`. Everything below was established
by inspecting the actual data and probing the live CMS API on 2026-08-19 —
nothing is copied from a specification and assumed.

---

## 1. ONC/HHS/RCE data — what is actually supplied

Inspected: the ONC-shaped entity dataset loaded by `RCEDirectoryConnector`
(30 entities, FHIR R4 `Organization`).

| Present on | Field | Notes |
|---|---|---|
| 30/30 | `resourceType`, `id`, `identifier[]`, `active`, `type[]`, `name`, `telecom[]`, `address[]`, `partOf`, `_qhin` | |
| 4/30 | `alias[]` | |
| 1/30 | `meta.profile[]` | |

### Field → semantic meaning → dimension → authority → corroboration

| ONC/HHS field | Semantic meaning | Dimension | Authoritative source | External corroboration |
|---|---|---|---|---|
| `identifier[system=http://hl7.org/fhir/sid/us-npi].value` | NPI | D1 Identity | **NPPES** | CMS PPEF Enrollment (`NPI`) |
| `identifier[system=urn:docuaction:tefca/identifier].value` | TEFCA identifier | D5 TEFCA Alignment | ONC/RCE (no external authority exists) | — |
| `type[].coding[].code` | TEFCA class (QHIN / Participant / Subparticipant) | D5 | ONC/RCE | — |
| `name` | Legal/organisation name | D1 | NPPES (`legal_name`) | PPEF `ORG_NAME` / `FIRST_NAME`+`LAST_NAME` |
| `address[0]` | Submitted address | D4 Address | ONC submitted value (the subject of review) | NPPES location, PPEF Practice Location, USPS |
| `partOf.reference` | Parent organisation | D6 Relationship | ONC/RCE | PPEF Reassignment (Medicare-specific only) |
| `_qhin` | QHIN attribution | D5 | ONC/RCE | — |
| `active` | Entity active flag | D5 | ONC/RCE | NPPES `status` |
| `telecom[]` | Phone/email (no `url` in the current dataset) | supplemental | — | Entrant website (if a `url` is ever supplied) |

### Fields the specification asks about that ONC does **not** supply

`HCID` · `Exchange Purpose` · any NPI **Type 1 / Type 2 marker** · any
**provider/entity type** beyond the TEFCA class.

These are reported as `NOT_SUPPLIED_BY_ONC` in D5 rather than omitted, and they
are never inferred. Exchange Purpose in particular is never derived from PECOS:
Medicare enrolment data says nothing about why two organisations exchange
information under TEFCA.

**Consequence.** Because ONC supplies no provider/entity type, Medicare
relevance cannot be read off the ONC record. It is derived from NPPES
(enumeration type + taxonomy) with a third state, `UNDETERMINED`, that resolves
to *corroborative* applicability — never to a requirement. See
`app/Tefca/applicability.py`.

---

## 2. CMS — what is actually published

Probed live against `data.cms.gov` on 2026-08-19.

### Available

| Dataset | UUID | Fields (verified) |
|---|---|---|
| Medicare FFS Public Provider Enrollment (PPEF **Enrollment**) | `2457ea29-fc82-48b0-86ec-3b0755de7515` | `NPI`, `MULTIPLE_NPI_FLAG`, `PECOS_ASCT_CNTL_ID`, `ENRLMT_ID`, `PROVIDER_TYPE_CD`, `PROVIDER_TYPE_DESC`, `STATE_CD`, `FIRST_NAME`, `MDL_NAME`, `LAST_NAME`, `ORG_NAME` |
| Revoked Medicare Providers and Suppliers | `a6496a7d-4e19-479a-a9ad-d4c0a49e07c3` | `ENRLMT_ID`, `NPI`, `FIRST_NAME`, `MDL_NAME`, `LAST_NAME`, `ORG_NAME`, `MULTIPLE_NPI_FLAG`, `STATE_CD`, `PROVIDER_TYPE_DESC`, `REVOCATION_RSN`, `REVOCATION_EFCTV_DT`, `REENROLLMENT_BAR_EXPRTN_DT` |

Query contract, verified: `filter[FIELD]=value` (exact match), `size` + `offset`
paging, `/data/stats` for row counts. Keyless. Enrollment holds 2,978,925 rows.

### NOT available — the four other PPEF relational components

`PRACTICE_LOCATION`, `REASSIGNMENT`, `ADDITIONAL_NPIS`, `SECONDARY_SPECIALTY`
are **not served by the CMS data API**. Evidence:

1. The DCAT catalogue (`data.cms.gov/data.json`, 159 datasets) carries a single
   PPEF entry. Its five distributions are three API versions and two CSV
   versions of the *same Enrollment extract* — the CSVs are named
   `PPEF_Enrollment_Extract_2026.07.17.csv` and `...2026.04.01.csv`.
2. All three API distributions were probed. All three return the identical
   11-field Enrollment schema.
3. A catalogue-wide search for `REASGN` / `ENRLMT` / `PRACTICE_LOCATION` /
   `ADDITIONAL_NPI` matched only the **Revalidation** datasets — which the
   specification explicitly forbids substituting for the PPEF relationship.

**How this is handled.** Each component reports `UNAVAILABLE` with the
machine-readable reason `ppef_component_not_published_via_cms_data_api`. No
joins are invented and no data is fabricated. The relational code — the
`ENRLMT_ID` fan-out, the one-to-many collection, the Amendment 5 hop from
`RCV_BNFT_ENRLMT_ID` back to a receiving organisation — is implemented and
tested, so filling in one entry in `PPEF_COMPONENT_DATASETS` turns a component
on with no other change.

An unavailable component is never a verification failure.

---

## 3. Disposition semantics that carry the most weight

| Situation | Result | Never |
|---|---|---|
| No PPEF enrolment found, Medicare-relevant entity | `REVIEW` | `FAIL` |
| No PPEF enrolment found, relevance not established | `NOT_APPLICABLE` | `FAIL` |
| Absent from the revocation dataset | `NO_ACTIVE_REVOCATION_RECORD_FOUND` | "enrolled" / "good standing" |
| Present in the revocation dataset | `REVIEW` pending identity matching | automatic rejection |
| CMS unreachable / 429 / 5xx / malformed | `UNAVAILABLE` | `FAIL` |
| PPEF NPI differs, `MULTIPLE_NPI_FLAG = Y` | `UNRESOLVED_MULTIPLE_NPI` | `CONFLICT` |
| No practice-location row | `NO_PRACTICE_LOCATION` | `FAIL` |
| RCE relationship present, no Medicare reassignment | `REVIEW` or `NOT_APPLICABLE` | `FAIL` |
| RCE and PECOS name different organisations | `REVIEW`, all shown | `CONFLICT` / `FAIL` |
| Website unreachable, blocked, or absent | `UNAVAILABLE` / `NOT_FOUND` | any effect on the determination |

---

## 4. TIN / EIN — documented gap (out of scope)

The public PPEF Enrollment extract exposes **no EIN or TIN field**. The verified
11-field schema is listed above; there is no tax identifier in it, and the
Revocation dataset has none either.

No tax identifier is scraped, derived, inferred or stored anywhere in this
implementation. There is no code path that produces one. If tax-identifier
corroboration is ever required it needs a different, authorised source and a
separate privacy review — it cannot be obtained from this data.

---

## 5. What was deliberately NOT changed

The approved methodology and the shipped application structure are untouched:

* `ValidationEngine`, the B1–B4 bucket rules, the five-element evidence record,
  and confidence/tier logic — unchanged.
* `/api/tefca/reviews`, `/api/tefca/reviews/{id}`, `/api/tefca/connectors/status`
  and every other existing endpoint — unchanged response shapes.
* The AI Control Plane (`app/tefca_registry/ai/`) and FCC Bulletin
  (`app/bulletin_intelligence/`) — not touched.
* TEFCA Registry, Import workflow, RBAC, Reports — not restructured.

The evidence layer is additive and sits alongside them.
