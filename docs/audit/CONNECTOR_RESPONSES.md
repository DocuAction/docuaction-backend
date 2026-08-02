# Authoritative Source Connector Responses

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `706a2f641f3a48f3dc117f57d579ddc82dbd5686` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-01T22:11:55+00:00 |


**Probe NPI:** `1770626038` (Inova Fairfax Hospital — real, NPPES-listed, passes the CMS check digit)


## NPPES — CMS NPI Registry

**Request:** `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&number=1770626038`

**HTTP:** 200  
**result_count:** 1


| Field | Value |
|-------|-------|
| organization_name | INOVA FAIRFAX HOSPITAL |
| enumeration_type | NPI-2 |
| status | A |

**Raw response (truncated):**

```json
{
  "result_count": 1,
  "results": [
    {
      "addresses": [
        {
          "address_1": "12609 LAMP POST LN",
          "address_purpose": "MAILING",
          "address_type": "DOM",
          "city": "POTOMAC",
          "country_code": "US",
          "country_name": "United States",
          "postal_code": "208542314",
          "state": "MD",
          "telephone_number": "301-309-3781"
        },
        {
          "address_1": "3300 GALLOWS RD",
          "address_purpose": "LOCATION",
          "address_type": "DOM",
          "city": "FALLS CHURCH",
          "country_code": "US",
          "country_name": "United States",
          "postal_code": "220423307",
          "state": "VA",
          "telephone_number": "703-776-4132"
        }
      ],
      "basic": {
        "authorized_official_credential": "NP",
        "authorized_official_first_name": "KELLY",
        "authorized_official_last_name": "SOLOMON",
        "authorized_official_middle_name": "ANN",
        "authorized_official_name_prefix": "Mrs.",
        "authorized_official_name_suffix": "--",
        "authorized_official_telephone_number": "7037764132",
        "authorized_official_title_or_position": "Thoracic Nurse Practitioner",
        "enumeration_date": "2007-02-15",
        "last_updated": "2008-10-01",
        "organization_name": "INOVA FAIRFAX HOSPITAL",
        "organizational_subpart": "NO",
        "status": "A"
      },
      "created_epoch": "1171564996000",
      "endpoints": [],
      "enumeration_type": "NPI-2",
      "identifiers": [
        {
          "code": "01",
          "desc": "Other (non-Medicare)",
          "identifier": "0017138748",
          "issuer": "Nurse Practitioner number",
          "state": "VA"
        }
      ],
      "last_updated_epoch": "1222870054000",
      "number": "1770626038",
      "other_names": [],
      "practiceLocations": [],
      "taxonomies": [
        {
          "code": "282N00000X",
          "desc": "General Acute Care Hospital",
          "license": "0017138748",
          "primary": true,
          "state": "VA",
          "taxonomy_group": ""
        }
      ]
    }
  ]
}
```


## PECOS — CMS Provider Enrollment

The PECOS connector resolves enrolment through the same CMS NPI dataset; there is no separate key-less PECOS endpoint. Recorded here as observed rather than implied.

**Request:** `GET https://npiregistry.cms.hhs.gov/api/?version=2.1&number=1770626038` (same dataset, enrolment view)


## OIG LEIE — HHS Exclusion List

**Request:** `GET https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv` (public CSV, key-less)

**HTTP:** 200


**CSV header + first rows (truncated):**

```csv
LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE
"","","","#1 MARKETING SERVICE, INC","OTHER BUSINESS","SOBER HOME","","0000000000","","239 BRIGHTON BEACH AVENUE","BROOKLYN","NY","11235","1128a1","20200319","00000000","00000000",""
"","","","1 BEST CARE, INC","OTHER BUSINESS","HOME HEALTH AGENCY","","0000000000","","2161 UNIVERSITY AVENUE W, STE","SAINT PAUL","MN","55114","1128b5","20230518","00000000","00000000",""
"","","","101 FIRST CARE PHARMACY INC","OTHER BUSINESS","PHARMACY","","1972902351","","C/O 609 W 191ST STREET, APT D","NEW YORK","NY","10040","1128b8","20220320","00000000","00000000",""
"","","","14 LAWRENCE AVE PHARMACY","PHARMACY","","","0000000000","","14 LAWRENCE AVENUE","SMITHTOWN","NY","11787","1128a1","19880830","00000000","00000000",""
"","","","143 MEDICAL EQUIPMENT CO","DME COMPANY","DME - OXYGEN","","0000000000","","701 NW 36 AVENUE","MIAMI","FL","33125","1128b7","19970620","00000000","00000000",""
"","","","149 BALLSTON AVENUE, LLC","OTHER BUSINESS","MARKETING FIRM","","0000000000","","C/O 365 W END AVE, APT 5A","NEW YORK","NY","10024","1128b6","20230227","00000000"
```


An NPI is 'excluded' only when it appears with no reinstatement date. The connector reports `excluded: false` for an NPI absent from this list — which is the good outcome, not a failure.


## As reported by the platform health probe


| Connector | live | status | note |
|-----------|------|--------|------|
| NPPES | True | OK | NPI Registry — CMS/HHS |
| OIG_LEIE | True | OK | Exclusion List — OIG/HHS |
| SAM_GOV | False | UNAVAILABLE | Federal Registration — GSA (requires SAM_GOV_API_KEY) |
| PECOS | True | OK | Provider Enrollment — CMS |
| RCE_DIRECTORY | False | UNAVAILABLE | FHIR R4 — Sequoia Project (key pending Case #00055525) |
| IQVIA_ONEKEY | False | UNAVAILABLE | Provider hierarchy — pending federal ODC |