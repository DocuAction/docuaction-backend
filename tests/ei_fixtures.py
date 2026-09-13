"""Synthetic fixtures for the entity-intelligence tests. NO real NPIs, names,
addresses, PII, PHI or Government data. NPIs use the 9999 9xxx xxx range with
a synthetic prefix; every organisation is 'SYNTHETIC ...'."""
from __future__ import annotations

import csv
import io
from typing import Dict, List, Optional

from app.core.entity_intelligence.observations import (DeliveryPath, EvidenceObservation,
                                                        LocationRole, NameKind, ObservationType,
                                                        Provenance, SourceAuthority)
from app.evidence_sources.nppes_v2.schema import (MAIN_FILE_COLUMN_COUNT, OTHER_NAME_COLUMNS,
                                                  PRACTICE_LOCATION_COLUMNS)

PROGRAM_SOURCE = "SYNTHETIC_PROGRAM_DELIVERY"

# ── NPPES V2 file builders ──────────────────────────────────────────────────

MAIN_HEADER = ["NPI", "Entity Type Code", "Replacement NPI", "Employer Identification Number (EIN)",
               "Provider Organization Name (Legal Business Name)", "Provider Last Name (Legal Name)",
               "Provider First Name", "Provider Middle Name", "Provider Name Prefix Text",
               "Provider Name Suffix Text", "Provider Credential Text", "Provider Other Organization Name",
               "Provider Other Organization Name Type Code", "Provider Other Last Name",
               "Provider Other First Name", "Provider Other Middle Name", "Provider Other Name Prefix Text",
               "Provider Other Name Suffix Text", "Provider Other Credential Text",
               "Provider Other Last Name Type Code", "Provider First Line Business Mailing Address",
               "Provider Second Line Business Mailing Address", "Provider Business Mailing Address City Name",
               "Provider Business Mailing Address State Name", "Provider Business Mailing Address Postal Code",
               "Provider Business Mailing Address Country Code (If outside U.S.)",
               "Provider Business Mailing Address Telephone Number", "Provider Business Mailing Address Fax Number",
               "Provider First Line Business Practice Location Address",
               "Provider Second Line Business Practice Location Address",
               "Provider Business Practice Location Address City Name",
               "Provider Business Practice Location Address State Name",
               "Provider Business Practice Location Address Postal Code",
               "Provider Business Practice Location Address Country Code (If outside U.S.)",
               "Provider Business Practice Location Address Telephone Number",
               "Provider Business Practice Location Address Fax Number", "Provider Enumeration Date",
               "Last Update Date", "NPI Deactivation Reason Code", "NPI Deactivation Date",
               "NPI Reactivation Date"] + [f"Column {i}" for i in range(41, MAIN_FILE_COLUMN_COUNT)]
assert len(MAIN_HEADER) == MAIN_FILE_COLUMN_COUNT


def main_row(npi: str, legal: str, *, entity_type: str = "2", other: str = "", other_code: str = "",
             practice: Optional[Dict[str, str]] = None, mailing: Optional[Dict[str, str]] = None,
             enumeration: str = "01/15/2015", last_update: str = "06/01/2026",
             deactivation: str = "", replacement: str = "") -> List[str]:
    row = [""] * MAIN_FILE_COLUMN_COUNT
    row[0], row[1], row[2], row[4] = npi, entity_type, replacement, legal
    row[11], row[12] = other, other_code
    m = mailing or {}
    row[20:26] = [m.get("line1", ""), m.get("line2", ""), m.get("city", ""), m.get("state", ""),
                  m.get("postal_code", ""), m.get("country_code", "US")]
    p = practice or {}
    row[28:34] = [p.get("line1", ""), p.get("line2", ""), p.get("city", ""), p.get("state", ""),
                  p.get("postal_code", ""), p.get("country_code", "US")]
    row[36], row[37], row[39] = enumeration, last_update, deactivation
    return row


def csv_text(header: List[str], rows: List[List[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


OTHER_NAME_HEADER = [c[0] for c in OTHER_NAME_COLUMNS]
PL_HEADER = [c[0] for c in PRACTICE_LOCATION_COLUMNS]


def other_name_row(npi: str, name: str, code: str, created: str = "03/01/2024") -> List[str]:
    return [npi, name, code, created]


def pl_row(npi: str, addr: Dict[str, str], phone: str = "") -> List[str]:
    return [npi, addr.get("line1", ""), addr.get("line2", ""), addr.get("city", ""), addr.get("state", ""),
            addr.get("postal_code", ""), addr.get("country_code", "US"), phone, "", ""]


# ── synthetic addresses ─────────────────────────────────────────────────────

BALTIMORE = {"line1": "100 SYNTHETIC WAY", "line2": "STE 200", "city": "BALTIMORE", "state": "MD", "postal_code": "212010000"}
FREDERICK = {"line1": "55 TEST CLINIC RD", "line2": "", "city": "FREDERICK", "state": "MD", "postal_code": "217010000"}
ROCKVILLE = {"line1": "9 SAMPLE PARKWAY", "line2": "", "city": "ROCKVILLE", "state": "MD", "postal_code": "208500000"}
BALTIMORE_ALT_STREET = {"line1": "300 OTHER STREET", "line2": "", "city": "BALTIMORE", "state": "MD", "postal_code": "212010000"}

# ── program-delivery observations (the subject under review) ───────────────

def delivered(name: str, address: Optional[Dict[str, str]] = None, npi: Optional[str] = "9999900001",
              relationships: Optional[List[Dict[str, str]]] = None, entity_id: str = "ent-1") -> List[EvidenceObservation]:
    prov = Provenance(source_owner="Synthetic program", delivery_path=DeliveryPath.OPERATOR_UPLOAD,
                      source_record_ref="synthetic:1")
    common = dict(canonical_entity_id=entity_id, source_id=PROGRAM_SOURCE,
                  source_authority=SourceAuthority.PROGRAM_DELIVERY, provenance=prov)
    out = [EvidenceObservation(observation_type=ObservationType.NAME, role=NameKind.DELIVERED.value,
                               observed_value={"name": name}, **common)]
    if npi:
        out.append(EvidenceObservation(observation_type=ObservationType.IDENTIFIER, role="NPI",
                                       observed_value={"value": npi}, **common))
    if address:
        out.append(EvidenceObservation(observation_type=ObservationType.LOCATION,
                                       role=LocationRole.DELIVERED_LOCATION.value,
                                       observed_value=dict(address), **common))
    for rel in relationships or []:
        out.append(EvidenceObservation(observation_type=ObservationType.RELATIONSHIP, role=rel["kind"],
                                       observed_value={"related_entity_name": rel["name"]}, **common))
    return out


def unavailable(source_id: str, entity_id: str = "ent-1") -> EvidenceObservation:
    return EvidenceObservation(canonical_entity_id=entity_id, source_id=source_id,
                               observation_type=ObservationType.IDENTIFIER, role="NPI",
                               observed_value={"unavailable": True, "reason": "synthetic outage"},
                               source_authority=SourceAuthority.FEDERAL_REGISTRY,
                               provenance=Provenance(source_owner="synthetic", delivery_path=DeliveryPath.DIRECT_QUERY))


def enable_all(monkeypatch) -> None:
    from app.core.config import settings
    for name in ("ENTITY_INTELLIGENCE_ENABLED", "NPPES_IDENTITY_CORROBORATION_ENABLED",
                 "IQVIA_EVIDENCE_ENABLED"):
        monkeypatch.setattr(settings, name, True)
