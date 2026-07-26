"""
TEFCA registry — CSV import engine (backward compatibility).

Imports entities from a CSV with columns:
  Required: TEFCAID, HCID, EntityName, EntityLevel
  Optional: NPI, CCN, CLIA, EntityType, State, City, ZIP, Address,
            ParentTEFCAID, OperationalStatus

Reuses the shared ``persist_import`` from ``fhir_import`` so CSV and FHIR imports
produce identical structures (entities + identifiers + relationships + versions +
audit), with the same batch tracking, idempotent skip, and error handling.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry.fhir_import import (
    ENTITY_LEVELS, SYSTEM_URI, ParsedEntity, _resolve_tefcaid_parent, persist_import,
)

_DEFAULT_TYPE_BY_LEVEL = {
    "qhin": "health_information_network",
    "participant": "provider",
    "sub_participant": "provider",
    "child": "provider",
}
# Optional identifier columns -> (identifier_type, is_primary)
_ID_COLUMNS = [("TEFCAID", "tefcaid", True), ("HCID", "hcid", False),
               ("NPI", "npi", False), ("CCN", "ccn", False), ("CLIA", "clia", False)]


def _get(row: dict, key: str) -> Optional[str]:
    """Case-insensitive, whitespace-trimmed column access."""
    for k, v in row.items():
        if k and k.strip().lower() == key.lower():
            return (v or "").strip() or None
    return None


def _parse_row(row: dict) -> ParsedEntity:
    tefcaid = _get(row, "TEFCAID")
    hcid = _get(row, "HCID")
    name = _get(row, "EntityName")
    level = (_get(row, "EntityLevel") or "").lower()
    missing = [c for c, val in (("TEFCAID", tefcaid), ("HCID", hcid),
                                ("EntityName", name), ("EntityLevel", level)) if not val]
    if missing:
        raise ValueError("missing required column(s): " + ", ".join(missing))
    if level not in ENTITY_LEVELS:
        raise ValueError(f"invalid EntityLevel '{level}'")

    entity_type = (_get(row, "EntityType") or _DEFAULT_TYPE_BY_LEVEL[level]).lower()
    op_status = (_get(row, "OperationalStatus") or "active").lower()

    identifiers = []
    for col, itype, primary in _ID_COLUMNS:
        val = _get(row, col)
        if val:
            identifiers.append((itype, val, SYSTEM_URI.get(itype), primary))

    return ParsedEntity(
        key=tefcaid, name=name, level=level, entity_type=entity_type,
        operational_status=op_status,
        state=_get(row, "State"), city=_get(row, "City"), zip=_get(row, "ZIP"),
        address=_get(row, "Address"), fhir_resource=None,
        exchange_purposes={"purposes": []}, identifiers=identifiers, endpoints=[],
        parent_ref=_get(row, "ParentTEFCAID"),
    )


async def import_csv(
    session: AsyncSession, csv_text: str, *,
    filename: Optional[str] = None, file_checksum: Optional[str] = None,
    file_size: Optional[int] = None, actor_id=None, actor_email=None, ip_address=None,
) -> dict:
    """Import entities from CSV text (see module docstring for columns)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    parsed, pre_errors, total = [], [], 0
    for i, row in enumerate(reader, start=1):
        if not any((v or "").strip() for v in row.values()):
            continue  # skip blank lines
        total += 1
        try:
            parsed.append(_parse_row(row))
        except Exception as ex:  # noqa: BLE001
            pre_errors.append(f"Row {i}: {ex}")
    return await persist_import(
        session, source_type="csv", filename=filename, file_checksum=file_checksum,
        file_size=file_size, parsed=parsed, total=total, pre_errors=pre_errors,
        resolve_db_parent=_resolve_tefcaid_parent,
        actor_id=actor_id, actor_email=actor_email, ip_address=ip_address)
