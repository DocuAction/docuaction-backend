"""NPPES Data Dissemination V2 layouts — transcribed from the CMS readme, not remembered.

SOURCE DOCUMENTS (verified 2026-09-11 from the CMS weekly V2 bundle)
    NPPES_Data_Dissemination_Readme_v.2.pdf — "Updated: May 12, 2026"
    NPPES_Data_Dissemination_CodeValues.pdf — "February 1, 2025"
    File header rows shipped in NPPES_Data_Dissemination_083126_090626_Weekly_V2.zip
    https://download.cms.gov/nppes/NPI_Files.html

VERSIONS
    "As of 12/24/2024, two versions of these data files will be available as we
    transition to allow additional characters in all first name and legal
    business name fields. Version 1 (original) will provide only the original
    field lengths while Version 2 (v.2) will include the extended field
    lengths for all relevant fields." NPI_Files.html: Version 1 is no longer
    supported as of March 3, 2026. This adapter reads V2 only.

CADENCE
    Monthly full file (NPPES Data Dissemination V.2, e.g. August 2026), monthly
    Deactivated NPI report, weekly incremental files named by date range
    (e.g. 083126_090626). Each zip carries the main data file, the Other Name,
    Practice Location and Endpoint reference files, each with a *_fileheader.csv,
    plus the readme and code-values PDFs.

LINKING KEY
    NPI (10, NUMBER) in every file. The main file holds ONE "Provider Other
    Organization Name" (+ Type Code); the Other Name Reference File holds the
    additional other names for Type 2 NPIs. The main file holds the FIRST
    primary practice location; the Practice Location Reference File holds all
    NON-PRIMARY practice locations for Type 1 and Type 2 NPIs.

WHAT NPPES DOES NOT ESTABLISH
    An NPI is an enumeration. It does not prove licensure, credentialing,
    Medicare enrolment or program compliance, and this adapter never says it does.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

NPPES_V2_README_DATE = "2026-05-12"
NPPES_CODE_VALUES_DATE = "2025-02-01"
SCHEMA_VERSION = "V2-2026-05-12"

#: (column name, max length, data type) — main data file, the organisation-relevant
#: subset in file order (positions are 0-based indexes in the 330-column header).
MAIN_FILE_COLUMNS: List[Tuple[int, str, int, str]] = [
    (0, "NPI", 10, "NUMBER"),
    (1, "Entity Type Code", 1, "NUMBER"),
    (2, "Replacement NPI", 10, "NUMBER"),
    (3, "Employer Identification Number (EIN)", 9, "VARCHAR"),          # not used: EIN/TIN out of scope
    (4, "Provider Organization Name (Legal Business Name)", 100, "VARCHAR"),   # V2: 70 → 100
    (11, "Provider Other Organization Name", 100, "VARCHAR"),            # V2: 70 → 100
    (12, "Provider Other Organization Name Type Code", 1, "VARCHAR"),
    (20, "Provider First Line Business Mailing Address", 55, "VARCHAR"),
    (21, "Provider Second Line Business Mailing Address", 55, "VARCHAR"),
    (22, "Provider Business Mailing Address City Name", 40, "VARCHAR"),
    (23, "Provider Business Mailing Address State Name", 40, "VARCHAR"),
    (24, "Provider Business Mailing Address Postal Code", 20, "VARCHAR"),
    (25, "Provider Business Mailing Address Country Code (If outside U.S.)", 2, "VARCHAR"),
    (28, "Provider First Line Business Practice Location Address", 55, "VARCHAR"),
    (29, "Provider Second Line Business Practice Location Address", 55, "VARCHAR"),
    (30, "Provider Business Practice Location Address City Name", 40, "VARCHAR"),
    (31, "Provider Business Practice Location Address State Name", 40, "VARCHAR"),
    (32, "Provider Business Practice Location Address Postal Code", 20, "VARCHAR"),
    (33, "Provider Business Practice Location Address Country Code (If outside U.S.)", 2, "VARCHAR"),
    (36, "Provider Enumeration Date", 10, "DATE"),
    (37, "Last Update Date", 10, "DATE"),
    (38, "NPI Deactivation Reason Code", 2, "VARCHAR"),
    (39, "NPI Deactivation Date", 10, "DATE"),
    (40, "NPI Reactivation Date", 10, "DATE"),
]
MAIN_FILE_COLUMN_COUNT = 330

#: Other Name Reference File — Exhibit 2-2 (readme v.2, p.19–20).
OTHER_NAME_COLUMNS: List[Tuple[str, int, str]] = [
    ("NPI", 10, "NUMBER"),
    ("Provider Other Organization Name", 100, "VARCHAR"),
    ("Provider Other Organization Name Type Code", 1, "VARCHAR"),
    ("Created Date", 10, "DATE"),   # MM/DD/YYYY
]

#: Practice Location Reference File — Exhibit 2-3 (readme v.2, p.20–21).
#: Header text is reproduced verbatim, including the irregular spacing/hyphens
#: CMS ships, because the parser matches on it.
PRACTICE_LOCATION_COLUMNS: List[Tuple[str, int, str]] = [
    ("NPI", 10, "NUMBER"),
    ("Provider Secondary Practice Location Address- Address Line 1", 55, "VARCHAR"),
    ("Provider Secondary Practice Location Address-  Address Line 2", 55, "VARCHAR"),
    ("Provider Secondary Practice Location Address - City Name", 40, "VARCHAR"),
    ("Provider Secondary Practice Location Address - State Name", 40, "VARCHAR"),
    ("Provider Secondary Practice Location Address - Postal Code", 20, "VARCHAR"),
    ("Provider Secondary Practice Location Address - Country Code (If outside U.S.)", 2, "VARCHAR"),
    ("Provider Secondary Practice Location Address - Telephone Number", 20, "VARCHAR"),
    ("Provider Secondary Practice Location Address - Telephone Extension", 5, "VARCHAR"),
    ("Provider Practice Location Address - Fax Number", 20, "VARCHAR"),
]

#: Code values — Exhibit 1-6 Other Provider Name Type Codes (applies to
#: "Provider Other Organization Name Type Code" and "Provider Other Last Name
#: Type Code"). Entity Name Type Code: I = Individual, O = Organization, B = Both.
OTHER_ORG_NAME_TYPE_CODES: Dict[str, Tuple[str, str]] = {
    "1": ("Former Name", "I"),
    "2": ("Professional Name", "I"),
    "3": ("Doing Business As", "O"),
    "4": ("Former Legal Business Name", "O"),
    "5": ("Other Name", "B"),
}

#: Exhibit 1-1 Entity Type Codes.
ENTITY_TYPE_CODES: Dict[str, str] = {"1": "Individual", "2": "Organization"}


def other_name_kind(type_code: str) -> str:
    """Map the SOURCE'S type code onto the core NameKind. Only code 3 is DBA;
    everything else is what CMS says it is, never re-labelled."""
    return {"3": "DOING_BUSINESS_AS", "4": "FORMER_LEGAL_BUSINESS_NAME", "5": "OTHER_NAME",
            "1": "OTHER_NAME", "2": "OTHER_NAME"}.get((type_code or "").strip(), "UNKNOWN")
