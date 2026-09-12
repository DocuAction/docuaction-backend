# NPPES Data Dissemination V2 — identity mapping

Sources verified 2026-09-11 from the CMS weekly bundle `NPPES_Data_Dissemination_083126_090626_Weekly_V2.zip` (download.cms.gov/nppes/NPI_Files.html): `NPPES_Data_Dissemination_Readme_v.2.pdf` (Updated May 12, 2026), `NPPES_Data_Dissemination_CodeValues.pdf` (February 1, 2025), and the four `*_fileheader.csv` files.

## Versions and cadence

- "As of 12/24/2024, two versions … Version 1 (original) will provide only the original field lengths while Version 2 (v.2) will include the extended field lengths for all relevant fields." Version 1 no longer supported as of March 3, 2026. **DocuAction reads V2 only.**
- Monthly full file (e.g. `NPPES_Data_Dissemination_August_2026_V2.zip`), monthly Deactivated NPI report, weekly incremental files by date range (e.g. `083126_090626`). Each zip contains: `npidata_pfile_*.csv`, `othername_pfile_*.csv`, `pl_pfile_*.csv`, `endpoint_pfile_*.csv`, each with a `*_fileheader.csv`, plus the readme and code-values PDFs.
- Values are double-quoted CSV; embedded double quotes are replaced by single quotes.

## Main data file (330 columns; organisation-relevant subset)

| Idx | Column | Max | Type |
|---|---|---|---|
| 0 | NPI | 10 | NUMBER |
| 1 | Entity Type Code (1 Individual, 2 Organization) | 1 | NUMBER |
| 2 | Replacement NPI | 10 | NUMBER |
| 4 | Provider Organization Name (Legal Business Name) | **100** (V2; V1 was 70) | VARCHAR |
| 11 | Provider Other Organization Name | **100** | VARCHAR |
| 12 | Provider Other Organization Name Type Code | 1 | VARCHAR |
| 20–25 | Business Mailing Address line 1 (55), line 2 (55), city (40), state (40), postal code (20), country code (2) | | |
| 28–33 | Business Practice Location Address line 1 (55), line 2 (55), city (40), state (40), postal code (20), country code (2) | | |
| 36 | Provider Enumeration Date | 10 | DATE MM/DD/YYYY |
| 37 | Last Update Date | 10 | DATE |
| 38–40 | NPI Deactivation Reason Code (2), Deactivation Date, Reactivation Date | | |

EIN (idx 3) is out of scope and not read.

## Other Name Reference File (Exhibit 2-2)

"NPPES now collects multiple Other Names associated with Type 2 NPIs." One row per additional other name.

| Column | Max | Type |
|---|---|---|
| NPI | 10 | NUMBER |
| Provider Other Organization Name | 100 | VARCHAR |
| Provider Other Organization Name Type Code | 1 | VARCHAR |
| Created Date | 10 | DATE |

The main file carries one other name (idx 11/12); the reference file carries the rest. Both are read.

## Practice Location Reference File (Exhibit 2-3)

"The Data File contains the first Primary Practice Location, and the Practice Location Reference File will contain all of the non-primary Practice Locations." Applies to Type 1 and Type 2 NPIs. Header text is reproduced verbatim in `schema.py` (CMS ships irregular spacing/hyphens):

NPI (10) · Address Line 1 (55) · Address Line 2 (55) · City Name (40) · State Name (40) · Postal Code (20) · Country Code (2) · Telephone Number (20) · Telephone Extension (5) · Fax Number (20).

## Code values (Exhibit 1-6, Other Provider Name Type Codes)

| Code | Description | Entity name type |
|---|---|---|
| 1 | Former Name | I |
| 2 | Professional Name | I |
| 3 | Doing Business As | O |
| 4 | Former Legal Business Name | O |
| 5 | Other Name | B |

Observed in the public weekly sample: codes 3, 5 and 4 only for organisations (5,692 rows; 1,978 NPIs with more than one non-primary location).

## Mapping to Core observations

| NPPES field | ObservationType | role | observed_value keys | signal note |
|---|---|---|---|---|
| NPI (+ Entity Type, Replacement NPI, deactivation) | IDENTIFIER | `NPI` | value, entity_type, replacement_npi, deactivation_date, reactivation_date | `NPPES_NPI_OBSERVED` |
| Legal Business Name | NAME | `LEGAL_BUSINESS_NAME` | name | `NPPES_LEGAL_BUSINESS_NAME_OBSERVED` |
| Other Organization Name, code 3 | NAME | `DOING_BUSINESS_AS` | name, type_code, type_description, created_date, signal | `NPPES_OTHER_NAME_OBSERVED` + `DBA_RELATIONSHIP_IDENTIFIED` |
| Other Organization Name, code 4 | NAME | `FORMER_LEGAL_BUSINESS_NAME` | name, type_code, … | `NPPES_OTHER_NAME_OBSERVED` (never DBA) |
| Other Organization Name, code 5 (or 1/2) | NAME | `OTHER_NAME` | name, type_code, … | `NPPES_OTHER_NAME_OBSERVED` (never DBA) |
| Business Practice Location (main file) | LOCATION | `PRIMARY_PRACTICE_LOCATION` | line1, line2, city, state, postal_code, country_code | `NPPES_PRIMARY_PRACTICE_LOCATION_OBSERVED` |
| Practice Location Reference rows | LOCATION | `ADDITIONAL_PRACTICE_LOCATION` | … + telephone | `NPPES_ADDITIONAL_PRACTICE_LOCATION_OBSERVED` |
| Business Mailing Address | LOCATION | `MAILING_LOCATION` | … | `NPPES_MAILING_LOCATION_OBSERVED` |

Provenance on every observation: source owner "CMS NPPES", delivery path FILE_DOWNLOAD, `SourceVersionRef` with dataset version (bundle name), file SHA-256, retrieval time, `source_record_ref` = file kind : line number : field, parser version `nppes-v2-1.0`, schema `V2-2026-05-12`.

## What NPPES does not establish

An NPI proves enumeration. Nothing here represents NPPES as proving licensure, credentialing, Medicare enrolment or TEFCA compliance; the identifier explanation template says so.

## Not yet built

Acquisition (downloading the monthly/weekly zips and preserving them through `app.core.ingestion.contracts`), Type 1 handling, endpoint file, deactivation report reconciliation.
