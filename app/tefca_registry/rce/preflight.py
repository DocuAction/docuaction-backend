"""Preflight — what must be known about a delivery BEFORE final classification.

WHAT PREFLIGHT IS, AND IS NOT
─────────────────────────────
Preflight runs over Area 1 (the immutable delivered lines) after ingestion
and before `verify_and_classify`. It answers four questions, each with its
own category:

    SCHEMA             does the delivered header match the locked 41-field
                       map (missing / extra / reordered / renamed columns),
                       and were delimiter, encoding and BOM as expected?
    IDENTIFIER         are the delivered identifiers structurally valid
                       (NPI length / format / Luhn, CCN shape, OID / HCID
                       shape, duplicate source ids)?
    CONDITIONAL_BLANK  is a REQUIRED or CONDITIONAL field blank?
    MISSING_CONTEXT    is something the classifier would need to decide
                       applicability absent (partOf unresolved, blank
                       hl7orgrole / sequoiaorgtype)?

It is NOT a second quality engine. Where a quality rule already decides a
question (`quality_rules.RULES`), preflight CALLS that rule and records the
result under its rule id (`rule_ref`); it does not re-implement it. Only the
checks no rule covers are implemented here, under `PF-*` codes.

FOUR DIMENSIONS, NEVER ONE STATUS
─────────────────────────────────
Every finding carries, separately:

    applicability   applies / does_not_apply / unresolved
    execution       done / unavailable / insufficient
    evidence        what was actually observed (a dict)
    disposition     open / informational / blocked

A finding with applicability=unresolved and execution=insufficient is a
real, recorded fact ("we cannot tell whether an NPI is required here"), not
a failure and not a pass. Collapsing those into one "status" is exactly what
turns a blank column into a compliance conclusion.

ORIGINALS ARE NEVER TOUCHED
───────────────────────────
Preflight reads `rce_source_records.parsed` and writes only its own tables.
Any value it DERIVES (trimmed whitespace, zero-padded ZIP, upper-cased
state, a normalised `active` flag) is written to
`rce_preflight_normalization` beside the original and the method that
produced it. The original stays where it was, byte for byte.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.core import request_context
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import preflight_shadow_models as pm
from app.tefca_registry.rce.field_map import (
    FIELD_BY_NAME, FIELD_MAP_VERSION, RCE_DELIMITER, RCE_ENCODING, RCE_FIELDS,
    Necessity, is_oid_syntax,
)
from app.tefca_registry.rce.quality_engine import _build_dataset_context
from app.tefca_registry.rce.quality_rules import (
    RULE_BY_ID, RULE_SET_VERSION, Finding, RecordContext,
)

logger = logging.getLogger(__name__)

PREFLIGHT_VERSION = "1.0.0"
BATCH_SIZE = 2000
INSERT_BATCH = 2000

#: Quality rules preflight REUSES, and the category each one's findings land
#: in. Anything not listed here is left to the quality engine proper.
_REUSED_RULES: Dict[str, str] = {
    "SCH-001": "SCHEMA",
    "SCH-003": "IDENTIFIER",
    "ID-001": "IDENTIFIER",
    "ID-002": "CONDITIONAL_BLANK",
    "ID-003": "IDENTIFIER",
    "ID-005": "CONDITIONAL_BLANK",
    "NPI-002": "IDENTIFIER",
    "NPI-004": "IDENTIFIER",
    "NPI-003": "IDENTIFIER",
    "REQ-001": "CONDITIONAL_BLANK",
    "REQ-002": "CONDITIONAL_BLANK",
    "REQ-003": "CONDITIONAL_BLANK",
    "INT-001": "CONDITIONAL_BLANK",
    "INT-002": "MISSING_CONTEXT",
    "CON-003": "CONDITIONAL_BLANK",
}

#: Issue types that are CONTEXT questions rather than blanks, overriding the
#: rule-level category above.
_CONTEXT_ISSUE_TYPES = frozenset({
    "UNKNOWN_SEQUOIA_ORG_TYPE", "PART_OF_UNRESOLVED", "PART_OF_RESOLVED_IN_REGISTRY",
    "UNSUPPORTED_ACTIVE_VALUE",
})

#: Quality findings that are NOT preflight findings at all: the first is a
#: normalisation (recorded separately), the second is an operational fact.
_SKIPPED_ISSUE_TYPES = frozenset({"ACTIVE_FORMAT_NORMALIZED", "INACTIVE_RECORD"})

#: Rules whose `suggested_value` is a deterministic, non-substantive
#: normalisation. These are the ONLY derivations preflight records, plus a
#: plain whitespace trim. Identity fields are never derived.
_NORMALIZING_RULES = ("FMT-001", "FMT-002", "FMT-004", "CON-003")

_CCN_RE = re.compile(r"^[0-9A-Z]{6}$|^[0-9A-Z]{10}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HCID_PREFIX = "urn:oid:"

_REQUIRED = frozenset(s.name for s in FIELD_BY_NAME.values()
                      if s.necessity == Necessity.REQUIRED)
_CONDITIONAL = frozenset(s.name for s in FIELD_BY_NAME.values()
                         if s.necessity == Necessity.CONDITIONAL)


class _Acc:
    """Accumulates finding and normalisation rows for one run."""

    def __init__(self, run_id, now):
        self.run_id = run_id
        self.now = now
        self.sequence = 0
        self.findings: List[Dict[str, Any]] = []
        self.normalizations: List[Dict[str, Any]] = []
        self.by_category: Dict[str, int] = {}
        self.by_code: Dict[str, int] = {}
        self.by_applicability: Dict[str, int] = {}
        self.by_execution: Dict[str, int] = {}
        self.by_disposition: Dict[str, int] = {}
        self.by_method: Dict[str, int] = {}
        self.affected_records: set = set()

    def finding(self, *, category: str, code: str, description: str,
                applicability: str, execution: str, disposition: str,
                evidence: Optional[Dict[str, Any]] = None, rule_ref: Optional[str] = None,
                field_name: Optional[str] = None, original_value: Optional[str] = None,
                record_id=None, line_number: Optional[int] = None) -> None:
        assert category in pm.PREFLIGHT_CATEGORIES, category
        assert applicability in pm.APPLICABILITY, applicability
        assert execution in pm.EXECUTION, execution
        assert disposition in pm.DISPOSITION, disposition
        self.sequence += 1
        self.findings.append({
            "run_id": self.run_id, "source_record_id": record_id,
            "line_number": line_number, "sequence": self.sequence,
            "category": category, "code": code, "rule_ref": rule_ref,
            "field_name": (field_name or "")[:100] or None,
            "applicability": applicability, "execution": execution,
            "evidence": evidence or {}, "disposition": disposition,
            "description": description, "original_value": original_value,
            "created_at": self.now,
        })
        self.by_category[category] = self.by_category.get(category, 0) + 1
        self.by_code[code] = self.by_code.get(code, 0) + 1
        self.by_applicability[applicability] = self.by_applicability.get(applicability, 0) + 1
        self.by_execution[execution] = self.by_execution.get(execution, 0) + 1
        self.by_disposition[disposition] = self.by_disposition.get(disposition, 0) + 1
        if record_id is not None:
            self.affected_records.add(record_id)

    def normalization(self, *, record_id, line_number: int, field_name: str,
                      original: Optional[str], derived: Optional[str], method: str,
                      rule_ref: Optional[str] = None) -> None:
        self.normalizations.append({
            "run_id": self.run_id, "source_record_id": record_id,
            "line_number": line_number, "field_name": field_name[:100],
            "original_value": original, "derived_value": derived,
            "method": method[:40], "rule_ref": rule_ref, "created_at": self.now,
        })
        self.by_method[method] = self.by_method.get(method, 0) + 1


# ── delivery-level: schema ───────────────────────────────────────────────────

def _schema_findings(acc: _Acc, intake) -> None:
    delivered = [str(h) for h in (intake.headers or [])]
    expected = list(RCE_FIELDS)
    stripped = [h.strip() for h in delivered]
    bom = bool(delivered) and delivered[0].startswith("﻿")
    if bom:
        stripped[0] = stripped[0].lstrip("﻿")
        acc.finding(category="SCHEMA", code="PF-SCH-007", field_name="__header__",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_INFORMATIONAL,
                    evidence={"first_header_as_delivered": delivered[0]},
                    description="The header begins with a UTF-8 byte-order mark. The first "
                                "column name is matched with the mark removed; the "
                                "delivered bytes are preserved as they arrived.")

    expected_set = set(expected)
    delivered_set = set(stripped)
    missing = [c for c in expected if c not in delivered_set]
    extra = [c for c in stripped if c not in expected_set]

    # Renamed candidates: an extra column that matches a missing one once
    # case and whitespace are ignored. Reported, never silently mapped.
    fold = lambda s: re.sub(r"\s+", "", s).lower()  # noqa: E731
    missing_fold = {fold(c): c for c in missing}
    renamed = [(c, missing_fold[fold(c)]) for c in extra if fold(c) in missing_fold]
    renamed_from = {c for c, _ in renamed}
    renamed_to = {t for _, t in renamed}

    truly_missing = [c for c in missing if c not in renamed_to]
    truly_extra = [c for c in extra if c not in renamed_from]

    if truly_missing:
        acc.finding(category="SCHEMA", code="PF-SCH-001", field_name="__header__",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_BLOCKED,
                    evidence={"missing_columns": truly_missing,
                              "delivered_column_count": len(stripped),
                              "expected_column_count": len(expected)},
                    description=f"{len(truly_missing)} column(s) of the locked 41-field map "
                                f"are absent from the delivered header: "
                                f"{', '.join(truly_missing)}. Final classification is "
                                f"BLOCKED: a field the rules read cannot be evaluated "
                                f"when it was never delivered, and a positional map "
                                f"against a different header would mis-assign values.")
    for frm, to in renamed:
        acc.finding(category="SCHEMA", code="PF-SCH-004", field_name=to,
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_OPEN,
                    evidence={"delivered_name": frm, "expected_name": to},
                    original_value=frm,
                    description=f"Delivered column {frm!r} differs from the expected "
                                f"{to!r} only in case or whitespace. Recorded for a "
                                f"human; it is NOT auto-mapped, because a renamed "
                                f"column may also mean a changed definition.")
    if truly_extra:
        acc.finding(category="SCHEMA", code="PF-SCH-002", field_name="__header__",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_OPEN,
                    evidence={"extra_columns": truly_extra},
                    description=f"{len(truly_extra)} delivered column(s) are not in the "
                                f"locked 41-field map: {', '.join(truly_extra)}. "
                                f"Preserved in Area 1; no rule reads them; a human "
                                f"decides whether the map must grow.")
    if not truly_missing and not truly_extra and not renamed and stripped != expected:
        acc.finding(category="SCHEMA", code="PF-SCH-003", field_name="__header__",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_OPEN,
                    evidence={"delivered_order": stripped, "expected_order": expected},
                    description="The delivered columns are the expected set in a "
                                "different order. Values are read by NAME, so nothing "
                                "is transposed, but the schema fingerprint differs "
                                "and the change is recorded for a human.")

    if intake.delimiter and intake.delimiter != RCE_DELIMITER:
        acc.finding(category="SCHEMA", code="PF-SCH-005", field_name="__delimiter__",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_INFORMATIONAL,
                    evidence={"delivered": intake.delimiter, "profiled": RCE_DELIMITER},
                    original_value=intake.delimiter,
                    description=f"Delimiter {intake.delimiter!r} differs from the profiled "
                                f"July delivery's {RCE_DELIMITER!r}. The reader detected "
                                f"and applied it consistently; recorded as a fact about "
                                f"this delivery, not a defect.")
    enc = (intake.encoding or "").lower().replace("_", "-")
    if enc and enc not in (RCE_ENCODING, "utf-8-sig"):
        acc.finding(category="SCHEMA", code="PF-SCH-006", field_name="__encoding__",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_OPEN,
                    evidence={"delivered": intake.encoding, "expected": RCE_ENCODING},
                    original_value=intake.encoding,
                    description=f"The delivery decoded as {intake.encoding!r}, not "
                                f"{RCE_ENCODING!r}. Nothing is re-decoded; a human "
                                f"confirms the encoding before values are compared.")
    if intake.encoding_anomaly:
        meta = intake.source_metadata or {}
        acc.finding(category="SCHEMA", code="PF-SCH-006", field_name="__encoding__",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_OPEN,
                    evidence={"mojibake_cells": meta.get("mojibake_cells"),
                              "encoding_had_errors": True},
                    description="Decoding needed replacement characters or mojibake "
                                "markers were seen. Values are preserved exactly as "
                                "delivered and are not re-decoded.")


# ── per-record checks ────────────────────────────────────────────────────────

def _disposition_for(finding: Finding, rule) -> str:
    sev = finding.severity or (rule.severity() if rule else "MEDIUM")
    if finding.correction_authority in ("HUMAN_REQUIRED", "QA_REQUIRED"):
        return pm.DISP_OPEN
    if sev == "INFORMATIONAL":
        return pm.DISP_INFORMATIONAL
    return pm.DISP_OPEN


def _category_for(rule_id: str, finding: Finding) -> str:
    if finding.issue_type in _CONTEXT_ISSUE_TYPES:
        return "MISSING_CONTEXT"
    return _REUSED_RULES[rule_id]


def _applicability_for(rule_id: str, finding: Finding, ctx: RecordContext) -> str:
    field = finding.field_name or ""
    if field in _CONDITIONAL:
        return pm.UNRESOLVED       # the governing condition is not captured by the delivery
    if finding.issue_type == "PART_OF_RESOLVED_IN_REGISTRY":
        return pm.APPLIES
    return pm.APPLIES


def _execution_for(rule_id: str, finding: Finding) -> str:
    if finding.issue_type == "PART_OF_UNRESOLVED":
        # The check ran; what it lacks is a registry/delivery row to resolve
        # against. That is an input gap, recorded as such.
        return pm.EXEC_INSUFFICIENT
    return pm.EXEC_DONE


def _record_findings(acc: _Acc, record, ctx: RecordContext) -> None:
    rid, line = record.id, record.line_number

    # 1. Reused quality rules — called, not re-implemented.
    for rule_id in _REUSED_RULES:
        rule = RULE_BY_ID[rule_id]
        try:
            found = rule.evaluate(ctx) or []
        except Exception as exc:  # noqa: BLE001 — one rule must not stop preflight
            logger.warning("preflight: rule %s raised on line %s: %s", rule_id, line, exc)
            continue
        for f in found:
            if f.issue_type in _SKIPPED_ISSUE_TYPES:
                continue
            if f.severity is None:
                f.severity = rule.severity()
            acc.finding(
                category=_category_for(rule_id, f), code=f.issue_type[:32],
                rule_ref=rule_id, field_name=f.field_name,
                applicability=_applicability_for(rule_id, f, ctx),
                execution=_execution_for(rule_id, f),
                disposition=_disposition_for(f, rule),
                evidence={"severity": f.severity, "correction_authority": f.correction_authority,
                          "rule_version": rule.version,
                          **({"suggested_value": f.suggested_value,
                              "suggested_source": f.suggested_source}
                             if f.suggested_value is not None else {})},
                original_value=f.original_value, description=f.description,
                record_id=rid, line_number=line)

    # 2. Checks no quality rule covers.
    value = ctx.get("id")
    if value and not is_oid_syntax(value) and not _UUID_RE.match(value):
        acc.finding(category="IDENTIFIER", code="PF-OID-001", field_name="id",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_OPEN, original_value=value,
                    evidence={"length": len(value)},
                    description=f"id {value!r} is neither an OID nor a UUID. The profiled "
                                f"delivery carried 23,565 OIDs and 1 bare UUID; any other "
                                f"shape is recorded for a human. The value is preserved.",
                    record_id=rid, line_number=line)
    hcid = ctx.get("HCID")
    if hcid:
        body = hcid[len(_HCID_PREFIX):] if hcid.lower().startswith(_HCID_PREFIX) else None
        if body is None or not (is_oid_syntax(body) or _UUID_RE.match(body)):
            acc.finding(category="IDENTIFIER", code="PF-HCID-001", field_name="HCID",
                        applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                        disposition=pm.DISP_OPEN, original_value=hcid,
                        evidence={"has_urn_oid_prefix": body is not None},
                        description=f"HCID {hcid!r} is not 'urn:oid:' followed by an OID "
                                    f"(the shape on 23,561 of 23,566 profiled records). "
                                    f"Preserved; recorded for a human.",
                        record_id=rid, line_number=line)
    ccn = ctx.get("CCN")
    if ccn and not _CCN_RE.match(ccn.upper()):
        acc.finding(category="IDENTIFIER", code="PF-CCN-001", field_name="CCN",
                    applicability=pm.APPLIES, execution=pm.EXEC_DONE,
                    disposition=pm.DISP_OPEN, original_value=ccn,
                    evidence={"length": len(ccn)},
                    description=f"CCN {ccn!r} is not a 6- or 10-character CMS Certification "
                                f"Number shape. Preserved; never corrected automatically.",
                    record_id=rid, line_number=line)

    # 3. Missing context: the NPI-requirement question cannot be decided.
    #    `quality_rules.npi_required` deliberately returns False on a blank
    #    hl7orgrole (no case is ever created solely for a blank role). That
    #    is the right escalation behaviour; it is NOT the same as the
    #    requirement being resolved, and preflight records the difference.
    if not ctx.get("NPI") and not ctx.get("hl7orgrole") and ctx.get("sequoiaorgtype"):
        acc.finding(category="MISSING_CONTEXT", code="PF-CTX-001", field_name="NPI",
                    applicability=pm.UNRESOLVED, execution=pm.EXEC_INSUFFICIENT,
                    disposition=pm.DISP_INFORMATIONAL,
                    evidence={"hl7orgrole": "", "sequoiaorgtype": ctx.get("sequoiaorgtype"),
                              "npi_required_predicate": False,
                              "why_unresolved": "no delivered field captures the HIPAA "
                                                "covered-provider test (45 CFR 160.103 / "
                                                "162.408); sequoiaorgtype is a TEFCA "
                                                "participation tier, not that test"},
                    description="No NPI and no hl7orgrole. Whether an NPI is REQUIRED of "
                                "this organisation cannot be decided from the delivered "
                                "fields; no case is created for the blank role alone, and "
                                "the absence is recorded as unresolved applicability, "
                                "never as a missing required identifier.",
                    record_id=rid, line_number=line)

    # 4. Derived normalisations — recorded beside the original, never applied.
    for name, raw in ctx.values.items():
        if isinstance(raw, str) and raw != raw.strip() and raw.strip():
            acc.normalization(record_id=rid, line_number=line, field_name=name,
                              original=raw, derived=raw.strip(), method="WHITESPACE_TRIM")
    for rule_id in _NORMALIZING_RULES:
        rule = RULE_BY_ID[rule_id]
        try:
            found = rule.evaluate(ctx) or []
        except Exception:  # noqa: BLE001
            continue
        for f in found:
            if f.suggested_value is None or f.suggested_value == f.original_value:
                continue
            acc.normalization(record_id=rid, line_number=line,
                              field_name=f.field_name or name,
                              original=f.original_value, derived=f.suggested_value,
                              method=f.suggested_source or rule_id, rule_ref=rule_id)


async def _flush(db, acc: _Acc) -> None:
    if acc.findings:
        await db.execute(pm.RcePreflightFinding.__table__.insert(), acc.findings)
        acc.findings = []
    if acc.normalizations:
        await db.execute(pm.RcePreflightNormalization.__table__.insert(), acc.normalizations)
        acc.normalizations = []


# ── entry point ──────────────────────────────────────────────────────────────

async def run_preflight(db, intake_id, *, actor: str = "SYSTEM") -> Dict[str, Any]:
    """Run preflight over one delivery and persist the run, its findings and
    its normalisations. Returns the run summary."""
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise ValueError(f"No intake {intake_id}")

    now = datetime.now(timezone.utc)
    run = pm.RcePreflightRun(
        source_intake_id=intake.id, field_map_version=FIELD_MAP_VERSION,
        rule_set_version=RULE_SET_VERSION, preflight_version=PREFLIGHT_VERSION,
        status="RUNNING", started_at=now, actor=actor[:320],
        correlation_id=request_context.correlation_id()[:64],
        build_sha=request_context.build_sha())
    db.add(run)
    await db.flush()
    acc = _Acc(run.id, now)

    try:
        _schema_findings(acc, intake)
        dataset = await _build_dataset_context(db, intake.id)
        total = int((await db.execute(
            select(func.count()).select_from(m.RceSourceRecord)
            .where(m.RceSourceRecord.source_intake_id == intake.id))).scalar() or 0)

        evaluated = 0
        role_blank = 0
        for offset in range(0, total, BATCH_SIZE):
            records = (await db.execute(
                select(m.RceSourceRecord)
                .where(m.RceSourceRecord.source_intake_id == intake.id)
                .order_by(m.RceSourceRecord.line_number)
                .limit(BATCH_SIZE).offset(offset))).scalars().all()
            for record in records:
                evaluated += 1
                ctx = RecordContext(line_number=record.line_number,
                                    parse_status=record.parse_status,
                                    field_count=record.field_count,
                                    values=dict(record.parsed or {}), dataset=dataset)
                if not ctx.get("hl7orgrole"):
                    role_blank += 1
                _record_findings(acc, record, ctx)
            if len(acc.findings) >= INSERT_BATCH or len(acc.normalizations) >= INSERT_BATCH:
                await _flush(db, acc)

        # Delivery-level context fact, recorded ONCE (the SCH-002 principle):
        # how much of the applicability question this delivery leaves open.
        if total:
            acc.finding(category="MISSING_CONTEXT", code="PF-CTX-000", field_name="hl7orgrole",
                        applicability=pm.UNRESOLVED, execution=pm.EXEC_INSUFFICIENT,
                        disposition=pm.DISP_INFORMATIONAL,
                        evidence={"records": total, "hl7orgrole_blank": role_blank,
                                  "hl7orgrole_blank_pct": round(role_blank / total * 100, 2)},
                        description=f"hl7orgrole is blank on {role_blank} of {total} records. "
                                    f"For those records the NPI-requirement applicability "
                                    f"is UNRESOLVED (recorded once here; per-record "
                                    f"PF-CTX-001 rows exist only where the NPI is also "
                                    f"blank). This is a statement about what the delivered "
                                    f"data can prove, not a finding against any record.")
        await _flush(db, acc)

        gate = (pm.GATE_BLOCKED if acc.by_disposition.get(pm.DISP_BLOCKED)
                else pm.GATE_CLEAR_WITH_FINDINGS if acc.by_disposition.get(pm.DISP_OPEN)
                else pm.GATE_CLEAR)
        summary = {
            "by_category": acc.by_category, "by_code": acc.by_code,
            "by_applicability": acc.by_applicability, "by_execution": acc.by_execution,
            "by_disposition": acc.by_disposition,
            "normalizations_by_method": acc.by_method,
            "records_with_findings": len(acc.affected_records),
            "records_in_delivery": total,
            "every_record_evaluated": evaluated == total,
            "reused_rules": sorted(_REUSED_RULES),
            "originals_modified": False,
        }
        run.status = "COMPLETE"
        run.classification_gate = gate
        run.records_evaluated = evaluated
        run.findings_count = acc.sequence
        run.normalizations_count = sum(acc.by_method.values())
        run.summary = summary
        run.completed_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        run.status = "FAILED"
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise

    return run_dto(run)


def run_dto(run) -> Dict[str, Any]:
    return {
        "run_id": str(run.id), "intake_id": str(run.source_intake_id),
        "status": run.status, "classification_gate": run.classification_gate,
        "field_map_version": run.field_map_version,
        "rule_set_version": run.rule_set_version,
        "preflight_version": run.preflight_version,
        "records_evaluated": run.records_evaluated,
        "findings_count": run.findings_count,
        "normalizations_count": run.normalizations_count,
        "summary": run.summary or {},
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "actor": run.actor, "correlation_id": run.correlation_id,
        "build_sha": run.build_sha, "error": run.error,
        "dimensions_note": ("applicability, execution, evidence and disposition are "
                            "recorded separately on every finding and are never "
                            "collapsed into one status."),
    }


async def latest_run(db, intake_id):
    return (await db.execute(
        select(pm.RcePreflightRun)
        .where(pm.RcePreflightRun.source_intake_id == intake_id)
        .order_by(pm.RcePreflightRun.started_at.desc()).limit(1))).scalars().first()


async def list_findings(db, run_id, *, category: Optional[str] = None,
                        disposition: Optional[str] = None, limit: int = 100,
                        offset: int = 0) -> Dict[str, Any]:
    stmt = select(pm.RcePreflightFinding).where(pm.RcePreflightFinding.run_id == run_id)
    if category:
        if category not in pm.PREFLIGHT_CATEGORIES:
            raise ValueError(f"category must be one of {pm.PREFLIGHT_CATEGORIES}")
        stmt = stmt.where(pm.RcePreflightFinding.category == category)
    if disposition:
        if disposition not in pm.DISPOSITION:
            raise ValueError(f"disposition must be one of {pm.DISPOSITION}")
        stmt = stmt.where(pm.RcePreflightFinding.disposition == disposition)
    total = int((await db.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (await db.execute(stmt.order_by(pm.RcePreflightFinding.sequence)
                             .limit(limit).offset(offset))).scalars().all()
    return {
        "run_id": str(run_id), "total": total, "count": len(rows),
        "limit": limit, "offset": offset,
        "items": [{
            "id": str(r.id), "sequence": r.sequence, "line_number": r.line_number,
            "source_record_id": str(r.source_record_id) if r.source_record_id else None,
            "category": r.category, "code": r.code, "rule_ref": r.rule_ref,
            "field_name": r.field_name,
            "applicability": r.applicability, "execution": r.execution,
            "evidence": r.evidence, "disposition": r.disposition,
            "description": r.description, "original_value": r.original_value,
        } for r in rows],
    }


async def list_normalizations(db, run_id, *, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    stmt = select(pm.RcePreflightNormalization).where(pm.RcePreflightNormalization.run_id == run_id)
    total = int((await db.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    rows = (await db.execute(stmt.order_by(pm.RcePreflightNormalization.line_number,
                                           pm.RcePreflightNormalization.field_name)
                             .limit(limit).offset(offset))).scalars().all()
    return {
        "run_id": str(run_id), "total": total, "count": len(rows),
        "limit": limit, "offset": offset,
        "note": "derived values only; the original value is unchanged in Area 1",
        "items": [{
            "id": str(r.id), "line_number": r.line_number,
            "source_record_id": str(r.source_record_id),
            "field_name": r.field_name, "original_value": r.original_value,
            "derived_value": r.derived_value, "method": r.method, "rule_ref": r.rule_ref,
        } for r in rows],
    }
