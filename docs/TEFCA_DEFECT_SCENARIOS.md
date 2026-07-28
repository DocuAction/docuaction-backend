# TEFCA Registry — Intentional Defect Scenarios (Phase 1D)

This dataset embeds **36 intentional defects** across the ~176 seeded TEFCA registry entities. They are **latent data problems** — no verification jobs, checks, or findings are created by the seed (those remain at 0). Discovery is a later phase; this document is the ground-truth answer key.

All identifiers are **synthetic** (NPIs in the fake `1234xxxxx` block). Affected entities also carry a `defects` marker inside their `exchange_purposes` JSONB, and relationship-level defects are tagged in the relationship `notes`.

## Identity Defects (12)

| ID | Title | Expected detection | Affected entities |
|----|-------|--------------------|-------------------|
| DEF-ID-001 | Duplicate NPI | npi_duplicate — two active entities share the same NPI. | Northgate Grove Health System (`2814600a`); Clearwater Field Health Plan (`f8449f5c`) |
| DEF-ID-002 | Duplicate HCID | hcid_duplicate — HCID reused across two entities. | Brookfield Bay Health Information Exchange (`f16601c5`); Granite Delta Medical Group (`a8b89a12`) |
| DEF-ID-003 | Missing NPI on treatment entity | identifier_missing — treatment provider has no NPI. | Ironwood Family Clinic (`620fe048`) |
| DEF-ID-004 | Invalid NPI checksum | npi_invalid — NPI fails the Luhn check. | Harbor Landing Data Clearinghouse (`e83a3fad`) |
| DEF-ID-005 | Retired TEFCAID on active entity | identifier_retired — active entity's TEFCAID is retired. | Clearwater Field Health IT Solutions (`5a639814`) |
| DEF-ID-006 | Missing TEFCAID | identifier_missing — mandatory TEFCAID absent. | OH Department of Health (Silverton Park) (`267e92c1`) |
| DEF-ID-007 | Missing HCID | identifier_missing — mandatory HCID absent. | Clearwater Bay Laboratories (`42c53d3c`) |
| DEF-ID-008 | NPI not in NPPES | npi_invalid / not_found — NPI absent from NPPES. | Stonebridge Delta Health System (`d8395718`) |
| DEF-ID-009 | NPI type mismatch (Type 1 on org) | enrollment_mismatch — Type-1 (individual) NPI on an organization. | Riverbend Heights Health Plan (`920cdd35`) |
| DEF-ID-010 | Expired CCN | enrollment_expired — CCN past its end date. | Cascade Heights Health Information Exchange (`09b10161`) |
| DEF-ID-011 | Duplicate TEFCAID across QHINs | identifier_conflict — same TEFCAID under two QHINs. | Highland Point Medical Group (`829a15b3`); Clearwater Field Data Clearinghouse (`816f82b3`) |
| DEF-ID-012 | Multiple active NPIs on one entity | identifier_conflict — >1 active NPI on a single entity. | Evergreen Peak Health IT Solutions (`df6e1c36`) |

### DEF-ID-001 — Duplicate NPI
- **Category:** identity
- **How it is seeded:** Both entities carry NPI 1234599003 (second row has NULL system_uri to bypass the unique index).
- **Expected detection:** npi_duplicate — two active entities share the same NPI.
- **Affected entities:**
  - `2814600a-7a61-5eee-ad07-7f4f58f14724` — Northgate Grove Health System (key `part-001`)
  - `f8449f5c-b4a5-56ba-9374-7d7ce5963c15` — Clearwater Field Health Plan (key `part-002`)

### DEF-ID-002 — Duplicate HCID
- **Category:** identity
- **How it is seeded:** HCID 2.16.840.1.113883.3.9999.00014 shared by both entities.
- **Expected detection:** hcid_duplicate — HCID reused across two entities.
- **Affected entities:**
  - `f16601c5-fbcb-5eaf-89c7-819cdcde9c0b` — Brookfield Bay Health Information Exchange (key `part-003`)
  - `a8b89a12-30c4-5557-abae-ce6f83eba410` — Granite Delta Medical Group (key `part-004`)

### DEF-ID-003 — Missing NPI on treatment entity
- **Category:** identity
- **How it is seeded:** Treatment-purpose provider with no NPI identifier.
- **Expected detection:** identifier_missing — treatment provider has no NPI.
- **Affected entities:**
  - `620fe048-e0a5-5f03-b6cf-ac3d3073588e` — Ironwood Family Clinic (key `sub-001`)

### DEF-ID-004 — Invalid NPI checksum
- **Category:** identity
- **How it is seeded:** NPI 1234500044 has an invalid check digit.
- **Expected detection:** npi_invalid — NPI fails the Luhn check.
- **Affected entities:**
  - `e83a3fad-5ed7-53fd-9967-211091281852` — Harbor Landing Data Clearinghouse (key `part-005`)

### DEF-ID-005 — Retired TEFCAID on active entity
- **Category:** identity
- **How it is seeded:** Entity is active but its (mandatory) TEFCAID is marked retired.
- **Expected detection:** identifier_retired — active entity's TEFCAID is retired.
- **Affected entities:**
  - `5a639814-566f-5e77-9500-ab6f8c5e6f47` — Clearwater Field Health IT Solutions (key `part-006`)

### DEF-ID-006 — Missing TEFCAID
- **Category:** identity
- **How it is seeded:** Mandatory TEFCAID removed.
- **Expected detection:** identifier_missing — mandatory TEFCAID absent.
- **Affected entities:**
  - `267e92c1-7591-516e-a66f-99cfa7c7e32d` — OH Department of Health (Silverton Park) (key `part-007`)

### DEF-ID-007 — Missing HCID
- **Category:** identity
- **How it is seeded:** Mandatory HCID removed.
- **Expected detection:** identifier_missing — mandatory HCID absent.
- **Affected entities:**
  - `42c53d3c-03f5-56cb-b2b2-7bd9ff2544b6` — Clearwater Bay Laboratories (key `part-008`)

### DEF-ID-008 — NPI not in NPPES
- **Category:** identity
- **How it is seeded:** Synthetic NPI 1234508087 that will not resolve in NPPES.
- **Expected detection:** npi_invalid / not_found — NPI absent from NPPES.
- **Affected entities:**
  - `d8395718-7c9b-5c6a-a326-c8b84c873819` — Stonebridge Delta Health System (key `part-009`)

### DEF-ID-009 — NPI type mismatch (Type 1 on org)
- **Category:** identity
- **How it is seeded:** Organization entity carries individual (Type 1) NPI 1234509093.
- **Expected detection:** enrollment_mismatch — Type-1 (individual) NPI on an organization.
- **Affected entities:**
  - `920cdd35-aca8-526f-9d2e-9139edee80b6` — Riverbend Heights Health Plan (key `part-010`)

### DEF-ID-010 — Expired CCN
- **Category:** identity
- **How it is seeded:** CCN 990028 marked expired (ended 2024-12-31).
- **Expected detection:** enrollment_expired — CCN past its end date.
- **Affected entities:**
  - `09b10161-d06f-5521-bee2-79c10451e461` — Cascade Heights Health Information Exchange (key `part-011`)

### DEF-ID-011 — Duplicate TEFCAID across QHINs
- **Category:** identity
- **How it is seeded:** TEFCAID TEFCA-000023 appears under QHINs epicnexus and epicnexus.
- **Expected detection:** identifier_conflict — same TEFCAID under two QHINs.
- **Affected entities:**
  - `829a15b3-8af6-5058-92c9-3323c893ef39` — Highland Point Medical Group (key `part-012`)
  - `816f82b3-ff73-5e44-a0e0-96750c611bab` — Clearwater Field Data Clearinghouse (key `part-013`)

### DEF-ID-012 — Multiple active NPIs on one entity
- **Category:** identity
- **How it is seeded:** Entity carries two active NPIs (1234001364, 1234001372).
- **Expected detection:** identifier_conflict — >1 active NPI on a single entity.
- **Affected entities:**
  - `df6e1c36-e51a-5de2-8dd4-c02f2d61f23d` — Evergreen Peak Health IT Solutions (key `part-014`)

## Hierarchy Defects (12)

| ID | Title | Expected detection | Affected entities |
|----|-------|--------------------|-------------------|
| DEF-HR-001 | Orphan Sub-Participant | orphan_entity — sub-participant with no active parent. | Cascade Diagnostic Laboratory (`c1068f47`) |
| DEF-HR-002 | Circular relationship (A→B→A) | circular_relationship — mutual parent edges. | IN Department of Health (Highland Point) (`d5fae3b8`); Cypress Crossing Laboratories (`8dbedad5`) |
| DEF-HR-003 | Inactive parent with active children | inactive_parent — parent operational_status=inactive, children active. | Northgate Ridge Health System (`c5d6a674`) |
| DEF-HR-004 | Sub-Participant directly under QHIN | broken_hierarchy — sub_participant_of points at a QHIN. | Pinnacle Urgent Care (`e62e1e54`); CommonWell Health Alliance (`a9b8b9a4`) |
| DEF-HR-005 | Merged entity with orphaned children | orphan_entity — children still point to a merged/inactive parent. | Meridian Landing Health Plan (`b37fd3db`); Evergreen Peak Health Information Exchange (`da1e0570`) |
| DEF-HR-006 | QHIN with zero Participants | broken_hierarchy — QHIN has no participants. | Oracle Health (`5e248da8`) |
| DEF-HR-007 | Participant under another Participant | broken_hierarchy — belongs_to targets a Participant. | Meridian Delta Medical Group (`cd5909a1`); Crestline Bay Data Clearinghouse (`59d1ddea`) |
| DEF-HR-008 | Only historical relationships | orphan_entity — entity has only historical (ended) relationships. | Highland Imaging Center (`1ffae011`) |
| DEF-HR-009 | Two active parent relationships | broken_hierarchy — two active sub_participant_of parents. | Beacon Community Pharmacy (`fa8e98ba`) |
| DEF-HR-010 | Entity with zero relationships | orphan_entity — participant has no relationships at all. | OH Department of Health (Silverton Delta) (`a1c1ff8d`) |
| DEF-HR-011 | 4-level deep Sub-Sub-Participant | broken_hierarchy — hierarchy exceeds QHIN>Participant>Sub depth. | Lakeshore Satellite Clinic (`5077d241`); Silverton Behavioral Health (`b8f6760f`) |
| DEF-HR-012 | Soft-deleted parent | inactive_parent — parent is_active=false, children active. | Cypress Landing Laboratories (`32b18ccf`) |

### DEF-HR-001 — Orphan Sub-Participant
- **Category:** hierarchy
- **How it is seeded:** Only sub_participant_of edge set to status=inactive.
- **Expected detection:** orphan_entity — sub-participant with no active parent.
- **Affected entities:**
  - `c1068f47-aa76-53d7-991b-e71673ed5aea` — Cascade Diagnostic Laboratory (key `sub-002`)

### DEF-HR-002 — Circular relationship (A→B→A)
- **Category:** hierarchy
- **How it is seeded:** Two active contracts_with edges form a cycle.
- **Expected detection:** circular_relationship — mutual parent edges.
- **Affected entities:**
  - `d5fae3b8-e071-516a-b327-cf9e5f130850` — IN Department of Health (Highland Point) (key `part-015`)
  - `8dbedad5-b494-505c-9c9a-d92a10e769b5` — Cypress Crossing Laboratories (key `part-016`)

### DEF-HR-003 — Inactive parent with active children
- **Category:** hierarchy
- **How it is seeded:** Parent operational_status=inactive; child sub_participant_of edges remain active.
- **Expected detection:** inactive_parent — parent operational_status=inactive, children active.
- **Affected entities:**
  - `c5d6a674-1043-5edc-bb3e-312f8d0ab70b` — Northgate Ridge Health System (key `part-017`)

### DEF-HR-004 — Sub-Participant directly under QHIN
- **Category:** hierarchy
- **How it is seeded:** Sub-participant's active parent is a QHIN, not a Participant.
- **Expected detection:** broken_hierarchy — sub_participant_of points at a QHIN.
- **Affected entities:**
  - `e62e1e54-fa39-5c31-a913-b99c97c3a31a` — Pinnacle Urgent Care (key `sub-003`)
  - `a9b8b9a4-3aef-5384-9445-29e4c01caa9f` — CommonWell Health Alliance (key `commonwell`)

### DEF-HR-005 — Merged entity with orphaned children
- **Category:** hierarchy
- **How it is seeded:** Parent merged_into target and deactivated; its sub_participant_of children remain.
- **Expected detection:** orphan_entity — children still point to a merged/inactive parent.
- **Affected entities:**
  - `b37fd3db-e9e8-5e47-b309-3703adb3e8c0` — Meridian Landing Health Plan (key `part-018`)
  - `da1e0570-a8df-534e-b792-4fad5d3d66a9` — Evergreen Peak Health Information Exchange (key `part-019`)

### DEF-HR-006 — QHIN with zero Participants
- **Category:** hierarchy
- **How it is seeded:** Oracle Health QHIN intentionally has no Participant relationships.
- **Expected detection:** broken_hierarchy — QHIN has no participants.
- **Affected entities:**
  - `5e248da8-d03d-5d40-88ac-ad36f869b67d` — Oracle Health (key `oraclehealth`)

### DEF-HR-007 — Participant under another Participant
- **Category:** hierarchy
- **How it is seeded:** A Participant belongs_to another Participant instead of a QHIN.
- **Expected detection:** broken_hierarchy — belongs_to targets a Participant.
- **Affected entities:**
  - `cd5909a1-52b4-5326-95de-b84cbe5a3f99` — Meridian Delta Medical Group (key `part-020`)
  - `59d1ddea-655f-5ad7-9fcd-cfe864986414` — Crestline Bay Data Clearinghouse (key `part-021`)

### DEF-HR-008 — Only historical relationships
- **Category:** hierarchy
- **How it is seeded:** Sole sub_participant_of edge is status=historical with a past end_date.
- **Expected detection:** orphan_entity — entity has only historical (ended) relationships.
- **Affected entities:**
  - `1ffae011-87bf-577c-8508-384e6f9c0283` — Highland Imaging Center (key `sub-004`)

### DEF-HR-009 — Two active parent relationships
- **Category:** hierarchy
- **How it is seeded:** Sub-participant has two concurrent active parent edges.
- **Expected detection:** broken_hierarchy — two active sub_participant_of parents.
- **Affected entities:**
  - `fa8e98ba-a9c2-544e-83e4-f4694f28f632` — Beacon Community Pharmacy (key `sub-005`)

### DEF-HR-010 — Entity with zero relationships
- **Category:** hierarchy
- **How it is seeded:** All belongs_to / child edges removed.
- **Expected detection:** orphan_entity — participant has no relationships at all.
- **Affected entities:**
  - `a1c1ff8d-b963-5c7d-b4bd-845d38122bc4` — OH Department of Health (Silverton Delta) (key `part-031`)

### DEF-HR-011 — 4-level deep Sub-Sub-Participant
- **Category:** hierarchy
- **How it is seeded:** A 'child' entity sits under a Sub-Participant (4th level).
- **Expected detection:** broken_hierarchy — hierarchy exceeds QHIN>Participant>Sub depth.
- **Affected entities:**
  - `5077d241-597b-5a30-801b-6f8d983c21cf` — Lakeshore Satellite Clinic (key `child-001`)
  - `b8f6760f-bd93-55cd-ba2d-416c8f1d1a5e` — Silverton Behavioral Health (key `sub-006`)

### DEF-HR-012 — Soft-deleted parent
- **Category:** hierarchy
- **How it is seeded:** Parent is soft-deleted (is_active=false) but child edges remain active.
- **Expected detection:** inactive_parent — parent is_active=false, children active.
- **Affected entities:**
  - `32b18ccf-51d4-570c-99eb-39549abbb9d8` — Cypress Landing Laboratories (key `part-032`)

## Verification Defects (12)

| ID | Title | Expected detection | Affected entities |
|----|-------|--------------------|-------------------|
| DEF-VR-001 | OIG LEIE exclusion | exclusion_leie — Entity's NPI/name matches a (synthetic) OIG LEIE excluded record. | Pinnacle Family Clinic (`e16b9875`) |
| DEF-VR-002 | SAM.gov debarment | exclusion_sam — Entity matches a (synthetic) SAM.gov exclusion/debarment record. | Meridian Diagnostic Laboratory (`e77dc39d`) |
| DEF-VR-003 | PECOS enrollment mismatch | enrollment_mismatch — Entity's enrollment details disagree with PECOS. | Granite Urgent Care (`04932e5c`) |
| DEF-VR-004 | Address mismatch vs NPPES | address_mismatch — Entity street address differs from the NPPES record. | Ironwood Imaging Center (`ebaa8efd`) |
| DEF-VR-005 | Name mismatch vs NPPES | name_mismatch — Entity legal name differs from the NPPES record. | Ironwood Community Pharmacy (`65c36ffa`) |
| DEF-VR-006 | ZIP mismatch (transposed) | zip_mismatch — Entity ZIP has two transposed digits vs NPPES. | Cypress Behavioral Health (`2eb364c4`) |
| DEF-VR-007 | State mismatch | state_mismatch — Entity state differs from the NPPES record. | Willowbrook Family Clinic (`d783d5df`) |
| DEF-VR-008 | NPI not found in NPPES | npi_invalid — Entity NPI does not resolve in NPPES. | Willowbrook Diagnostic Laboratory (`39149717`) |
| DEF-VR-009 | PECOS enrollment expired | enrollment_expired — Entity's PECOS enrollment period has lapsed. | Sterling Urgent Care (`f8529ee1`) |
| DEF-VR-010 | Entity type mismatch | enrollment_mismatch — Entity type disagrees with NPPES taxonomy classification. | Pinnacle Imaging Center (`208c7e16`) |
| DEF-VR-011 | Missing taxonomy code | identifier_missing — Entity has no provider taxonomy code. | Harbor Community Pharmacy (`3df138e4`) |
| DEF-VR-012 | Multiple NPPES records for same NPI | npi_duplicate — The entity's NPI maps to multiple NPPES records. | Willowbrook Behavioral Health (`ad784217`) |

### DEF-VR-001 — OIG LEIE exclusion
- **Category:** verification
- **How it is seeded:** Entity's NPI/name matches a (synthetic) OIG LEIE excluded record.
- **Expected detection:** exclusion_leie — Entity's NPI/name matches a (synthetic) OIG LEIE excluded record.
- **Affected entities:**
  - `e16b9875-8657-5482-b2cb-14dc9bfb3d72` — Pinnacle Family Clinic (key `sub-007`)

### DEF-VR-002 — SAM.gov debarment
- **Category:** verification
- **How it is seeded:** Entity matches a (synthetic) SAM.gov exclusion/debarment record.
- **Expected detection:** exclusion_sam — Entity matches a (synthetic) SAM.gov exclusion/debarment record.
- **Affected entities:**
  - `e77dc39d-fdae-536b-a511-3f11ee1e5b2d` — Meridian Diagnostic Laboratory (key `sub-008`)

### DEF-VR-003 — PECOS enrollment mismatch
- **Category:** verification
- **How it is seeded:** Entity's enrollment details disagree with PECOS.
- **Expected detection:** enrollment_mismatch — Entity's enrollment details disagree with PECOS.
- **Affected entities:**
  - `04932e5c-eec2-5abb-bdde-9ab31a5a16e4` — Granite Urgent Care (key `sub-009`)

### DEF-VR-004 — Address mismatch vs NPPES
- **Category:** verification
- **How it is seeded:** Entity street address differs from the NPPES record.
- **Expected detection:** address_mismatch — Entity street address differs from the NPPES record.
- **Affected entities:**
  - `ebaa8efd-ae14-547b-85e9-644b8a8dab85` — Ironwood Imaging Center (key `sub-010`)

### DEF-VR-005 — Name mismatch vs NPPES
- **Category:** verification
- **How it is seeded:** Entity legal name differs from the NPPES record.
- **Expected detection:** name_mismatch — Entity legal name differs from the NPPES record.
- **Affected entities:**
  - `65c36ffa-7f55-586e-ba7f-fa234c665c36` — Ironwood Community Pharmacy (key `sub-011`)

### DEF-VR-006 — ZIP mismatch (transposed)
- **Category:** verification
- **How it is seeded:** Entity ZIP has two transposed digits vs NPPES.
- **Expected detection:** zip_mismatch — Entity ZIP has two transposed digits vs NPPES.
- **Affected entities:**
  - `2eb364c4-bc6b-5a8a-bf0b-473cfaff9854` — Cypress Behavioral Health (key `sub-012`)

### DEF-VR-007 — State mismatch
- **Category:** verification
- **How it is seeded:** Entity state differs from the NPPES record.
- **Expected detection:** state_mismatch — Entity state differs from the NPPES record.
- **Affected entities:**
  - `d783d5df-ec35-547f-8b5b-449e1708705c` — Willowbrook Family Clinic (key `sub-013`)

### DEF-VR-008 — NPI not found in NPPES
- **Category:** verification
- **How it is seeded:** Entity NPI does not resolve in NPPES.
- **Expected detection:** npi_invalid — Entity NPI does not resolve in NPPES.
- **Affected entities:**
  - `39149717-1bf7-5d60-970d-d1d25299a88e` — Willowbrook Diagnostic Laboratory (key `sub-014`)

### DEF-VR-009 — PECOS enrollment expired
- **Category:** verification
- **How it is seeded:** Entity's PECOS enrollment period has lapsed.
- **Expected detection:** enrollment_expired — Entity's PECOS enrollment period has lapsed.
- **Affected entities:**
  - `f8529ee1-1656-55aa-b837-86678bf9404c` — Sterling Urgent Care (key `sub-015`)

### DEF-VR-010 — Entity type mismatch
- **Category:** verification
- **How it is seeded:** Entity type disagrees with NPPES taxonomy classification.
- **Expected detection:** enrollment_mismatch — Entity type disagrees with NPPES taxonomy classification.
- **Affected entities:**
  - `208c7e16-a667-5b17-89d5-0420db2d1ec6` — Pinnacle Imaging Center (key `sub-016`)

### DEF-VR-011 — Missing taxonomy code
- **Category:** verification
- **How it is seeded:** Entity has no provider taxonomy code.
- **Expected detection:** identifier_missing — Entity has no provider taxonomy code.
- **Affected entities:**
  - `3df138e4-ee2a-503e-96a3-9f5700c6bf54` — Harbor Community Pharmacy (key `sub-017`)

### DEF-VR-012 — Multiple NPPES records for same NPI
- **Category:** verification
- **How it is seeded:** The entity's NPI maps to multiple NPPES records.
- **Expected detection:** npi_duplicate — The entity's NPI maps to multiple NPPES records.
- **Affected entities:**
  - `ad784217-d3f9-5155-9060-4b090cdceaac` — Willowbrook Behavioral Health (key `sub-018`)

