"""Field-level findings export (grain B) - methodology v1.3-A (REVISED), Appendix F3.

The complete record-level B1-B4 master (october_review_dataset) and this file have DIFFERENT
grains and reconcile independently: one record can have many findings, no deduplication by
AGT_Record_ID, Field_ID may repeat, a passing record yields no row (nothing is manufactured).

Source of every definition below: F3.1 (18 headers, in order), F3.2 (Field_ID and B2
vocabularies), F3.3 (issue crosswalk), F3.4 (execution dates). The methodology is DRAFT / NOT
FINAL; its acceptance and effective date are not recorded in the decision register, so this
module implements the specification as written and does not treat it as accepted.

Rules this module keeps
  * Field_Discrepancy_Class is left BLANK: the specification says "leave blank if not
    established; do not invent a class", and no class is established in retained evidence.
  * NPI is the ORIGINAL submitted NPI verbatim; never filled from a supplemental candidate.
  * Entity_Type is Participant / Subparticipant from sequoiaorgtype (spelling variants
    normalised); anything else is blank with a reason in Field_Reason.
  * NAIC_ON_NON_PAYER is excluded from this export (history stays in the evidence store) and
    counted.
  * An issue type that is not in the crosswalk produces NO row; it is counted and listed.
  * Field_ID is exported only as one of the 13 controlled names; otherwise blank, with a
    mapping exception recorded and the reason stated in Field_Reason.
  * Reference_Value / Reference_Source only when the finding carries an attributable source.
  * Reference_Source_Date is the date the finding was produced by its run (the retained check
    event); blank, with a stated limitation, when no run/event is retained. It is never the
    snapshot, source-edition, assessment or export date.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, Iterable, List, Optional

FIELD_HEADERS = [
    "Snapshot_Date", "Source_Line", "QHIN_ID", "HCID", "TEFCA_ID", "Entity_Type", "NPI",
    "AGT_Record_ID", "Field_ID", "Field_Discrepancy_Class", "Submitted_Value", "Reference_Value",
    "Reference_Source", "Reference_Source_Date", "B2_Subclassifications", "Issue_Type",
    "Field_Reason", "Purpose_of_Use",
]

FIELD_IDS = frozenset("""HCID TEFCAID active NPI purposesofuse name address_text address_line
address_city address_state address_postalCode address_country partof""".split())
B2_VALUES = frozenset("MISSPELLING WHITE_SPACE CAPITALIZATION ABBREVIATION WRONG_ATTRIBUTE OTHER".split())

EXCLUDED_ISSUE_TYPES = frozenset({"NAIC_ON_NON_PAYER"})
ACTUAL = "ACTUAL"            # Field_ID is "the actual (evidenced) field"
NOT_SPECIFIED = None         # Stephen's dash: no Field_ID
#: original AGT issue type -> (client Issue_Type, Field_ID rule). Rule: a controlled name,
#: ACTUAL, NOT_SPECIFIED, or a tuple of allowed names for the issue's own field.
CROSSWALK: Dict[str, tuple] = {
    "ADDRESS_TEXT_IS_A_LABEL": ("B2_OTHER", ACTUAL),
    "COLUMN_EMPTY_IN_DELIVERY": ("MISSING_ENTRY", ACTUAL),
    "DOA_NOT_AN_OID": ("DOA_NOT_AN_OID", "partof"),
    "DUPLICATE_HCID": ("DUPLICATE_HCID", "HCID"),
    "EMBEDDED_TAB": ("B2_WHITE_SPACE", ACTUAL),
    "IDENTIFIER_EXISTING_VALUE_CONFLICT": ("IDENTIFIER_EXISTING_VALUE_CONFLICT", NOT_SPECIFIED),
    "INACTIVE_NEW_ENTRANT": ("INACTIVE_RECORD", "active"),
    "INACTIVE_RECORD": ("INACTIVE_RECORD", "active"),
    "MISSING_PURPOSES_OF_USE": ("MISSING_ENTRY", "purposesofuse"),
    "MULTIPLE_NPI_IN_ONE_FIELD": ("MULTI_ENTRY", "NPI"),
    "NODE_TYPE_IS_NOT_HIERARCHY": ("NODE_TYPE_IS_NOT_HIERARCHY", NOT_SPECIFIED),
    "NON_PROVIDER_ORGANISATION": ("NON_PROVIDER_ORGANISATION", NOT_SPECIFIED),
    "NPI_APPLICABILITY_UNRESOLVED": ("NPI_APPLICABILITY_UNRESOLVED", "NPI"),
    "NPI_CHECKSUM_INVALID": ("NPI_INVALID", "NPI"),
    "NPI_EXISTING_VALUE_CONFLICT": ("NPI_INVALID", "NPI"),
    "NPI_NOT_SUPPLIED": ("MISSING_ENTRY", "NPI"),
    "NPI_REQUIRED": ("MISSING_ENTRY", "NPI"),
    "PARTICIPANT_PARENT_IS_QHIN": ("PARTICIPANT_PARENT_IS_QHIN", "partof"),
    "PURPOSE_TOKEN_VARIANT": ("PURPOSE_TOKEN_VARIANT", "purposesofuse"),
    "SAM_EXTRACT_CANDIDATE": ("SAM_EXTRACT_CANDIDATE", NOT_SPECIFIED),
    "SHARED_TEFCAID": ("DUPLICATE_TEFCAID", "TEFCAID"),
    "SUBPARTICIPANT_PARENTED_TO_QHIN": ("SUBPARTICIPANT_PARENTED_TO_QHIN", "partof"),
    "TEST_RECORD_SUSPECTED": ("TEST_RECORD_SUSPECTED", NOT_SPECIFIED),
    "UNKNOWN_PURPOSE_TOKEN": ("UNKNOWN_PURPOSE_TOKEN", "purposesofuse"),
    "ZIP_LEADING_ZERO_STRIPPED": ("B2_OTHER", "address_postalCode"),
    "ZIP_STATE_MISMATCH": ("B2_OTHER", ("address_postalCode", "address_state")),
}
#: Client issue types that name a supported B2 subclassification (the label "B2_*" alone never
#: assigns a record to B2).
B2_FROM_ISSUE_TYPE = {"B2_WHITE_SPACE": "WHITE_SPACE", "B2_OTHER": "OTHER"}

#: Delivered/ledger field names -> controlled Field_ID (exact, case-sensitive after strip; the
#: ledger is not guessed at: an unknown name stays blank with a mapping exception).
_FIELD_ALIASES = {
    "hcid": "HCID", "tefcaid": "TEFCAID", "tefca_id": "TEFCAID", "active": "active", "npi": "NPI",
    "purposesofuse": "purposesofuse", "purposes_of_use": "purposesofuse", "name": "name",
    "address_text": "address_text", "address_line": "address_line", "address_city": "address_city",
    "address_state": "address_state", "address_postalcode": "address_postalCode",
    "address_country": "address_country", "partof": "partof",
}


def field_id_for(name: Optional[str]) -> Optional[str]:
    return _FIELD_ALIASES.get((name or "").strip().lower())


def entity_type(participation_raw: Optional[str]) -> str:
    from app.tefca_registry.rce.npi_applicability import normalize_participation_level
    level = normalize_participation_level(participation_raw)
    return {"participant": "Participant", "sub_participant": "Subparticipant"}.get(level or "", "")


def _s(v: Any) -> str:
    return "" if v is None else str(v)


#: Reference sources a client may be shown. Internal normalisation labels the rules write into
#: `suggested_source` (ZERO_PAD, WHITESPACE_NORMALISE, OBSERVED_FREQUENCY, table names) are NOT
#: attributable reference sources: a local normalisation is not an external reference.
REFERENCE_SOURCES = {"iqvia": "IQVIA", "sam.gov": "SAM.gov", "sam": "SAM.gov", "pecos": "PECOS", "nppes": "NPPES",
                     "usps": "USPS", "oig leie": "OIG LEIE", "leie": "OIG LEIE", "hhs-oig leie": "OIG LEIE"}


def reference_source(label: Optional[str]) -> str:
    return REFERENCE_SOURCES.get((label or "").strip().lower(), "")


def build_field_rows(facts: Iterable[Dict[str, Any]], issues: Iterable[Any], *,
                     snapshot_date: str = "", run_id: Any = None) -> Dict[str, Any]:
    """facts: the loader's per-record facts. issues: rows with issue_type, field_name,
    original_value, suggested_value, suggested_source, description, run_id, created_at,
    source_record_id. Returns {"rows", "reconciliation", "mapping_exceptions"}."""
    by_agt = {str(f["agt_record_id"]): f for f in facts}
    rows: List[List[str]] = []
    rec = {"records": len(by_agt), "issues_read": 0, "rows": 0, "excluded_naic": 0,
           "issue_types_not_in_crosswalk": {}, "issues_without_record": 0,
           "records_with_findings": 0, "field_id_blank": 0, "snapshot_date_blank": not snapshot_date}
    exceptions: List[Dict[str, str]] = []
    seen_records = set()
    rec["issues_from_other_runs"] = 0
    for it in issues:
        rec["issues_read"] += 1
        # One assessment run: the record-level master reports the selected run, and the engine writes a
        # fresh set of findings per run. A finding with no run is kept (it carries no check event).
        if run_id is not None and getattr(it, "run_id", None) is not None and str(it.run_id) != str(run_id):
            rec["issues_from_other_runs"] += 1
            continue
        itype = _s(it.issue_type).strip()
        if itype in EXCLUDED_ISSUE_TYPES:
            rec["excluded_naic"] += 1
            continue
        if itype not in CROSSWALK:
            rec["issue_types_not_in_crosswalk"][itype] = rec["issue_types_not_in_crosswalk"].get(itype, 0) + 1
            continue
        fact = by_agt.get(_s(it.source_record_id))
        if fact is None:
            rec["issues_without_record"] += 1
            continue
        client_type, rule = CROSSWALK[itype]
        notes: List[str] = []
        own = field_id_for(it.field_name)
        if rule is ACTUAL:
            field_id = own or ""
            if not field_id:
                notes.append("Field_ID not established: the retained finding names no controlled field")
        elif rule is NOT_SPECIFIED:
            field_id = ""
            notes.append("No Field_ID is specified for this issue type")
        elif isinstance(rule, tuple):
            field_id = own if own in rule else ""
            if not field_id:
                notes.append(f"Field_ID not established: expected one of {', '.join(rule)}")
        else:
            field_id = rule
            named = (it.field_name or "").strip()
            if named and field_id_for(named) != rule:
                # The finding is about a different delivered column than the fixed Field_ID names:
                # exporting the fixed name would pair that field with another column's value.
                notes.append(f"Field_ID not established: the finding concerns '{named}', not {rule}")
                field_id = ""
        if not field_id:
            rec["field_id_blank"] += 1
            exceptions.append({"agt_record_id": _s(fact["agt_record_id"]), "issue_type": itype,
                               "reason": notes[-1] if notes else "blank Field_ID"})
        ref_src = reference_source(it.suggested_source)
        has_ref = bool(ref_src and _s(it.suggested_value).strip())
        when = it.created_at if getattr(it, "run_id", None) else None
        ref_date = when.date().isoformat() if when is not None else ""
        if not ref_date:
            notes.append("Reference_Source_Date blank: no run/check event is retained for this finding")
        et = entity_type(fact.get("participation_level"))
        if not et:
            notes.append("Entity_Type blank: sequoiaorgtype missing or unsupported")
        if not snapshot_date:
            notes.append("Snapshot_Date blank: the delivery's snapshot date is not recorded")
        reason = _s(it.description).strip()
        if notes:
            reason = (reason + " | " if reason else "") + "; ".join(notes)
        sub = B2_FROM_ISSUE_TYPE.get(client_type, "")
        assert not sub or sub in B2_VALUES
        rows.append([
            snapshot_date, _s(fact.get("source_line")), _s(fact.get("qhin_number")), _s(fact.get("hcid")),
            _s(fact.get("tefca_id")), et, _s(fact.get("submitted_npi")), _s(fact["agt_record_id"]),
            field_id, "", _s(it.original_value),
            _s(it.suggested_value) if has_ref else "", ref_src if has_ref else "",
            ref_date, sub, client_type, reason, _s(fact.get("purpose_of_use")),
        ])
        seen_records.add(_s(fact["agt_record_id"]))
    rec["rows"] = len(rows)
    rec["records_with_findings"] = len(seen_records)
    rec["records_without_findings"] = rec["records"] - len(seen_records)
    assert all(len(r) == len(FIELD_HEADERS) for r in rows)
    return {"headers": list(FIELD_HEADERS), "rows": rows, "reconciliation": rec,
            "mapping_exceptions": exceptions}


def to_csv(result: Dict[str, Any]) -> bytes:
    """Same conventions as the record-level CSV: formula-neutralised cells, UTF-8 with BOM."""
    from app.reports.engine.csv_engine import neutralise_row, to_bytes
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(result["headers"])
    for r in result["rows"]:
        w.writerow(neutralise_row(r))
    return to_bytes(buf.getvalue())


async def load_issues(db, intake_id, cutoff) -> List[Any]:
    """Issue-ledger rows for ONE delivery at or before the cutoff (read-only)."""
    from sqlalchemy import select

    from app.tefca_registry.rce import models as m
    I = m.RceIssue
    return list((await db.execute(
        select(I).where(I.source_intake_id == intake_id, I.source_record_id.isnot(None),
                        I.created_at <= cutoff)
        .order_by(I.source_record_id, I.created_at, I.issue_code))).scalars().all())


def to_xlsx(result: Dict[str, Any]) -> bytes:
    """One sheet, every cell TEXT (identifiers and postal codes are never numerically converted)."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Field_Findings"
    ws.append(list(result["headers"]))
    for r in result["rows"]:
        ws.append([str(v) for v in r])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.number_format = "@"
            c.data_type = "s"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
