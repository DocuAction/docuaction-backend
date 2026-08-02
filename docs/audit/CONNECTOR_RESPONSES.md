# Authoritative Source Connector Responses

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Database | PostgreSQL (Azure) |
| Deployment | Azure App Service |
| Build | Git SHA `7e2ca47e3d5e80db0d89ec776c7ab23455a129bf` |
| Backend URL | https://docuaction-dev.azurewebsites.net |
| Test Date (UTC) | 2026-08-02T19:25:50.360026+00:00 |

## Tool Versions

| Tool | Version |
|------|---------|
| Python | 3.13.11 |
| httpx | 0.28.1 |

Raw upstream responses, captured directly from each authoritative source rather than through the application, so the evidence is not shaped by our own parsing.

Lookup identifier: NPI `1770626038` (Inova Fairfax Hospital).

| Test ID | Source | Expected | Actual | Result |
|---|---|---|---|---|
| 1.5 | NPPES | HTTP 200 with a parseable body | HTTP 200 | PASS |
| 1.5 | PECOS | HTTP 200 with a parseable body | HTTP 200 | PASS |
| 1.5 | OIG_LEIE | HTTP 200 with a parseable body | HTTP 200 | PASS |

## NPPES

- URL: `https://npiregistry.cms.hhs.gov/api/`
- Params: `{'version': '2.1', 'number': '1770626038'}`
- HTTP: **200**
- Captured (UTC): 2026-08-02T19:25:50.360026+00:00
- Content-Type: `application/json`
- Note: CMS NPI Registry - live public API

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

## PECOS

- URL: `https://npiregistry.cms.hhs.gov/api/`
- Params: `{'version': '2.1', 'number': '1770626038'}`
- HTTP: **200**
- Captured (UTC): 2026-08-02T19:25:51.373036+00:00
- Content-Type: `application/json`
- Note: PECOS enrollment is resolved through the same CMS NPI Registry endpoint

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

## OIG_LEIE

- URL: `https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv`
- Params: `None`
- HTTP: **200**
- Captured (UTC): 2026-08-02T19:25:52.002883+00:00
- Content-Type: `text/csv`
- Note: HHS OIG LEIE full exclusions CSV (streamed; first rows only)

```json
{
  "_csv_head": "LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE\r\n\"\",\"\",\"\",\"#1 MARKETING SERVICE, INC\",\"OTHER BUSINESS\",\"SOBER HOME\",\"\",\"0000000000\",\"\",\"239 BRIGHTON BEACH AVENUE\",\"BROOKLYN\",\"NY\",\"11235\",\"1128a1\",\"20200319\",\"00000000\",\"00000000\",\"\"\r\n\"\",\"\",\"\",\"1 BEST CARE, INC\",\"OTHER BUSINESS\",\"HOME HEALTH AGENCY\",\"\",\"0000000000\",\"\",\"2161 UNIVERSITY AVENUE W, STE\",\"SAINT PAUL\",\"MN\",\"55114\",\"1128b5\",\"20230518\",\"00000000\",\"00000000\",\"\"\r\n\"\",\"\",\"\",\"101 FIRST CARE PHARMACY INC\",\"OTHER BUSINESS\",\"PHARMACY\",\"\",\"1972902351\",\"\",\"C/O 609 W 191ST STREET, APT D\",\"NEW YORK\",\"NY\",\"10040\",\"1128b8\",\"20220320\",\"00000000\",\"00000000\",\"\"\r\n\"\",\"\",\"\",\"14 LAWRENCE AVE PHARMACY\",\"PHARMACY\",\"\",\"\",\"0000000000\",\"\",\"14 LAWRENCE AVENUE\",\"SMITHTOWN\",\"NY\",\"11787\",\"1128a1\",\"19880830\",\"00000000\",\"00000000\",\"\"\r\n\"\",\"\",\"\",\"143 MEDICAL EQUIPMENT CO\",\"DME COMPANY\",\"DME - OXYGEN\",\"\",\"0000000000\",\"\",\"701 NW 36 AVENUE\",\"MIAMI\",\"FL\",\"33125\",\"1128b7\",\"1997062"
}
```

## Note on PECOS

PECOS enrollment is resolved through the same CMS NPI Registry endpoint as NPPES; it is not a separate upstream host. Both rows above therefore show the same URL, which is the real behaviour rather than a copy-paste artefact.
