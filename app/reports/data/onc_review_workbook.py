"""The ONC data-review workbook, as DATA. No Excel here.

WHERE THIS SITS
───────────────
    Area 1 / curation / DQ / evidence / review
        -> onc_review_workbook   (this module: the dataset, ten sheets)
        -> engine/xlsx_engine    (bytes)
        -> artifact_registry     (stored, hashed, versioned, classified)
        -> reports routes        (RBAC, integrity-verified download)

The split is the one the reporting layer already uses: a data module decides
WHAT a report says, an engine decides how it looks, and the artifact registry
decides what was actually delivered. Nothing new was invented for Excel.

DOCUACTION IS THE SYSTEM OF RECORD. THIS IS A SNAPSHOT OF IT.
─────────────────────────────────────────────────────────────
Every sheet is read-only output. Nothing in this module or the engine writes to
Area 1, curation, DQ, evidence or review state, and a workbook edited on someone's
desktop changes nothing here — the README says so in the reader's own words.

THE SOURCE SHEET IS A CONTRACT
──────────────────────────────
`Source_Data` carries the 41 delivered fields, in the delivered order, with the
delivered values. Not 40, not 42, not reordered, not trimmed, not normalised.
The field list is taken from `rce.field_map.RCE_FIELDS` — the same tuple intake
parsed the delivery with — rather than being retyped here, because a second copy
of a 41-item ordered list is a second thing to get wrong.

The values come from `RceSourceRecord.parsed`, which is what the delivered line
held. Where a field is absent from the parsed payload it is written as an empty
string: the delivery had no value there, and inventing a placeholder would be a
change to Government data.

WHAT THIS MODULE REFUSES TO DECIDE
──────────────────────────────────
It computes no verdicts. `SOURCE_UNAVAILABLE` stays unavailable, a
HUMAN_REQUIRED finding stays a finding rather than becoming a failure, and HELD
stays held rather than becoming non-compliant. Each sheet reports the state the
owning engine recorded, under the rule-set version that recorded it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

#: Bump when a SHEET's shape or meaning changes, so a delivered workbook can be
#: traced to the definition that produced it.
WORKBOOK_VERSION = "1.0.0"

#: The sheets, in the order a reviewer should meet them: what this is, what the
#: Government sent, what we did to it, and how to trace either.
SHEET_ORDER = (
    "README",
    "Source_Data",
    "Curated_Data",
    "Processing_Status",
    "Data_Quality",
    "Verification",
    "Relationships",
    "Review_Status",
    "Data_Mapping",
    "Export_Metadata",
)


class WorkbookRefused(RuntimeError):
    """A workbook could not be built, and the reason is stated."""


def _fields() -> List[str]:
    from app.tefca_registry.rce.field_map import RCE_FIELDS
    return list(RCE_FIELDS)


# ── the delivery ─────────────────────────────────────────────────────────────

async def _intake(db, intake_id):
    from app.tefca_registry.rce import models as m

    if intake_id is not None:
        row = await db.get(m.RceSourceIntake, intake_id)
        if row is None:
            raise WorkbookRefused(f"No delivery {intake_id}")
        return row
    row = (await db.execute(
        select(m.RceSourceIntake)
        .where(m.RceSourceIntake.duplicate_of_intake_id.is_(None))
        .order_by(m.RceSourceIntake.received_at.desc())
        .limit(1))).scalars().first()
    if row is None:
        raise WorkbookRefused(
            "There is no delivery to export. A workbook without a delivery "
            "would describe nothing.")
    return row


# ── sheets ───────────────────────────────────────────────────────────────────

async def _source_data(db, intake_id) -> Dict[str, Any]:
    """The 41 delivered fields, in the delivered order, unaltered.

    Ordered by `line_number` so the sheet reads in delivery order, which is the
    order a reviewer comparing against their own file will expect.
    """
    from app.tefca_registry.rce import models as m

    columns = _fields()
    rows: List[List[Any]] = []
    result = await db.stream(
        select(m.RceSourceRecord.line_number, m.RceSourceRecord.parsed)
        .where(m.RceSourceRecord.source_intake_id == intake_id)
        .order_by(m.RceSourceRecord.line_number))
    async for line_number, parsed in result:
        payload = parsed or {}
        # `.get(name, "")` — a field the delivery did not carry is empty, never
        # a placeholder and never omitted, because the column contract is 41.
        rows.append([payload.get(name, "") for name in columns])

    return {"columns": columns, "rows": rows,
            "text_columns": list(range(len(columns))),
            "note": ("Exactly as delivered by ONC/RCE and preserved in "
                     "DocuAction. No value on this sheet has been normalised, "
                     "corrected or reformatted.")}


async def _curated_data(db, intake_id) -> Dict[str, Any]:
    """The controlled processing layer, and every correction behind it.

    One row per CORRECTION rather than per record: a record with no correction
    has nothing to show here, and a sheet of 23,566 rows that are mostly
    "unchanged" would bury the 1,631 that are not.
    """
    from app.tefca_registry.rce import models as m

    columns = ["Source ID", "Record status", "Field", "Original value",
               "Curated value", "Correction rule", "Correction authority",
               "Correction reason", "Applied at"]
    rows: List[List[Any]] = []
    result = await db.stream(
        select(m.RceCorrectionDetail, m.RceCuratedRecord)
        .join(m.RceCuratedRecord,
              m.RceCuratedRecord.id == m.RceCorrectionDetail.curated_record_id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .order_by(m.RceCuratedRecord.rce_org_oid))
    async for correction, curated in result:
        rows.append([
            curated.rce_org_oid,
            curated.record_status,
            correction.column_name,
            correction.original_value,
            correction.corrected_value,
            correction.correction_rule_id,
            correction.correction_authority,
            correction.correction_reason,
            correction.created_at,
        ])

    return {"columns": columns, "rows": rows,
            "text_columns": [0, 3, 4],
            "note": ("DocuAction's controlled corrections. These are NOT "
                     "Government-delivered values — the delivered value is in "
                     "the Original value column and on Source_Data.")}


async def _processing_status(db, intake_id) -> Dict[str, Any]:
    """One operational row per delivered record. Dimensions kept separate.

    There is deliberately no single PASS/FAIL column. Curation status, data
    quality, promotion and review are four different questions, and a system
    that answers them separately must not be exported as though it answered
    them once.
    """
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    columns = ["Source ID", "Organisation", "Record status", "DQ issues",
               "Highest DQ severity", "Human review required", "Promoted",
               "Canonical entity", "Managing QHIN"]

    severities = {}
    human = set()
    result = await db.stream(
        select(m.RceIssue.source_record_id, m.RceIssue.severity,
               m.RceIssue.correction_authority)
        .where(m.RceIssue.source_intake_id == intake_id))
    counts: Dict[Any, int] = {}
    RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
    async for source_record_id, severity, authority in result:
        counts[source_record_id] = counts.get(source_record_id, 0) + 1
        if RANK.get(severity, -1) > RANK.get(severities.get(source_record_id), -1):
            severities[source_record_id] = severity
        if authority == "HUMAN_REQUIRED":
            human.add(source_record_id)

    qhin_of = await _qhin_by_entity(db)
    rows: List[List[Any]] = []
    result = await db.stream(
        select(m.RceCuratedRecord, reg.TefcaRegEntity.name)
        .outerjoin(reg.TefcaRegEntity,
                   reg.TefcaRegEntity.id == m.RceCuratedRecord.canonical_entity_id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .order_by(m.RceCuratedRecord.rce_org_oid))
    async for curated, entity_name in result:
        entity_id = curated.canonical_entity_id
        rows.append([
            curated.rce_org_oid,
            curated.name,
            curated.record_status,
            counts.get(curated.source_record_id, 0),
            severities.get(curated.source_record_id, "None observed"),
            "Yes" if curated.source_record_id in human else "No",
            "Yes" if entity_id is not None else "No",
            entity_name,
            (qhin_of.get(str(entity_id)) or {}).get("name") if entity_id else None,
        ])

    return {"columns": columns, "rows": rows, "text_columns": [0],
            "note": ("Operational state per delivered record. Each column is a "
                     "separate question: a data-quality finding is not a "
                     "failure, and a held record is not a compliance finding.")}


async def _qhin_by_entity(db) -> Dict[str, Dict[str, Any]]:
    """The canonical managing QHIN per entity, in one query. Ambiguity reported."""
    from app.tefca_registry import models as reg

    rows = (await db.execute(
        select(reg.TefcaEntityRelationship.child_entity_id,
               reg.TefcaEntityRelationship.parent_entity_id,
               reg.TefcaRegEntity.name)
        .join(reg.TefcaRegEntity,
              reg.TefcaRegEntity.id == reg.TefcaEntityRelationship.parent_entity_id)
        .where(reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
               reg.TefcaEntityRelationship.status == "active"))).all()
    grouped: Dict[str, List[Any]] = {}
    for child, parent, name in rows:
        grouped.setdefault(str(child), []).append((parent, name))
    return {child: ({"id": str(edges[0][0]), "name": edges[0][1]}
                    if len(edges) == 1
                    else {"id": None, "name": "Ambiguous — more than one edge"})
            for child, edges in grouped.items()}


async def _entities_from(db, intake_id) -> List[Any]:
    """The canonical entities this delivery promoted.

    Verification observations, relationships and review cases hang off the
    ENTITY, not off the delivery. A query over those tables that does not say
    which entities it means returns the WHOLE registry - so a workbook built
    for one delivery would carry another delivery's rows, and a
    DEVELOPMENT_TEST workbook would carry Government content. Scope is
    therefore established once, here, and every entity-keyed sheet is bounded
    by it.

    An unpromoted record contributes no entity. That is not a silent drop: it
    is visible on Curated_Data as `Promoted = No`, which is the sheet whose job
    it is to say so.
    """
    from app.tefca_registry.rce import models as m

    rows = (await db.execute(
        select(m.RceCuratedRecord.canonical_entity_id)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.is_not(None)))).all()
    return [row[0] for row in rows]


async def _data_quality(db, intake_id) -> Dict[str, Any]:
    """Every recorded finding, under the rule-set version that produced it.

    `Rule set version` is per ISSUE, not the version in force today. The
    delivered run executed under the rules of its day and must stay explainable
    under them; restating old findings under a newer rule set would be
    rewriting history.
    """
    from app.tefca_registry.rce import models as m

    columns = ["Issue code", "Source ID", "Field", "Rule ID", "Rule set version",
               "Issue type", "Severity", "Correction authority",
               "Observed value", "Suggested value", "Description", "Resolution",
               "Recorded at"]
    rows: List[List[Any]] = []
    result = await db.stream(
        select(m.RceIssue, m.RceSourceRecord.source_rce_id)
        .join(m.RceSourceRecord, m.RceSourceRecord.id == m.RceIssue.source_record_id)
        .where(m.RceIssue.source_intake_id == intake_id)
        .order_by(m.RceIssue.issue_code))
    async for issue, source_rce_id in result:
        rows.append([
            issue.issue_code, source_rce_id, issue.field_name, issue.rule_id,
            issue.rule_version, issue.issue_type, issue.severity,
            issue.correction_authority, issue.original_value,
            issue.suggested_value, issue.description, issue.resolution,
            issue.created_at,
        ])

    return {"columns": columns, "rows": rows, "text_columns": [0, 1, 8, 9],
            "note": ("Findings as recorded, each under the rule-set version in "
                     "force when it was raised.")}


_VERIFICATION_NOTE = ("Observations as recorded. A source recorded as "
                      "unavailable could not answer; that is not evidence for "
                      "or against the organisation and is never a pass.")


async def _verification(db, intake_id, scope) -> Dict[str, Any]:
    """Recorded observations from the authoritative sources. No conclusions.

    A source that could not answer is exported as it was recorded. It is not a
    pass, not a clear, and not a finding against the organisation — which is
    exactly the distinction an export is most likely to lose.
    """
    from app.tefca_registry import models as reg

    columns = ["Source ID", "Organisation", "Authoritative source",
               "Identifier used", "Result", "Observation", "Source label",
               "Checked at"]
    rows: List[List[Any]] = []
    if not scope:
        return {"columns": columns, "rows": rows, "text_columns": [0, 3],
                "note": _VERIFICATION_NOTE}
    result = await db.stream(
        select(reg.TefcaVerification, reg.TefcaRegEntity.name,
               reg.TefcaRegEntity.rce_org_oid)
        .join(reg.TefcaRegEntity,
              reg.TefcaRegEntity.id == reg.TefcaVerification.entity_id)
        .where(reg.TefcaVerification.entity_id.in_(scope))
        .order_by(reg.TefcaVerification.verified_at.desc()))
    async for verification, name, oid in result:
        rows.append([
            oid, name, verification.source, verification.lookup_identifier,
            # The recorded status, verbatim. `unavailable` stays unavailable.
            verification.verification_status, verification.detail,
            verification.data_source_label, verification.verified_at,
        ])

    return {"columns": columns, "rows": rows, "text_columns": [0, 3],
            "note": _VERIFICATION_NOTE}


_RELATIONSHIP_NOTE = ("Relationships as promoted from this delivery, keyed "
                      "on the delivered organisation. Where a Subparticipant "
                      "names a QHIN directly, that is exported as delivered; "
                      "no intermediate Participant is inferred.")


async def _relationships(db, intake_id, scope) -> Dict[str, Any]:
    """Canonical TEFCA relationships. No intermediate entity is manufactured."""
    from app.tefca_registry import models as reg

    columns = ["Child source ID", "Child organisation", "Child level",
               "Relationship", "Parent organisation", "Parent level", "Status",
               "Effective date"]
    child = reg.TefcaRegEntity.__table__.alias("child")
    parent = reg.TefcaRegEntity.__table__.alias("parent")
    rows: List[List[Any]] = []
    if not scope:
        return {"columns": columns, "rows": rows, "text_columns": [0],
                "note": _RELATIONSHIP_NOTE}
    result = await db.stream(
        select(child.c.rce_org_oid, child.c.name, child.c.entity_level,
               reg.TefcaEntityRelationship.relationship_type,
               parent.c.name, parent.c.entity_level,
               reg.TefcaEntityRelationship.status,
               reg.TefcaEntityRelationship.effective_date)
        .join(child, child.c.id == reg.TefcaEntityRelationship.child_entity_id)
        .join(parent, parent.c.id == reg.TefcaEntityRelationship.parent_entity_id)
        # The CHILD is the delivered organisation; the parent is whoever it
        # named. Scoping on the child exports the edges this delivery asserted,
        # and does not drag in every other edge that happens to point at the
        # same QHIN.
        .where(reg.TefcaEntityRelationship.child_entity_id.in_(scope))
        .order_by(child.c.rce_org_oid))
    async for row in result:
        rows.append(list(row))

    return {"columns": columns, "rows": rows, "text_columns": [0],
            "note": _RELATIONSHIP_NOTE}


_REVIEW_NOTE = ("Workflow state only. Reportable is Yes only where an "
                "independent QA approval stands. Reviewer identities are held "
                "in the DocuAction audit trail, not in this export.")


async def _review_status(db, intake_id, scope) -> Dict[str, Any]:
    """Workflow state. Minimum necessary — no staff identities.

    The analyst and QA reviewer are recorded in DocuAction's audit trail, which
    is where an accountability question belongs. A workbook that circulates by
    email is not, so the holder is reported as a state rather than a person.
    """
    from app.tefca_registry import models as reg
    from app.tefca_registry.supervisor_ops import _events_for, _state_of

    columns = ["Review ID", "Source ID", "Queue source", "Selection reason",
               "Assignment status", "Workflow state", "Decision events",
               "Reportable", "Created at"]
    if not scope:
        return {"columns": columns, "rows": [], "text_columns": [0, 1],
                "note": _REVIEW_NOTE}
    records = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.entity_id.in_(scope))
        .order_by(reg.ReviewRecord.review_id))).scalars().all()

    # State is DERIVED, and deriving it one case at a time is a query per row.
    # `supervisor_ops` already owns the batched form of exactly this ladder, and
    # a test asserts it agrees with `case_state` on every state - so the export
    # reuses it rather than growing a third opinion about what a case is doing.
    by_review = await _events_for(db, [r.review_id for r in records])

    entity_oids = {}
    ids = [r.entity_id for r in records if r.entity_id]
    if ids:
        entity_oids = {str(i): oid for i, oid in (await db.execute(
            select(reg.TefcaRegEntity.id, reg.TefcaRegEntity.rce_org_oid)
            .where(reg.TefcaRegEntity.id.in_(ids)))).all()}

    rows: List[List[Any]] = []
    for record in records:
        payload = record.verification_results or {}
        rows.append([
            record.review_id,
            entity_oids.get(str(record.entity_id)) if record.entity_id else None,
            payload.get("queue_source"),
            payload.get("selection_reason"),
            "Assigned" if record.assigned_to_user_id else "Unassigned",
            _state_of(record, by_review.get(record.review_id, [])),
            len(by_review.get(record.review_id, [])),
            "Yes" if record.reportable_at else "No",
            record.created_at,
        ])

    return {"columns": columns, "rows": rows, "text_columns": [0, 1],
            "note": _REVIEW_NOTE}


def _data_mapping() -> Dict[str, Any]:
    """The 41-field mapping, read from the map intake itself uses.

    Not retyped. `FIELD_SPECS` is the authority for what each field is, what it
    is for, and how DocuAction treats it; a second hand-written mapping would
    be a second thing to drift.
    """
    from app.tefca_registry.rce.field_map import FIELD_SPECS

    columns = ["#", "ONC/RCE source field", "Business definition",
               "Data type / format", "Applicability", "DocuAction mapping",
               "Processing / validation", "Verification / authoritative source",
               "ARC use"]
    rows: List[List[Any]] = []
    for position, spec in enumerate(FIELD_SPECS, start=1):
        # `spec.ordinal` is a zero-based COLUMN INDEX, which is the right thing
        # for intake and the wrong thing under a heading a reviewer reads as
        # "field 1 of 41". The index stays as it is; the presentation counts
        # from one, and the two are asserted to agree so a gap in FIELD_SPECS
        # cannot pass silently.
        assert spec.ordinal == position - 1, (
            "FIELD_SPECS is not in delivered order at %s" % spec.name)
        rows.append([
            position,
            spec.name,
            spec.documented,
            spec.observed,
            spec.necessity,
            spec.docuaction,
            spec.validation,
            ", ".join(spec.dimensions) if spec.dimensions else "Not applicable",
            spec.role,
        ])
    return {"columns": columns, "rows": rows, "text_columns": [1],
            "note": ("The delivered schema and how DocuAction treats each "
                     "field. Where ONC/RCE has not published a definition, the "
                     "cell says so rather than asserting one.")}


# ── the workbook ─────────────────────────────────────────────────────────────

def _readme(intake, classification: str, report_id: str,
            generated_at: datetime, counts: Dict[str, int]) -> Dict[str, Any]:
    columns = ["Item", "Value"]
    rows = [
        ["Purpose", "A controlled snapshot of one ONC/RCE delivery as processed "
                    "by DocuAction, for Government review."],
        ["Delivery", intake.delivery_label],
        ["Delivered file", intake.original_filename],
        ["Delivery received", intake.received_at],
        ["Source records", intake.record_count],
        ["Workbook generated (UTC)", generated_at],
        ["Export identifier", report_id],
        ["Workbook version", WORKBOOK_VERSION],
        ["Classification", classification],
        ["", ""],
        ["How to use this workbook", ""],
        ["Source_Data",
         "The Government-delivered values, exactly as received and preserved. "
         "Nothing on that sheet has been normalised or corrected."],
        ["Curated_Data",
         "DocuAction's controlled corrections. The delivered value is shown "
         "beside the curated one so the two are never confused."],
        ["Processing_Status",
         "Where each delivered record stands. Each column answers a separate "
         "question; there is no single pass or fail."],
        ["Data_Quality",
         "Findings as recorded, each under the rule-set version in force when "
         "it was raised."],
        ["Verification",
         "Observations from the authoritative sources. A source that could not "
         "answer is recorded as unavailable — that is not a pass and not a "
         "finding against the organisation."],
        ["Relationships",
         "TEFCA organisational relationships as promoted from the delivery."],
        ["Review_Status",
         "Analyst and independent QA workflow state. A result is reportable "
         "only where a QA approval stands."],
        ["Data_Mapping", "The 41 delivered fields and how DocuAction treats each."],
        ["Export_Metadata", "Provenance for this workbook, including the source hash."],
        ["", ""],
        ["Important",
         "This workbook is a controlled export from DocuAction. Changes made "
         "in this file do not update DocuAction and do not change the "
         "Government source record."],
    ]
    rows.append(["", ""])
    rows.append(["Rows exported", ""])
    for sheet in SHEET_ORDER:
        if sheet in counts:
            rows.append([sheet, counts[sheet]])
    return {"columns": columns, "rows": rows, "text_columns": [],
            "note": None}


async def _field_map_version(db, intake_id) -> str:
    """The field-map version the ingestion run recorded, not today's."""
    from app.tefca_registry.rce import models as m

    row = (await db.execute(
        select(m.RceIngestionRun.field_map_version)
        .where(m.RceIngestionRun.source_intake_id == intake_id)
        .order_by(m.RceIngestionRun.started_at.desc())
        .limit(1))).scalar_one_or_none()
    return row or "Not recorded"


async def build_workbook_dataset(
        db, *, intake_id=None, classification: str = "DEVELOPMENT_TEST",
        report_id: Optional[str] = None,
        generated_by: str = "SYSTEM") -> Dict[str, Any]:
    """Every sheet, as data. Reads only; writes nothing."""
    from app.tefca_registry.rce.field_map import EXPECTED_SCHEMA_FINGERPRINT

    intake = await _intake(db, intake_id)
    # Resolved once, before any sheet is built, so every entity-keyed sheet is
    # bounded by the SAME delivery.
    scope = await _entities_from(db, intake.id)
    generated_at = datetime.now(timezone.utc)
    # The delivery date is in the identifier because a reviewer with a folder of
    # these needs to tell two DELIVERIES apart, and a generation timestamp alone
    # only tells them apart by when someone happened to press the button.
    delivered = getattr(intake, "received_at", None)
    report_id = report_id or (
        f"ONC-REVIEW-{delivered:%Y%m%d}-{generated_at:%Y%m%d%H%M%S}"
        if delivered else f"ONC-REVIEW-{generated_at:%Y%m%d-%H%M%S}")

    sheets: Dict[str, Dict[str, Any]] = {
        "Source_Data": await _source_data(db, intake.id),
        "Curated_Data": await _curated_data(db, intake.id),
        "Processing_Status": await _processing_status(db, intake.id),
        "Data_Quality": await _data_quality(db, intake.id),
        "Verification": await _verification(db, intake.id, scope),
        "Relationships": await _relationships(db, intake.id, scope),
        "Review_Status": await _review_status(db, intake.id, scope),
        "Data_Mapping": _data_mapping(),
    }
    counts = {name: len(sheet["rows"]) for name, sheet in sheets.items()}

    # ── the source contract, checked here rather than trusted ───────────────
    expected = _fields()
    exported = sheets["Source_Data"]["columns"]
    reconciliation = {
        "authoritative_source_fields": len(expected),
        "exported_source_fields": len(exported),
        "missing": [f for f in expected if f not in exported],
        "invented": [f for f in exported if f not in expected],
        "order_exact": exported == expected,
        "duplicate_columns": len(exported) != len(set(exported)),
        "source_records": int(intake.record_count or 0),
        "exported_source_records": counts["Source_Data"],
        "row_count_matches": counts["Source_Data"] == int(intake.record_count or 0),
    }
    if reconciliation["missing"] or reconciliation["invented"] \
            or not reconciliation["order_exact"] \
            or reconciliation["duplicate_columns"]:
        raise WorkbookRefused(
            f"The source sheet does not match the delivered schema: "
            f"{reconciliation}. Refusing to export a workbook whose Source_Data "
            f"is not the 41 delivered fields in the delivered order.")

    from app.reports.engine.xlsx_engine import XLSX_ENGINE_VERSION as engine_version

    # Every version that shaped this file, read from what actually produced it.
    # A findings sheet may legitimately carry more than one rule-set version:
    # each finding keeps the rules of its own run, and restating an old finding
    # under a newer rule set would be rewriting history.
    rule_versions = ", ".join(sorted({
        str(row[4]) for row in sheets["Data_Quality"]["rows"] if row[4]
    })) or "None applied"
    field_map_version = await _field_map_version(db, intake.id)

    # The SAME value the registry will store as `report_data_hash`, not a second
    # opinion about it. `dataset_hash` covers only the data sheets, so it can be
    # computed here — before Export_Metadata exists — and cannot contain itself.
    data_hash = dataset_hash({
        "workbook_version": WORKBOOK_VERSION,
        "intake_id": str(intake.id),
        "source_sha256": intake.sha256,
        "sheets": sheets,
    })

    metadata = {
        "columns": ["Item", "Value"],
        "rows": [
            ["Export identifier", report_id],
            ["Workbook version", WORKBOOK_VERSION],
            ["Generated at (UTC)", generated_at],
            ["Generated by", generated_by],
            ["Classification", classification],
            ["Delivery identifier", str(intake.id)],
            ["Delivery label", intake.delivery_label],
            ["Delivery received", intake.received_at],
            ["Delivered filename", intake.original_filename],
            ["Delivered file SHA-256", intake.sha256],
            ["Delivered file size (bytes)", intake.file_size_bytes],
            ["Delivered record count", intake.record_count],
            ["Schema fingerprint", intake.schema_fingerprint],
            ["Expected schema fingerprint", EXPECTED_SCHEMA_FINGERPRINT],
            ["Schema matches expected",
             "Yes" if intake.schema_fingerprint == EXPECTED_SCHEMA_FINGERPRINT else "No"],
            ["", ""],
            ["Versions in force", ""],
            ["Workbook data version", WORKBOOK_VERSION],
            ["Excel rendering engine", engine_version],
            ["Data-quality rule sets applied", rule_versions],
            ["Field mapping version", field_map_version],
            ["Workbook data hash (SHA-256)", data_hash],
            ["", ""],
            ["Source reconciliation", ""],
            ["Authoritative source fields", reconciliation["authoritative_source_fields"]],
            ["Exported source fields", reconciliation["exported_source_fields"]],
            ["Missing source fields", len(reconciliation["missing"])],
            ["Invented source fields", len(reconciliation["invented"])],
            ["Source column order exact", "Yes" if reconciliation["order_exact"] else "No"],
            ["Delivered records", reconciliation["source_records"]],
            ["Exported source rows", reconciliation["exported_source_records"]],
            ["Row count matches", "Yes" if reconciliation["row_count_matches"] else "No"],
            ["", ""],
            ["Sheets", ""],
        ],
        "text_columns": [],
        "note": None,
    }
    for name in SHEET_ORDER:
        if name in counts:
            metadata["rows"].append([f"{name} rows", counts[name]])

    sheets["Export_Metadata"] = metadata
    sheets["README"] = _readme(intake, classification, report_id, generated_at, counts)

    dataset = {
        "report_id": report_id,
        "workbook_version": WORKBOOK_VERSION,
        "classification": classification,
        "generated_at": generated_at,
        "generated_by": generated_by,
        "intake_id": str(intake.id),
        "delivery_label": intake.delivery_label,
        "source_sha256": intake.sha256,
        "schema_fingerprint": intake.schema_fingerprint,
        "sheet_order": list(SHEET_ORDER),
        "sheets": sheets,
        "counts": counts,
        "reconciliation": reconciliation,
    }
    dataset["data_hash"] = data_hash
    return dataset


def dataset_hash(dataset: Dict[str, Any]) -> str:
    """A hash of WHAT the workbook says, independent of how it looks.

    Excludes the generation timestamp and the generator's identity: two
    workbooks built from the same immutable snapshot say the same thing even
    though they were produced at different moments, and a data hash that moved
    every second could not answer "are these the same numbers".
    """
    def canonical(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [canonical(v) for v in value]
        return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)

    payload = {
        "workbook_version": dataset["workbook_version"],
        "intake_id": dataset["intake_id"],
        "source_sha256": dataset["source_sha256"],
        "sheets": {name: {"columns": sheet["columns"],
                          "rows": canonical(sheet["rows"])}
                   for name, sheet in dataset["sheets"].items()
                   # README and Export_Metadata carry the timestamp and the
                   # identifier, which are not part of what the data says.
                   if name not in ("README", "Export_Metadata")},
    }
    return hashlib.sha256(
        json.dumps(canonical(payload), sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()
