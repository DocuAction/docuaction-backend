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
    safe_import_error,
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


class EmptyCSVError(ValueError):
    """No data rows. Separate from a parse failure so the route can answer 422
    with a reason of its own — "0 imported, 0 errors" is indistinguishable from
    a successful no-op, and QA-1.4 is exactly that confusion."""


def _validate_npi_cell(value: Optional[str]) -> None:
    """Reject a malformed NPI at parse time (QA-1.2, QA-1.3).

    This is deliberately enforced HERE, at the CSV boundary, and not in
    fhir_import.validate_for_import — that function flags rather than rejects on
    purpose, because existing seed and RCE records carry NPIs with bad check
    digits and rejecting them would break importing data the rule postdates.
    Operator-supplied CSV is a different population: it is being typed or
    exported now, so a bad NPI there is a mistake to catch rather than history
    to tolerate.

    NPI is optional in this format. A blank cell is not an error; a present but
    invalid one is.
    """
    if value is None or not str(value).strip():
        return
    from app.services.npi_validator import validate_npi

    ok, message = validate_npi(str(value).strip())
    if not ok:
        raise ValueError(f"invalid NPI '{value}': {message}")


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
    _validate_npi_cell(_get(row, "NPI"))

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
            pre_errors.append(safe_import_error(f"Row {i}", ex, "csv parse"))

    # QA-1.4 — checked AFTER the read loop rather than on the raw text, so a
    # header-only file and a file of nothing but blank lines are both caught.
    # Raised rather than returned: a batch record for an import that never had
    # anything to import is noise in the audit trail.
    if total == 0:
        raise EmptyCSVError("File contains no data rows")

    return await persist_import(
        session, source_type="csv", filename=filename, file_checksum=file_checksum,
        file_size=file_size, parsed=parsed, total=total, pre_errors=pre_errors,
        resolve_db_parent=_resolve_tefcaid_parent,
        actor_id=actor_id, actor_email=actor_email, ip_address=ip_address)
