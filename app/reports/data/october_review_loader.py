"""Database loader for the record-level reporting dataset (october_review_dataset).

WHAT THIS DOES
    Reads ONE explicitly selected delivery (intake) from the application's tables
    and produces the normalised per-record FACTS that
    `october_review_dataset.build_dataset` turns into Excel / CSV / PDF-HTML.
    It writes nothing, changes no classification, releases no hold.

STARTING SET
    Every `rce_source_records` row of the selected intake - promoted or not,
    HELD, REJECTED, unparseable. AGT_Record_ID IS `rce_source_records.id`.
    Nothing is dropped because a downstream join found nothing.

IDENTIFIERS STAY SEPARATE (and are labelled in `Evidence Reference`)
    source_record (= AGT_Record_ID)  |  source_rce_id (the delivery's business
    `id`)  |  source_line  |  canonical entity id (curated.canonical_entity_id,
    absent for HELD / unpromoted)  |  review_id (REV-...).

NO JOIN FAN-OUT, NO CROSS-DELIVERY LEAKAGE
    * curated state: `uq_rce_curated_source_record` => at most one row per
      source record, and the join is also scoped to the intake;
    * review outcome: ONE row per source record (DISTINCT ON source_record_id,
      newest at/before the cutoff). Reviews are joined on the Area-1 anchor
      `review_records.source_record_id` ONLY - never through an entity id, which
      other deliveries can share;
    * evidence: ONE row per (entity, source) (DISTINCT ON, newest at/before the
      cutoff), restricted to the one dimension that carries that source's answer;
    * the final fact list is asserted unique by AGT_Record_ID and equal in size
      to the source-record set.
    Evidence is keyed by the canonical ENTITY by design (an entity verified for
    an earlier delivery keeps its evidence); the cutoff bounds it in time.

ASSESSMENT RUN AND EVIDENCE CUTOFF
    The assessment date comes from the selected run (explicit `run_id`, else the
    intake's own current completed run) via `assessment_context`, validated
    against the intake - never "the latest run in the table". The evidence cutoff
    is REQUIRED and explicit: nothing newer than it is read. No completed run, or
    no cutoff, is a refusal.

PERSISTED CLASSIFICATION ONLY
    A bucket is `review_records.classification_bucket` of the selected review
    and nothing else. No analyst `reclassified_to`, no spreadsheet analysis. A
    non-HELD record without a usable one gets NO bucket and the dataset reports
    itself INCOMPLETE. HELD is reporting-only (counted once in B4 by the dataset).

ANNOTATIONS AND COMMENTS ARE INPUTS, NOT DATABASE FACTS
    `annotations` (supplemental NPI, proposed address, provisional/Q1 notes,
    cause-specific PECOS limitation, report clarifications) and `comments`
    (original / second QA comment columns) come from retained, attributed files
    supplied by the caller; the comment store is an open owner decision. Ids not
    in the selected intake are REJECTED and listed, never attached. A supplemental
    NPI is carried in its own field and never fills the submitted NPI.

QHIN NUMBER
    The delivered `orgManagingOrg` value, verbatim (available for every source
    record). Its equivalence to the owner's "QHIN Number" column is UNVERIFIED
    and is stated in `meta`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import and_, func, or_, select

LOADER_VERSION = "1.0.0"
BATCH_SIZE = 2000
BUCKETS = ("B1", "B2", "B3", "B4")

#: annotation keys a caller may supply (anything else is a typo -> refusal)
ANNOTATION_KEYS = frozenset({
    "supplemental_npi", "proposed_address", "flag", "provisional",
    "bucket_annotation", "report_clarifications", "pecos_limitation",
    "recommendation", "issue_type", "issue_severity"})

#: dataset fact key -> (persisted evidence source, the ONE dimension carrying it)
EVIDENCE_SOURCES = {
    "nppes": ("NPPES", "IDENTITY"),
    "pecos": ("CMS_PPEF_ENROLLMENT", "MEDICARE_ENROLLMENT"),
    "leie": ("OIG_LEIE", "EXCLUSION_REVOCATION"),
    "sam": ("SAM_GOV", "EXCLUSION_REVOCATION"),
}
NOT_PROMOTED = ("Not established: the record was not promoted to an entity, so "
                "no verification evidence exists for it")
NO_EVIDENCE = "Not established: no evidence row at or before the cutoff"


class LoaderRefused(ValueError):
    """The dataset could not be loaded honestly, and the reason is stated."""


def _uuid(value: Any, what: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise LoaderRefused(f"{what} {value!r} is not a UUID")


def _cutoff(value: Any) -> datetime:
    """A naive-UTC datetime (the DB columns are naive UTC), or a refusal."""
    if value is None or value == "":
        raise LoaderRefused(
            "evidence_cutoff is required: the loader reads nothing newer than "
            "an explicit instant and never 'the latest'")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise LoaderRefused(f"evidence_cutoff {value!r} is not an ISO datetime")
    if not isinstance(value, datetime):
        raise LoaderRefused("evidence_cutoff must be a datetime or ISO string")
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _activity(raw: Any) -> str:
    from app.tefca_registry.rce.field_map import normalize_active
    n = normalize_active(_s(raw))
    return {"1": "Active", "0": "Inactive", "": ""}.get(n, n)


def _address(parsed: Mapping[str, Any]) -> str:
    parts = [_s(parsed.get(k)).strip() for k in
             ("address_line", "address_city", "address_state",
              "address_postalCode", "address_country")]
    return ", ".join(p for p in parts if p)


def _iso(dt: Any) -> str:
    return dt.isoformat() if isinstance(dt, datetime) else _s(dt)


def _evidence_cell(kind: str, row: Any, limitation: str = "") -> str:
    """The source's ACTUAL recorded status, its interpreted classifier state
    where that differs, when it was retrieved, and - for PECOS - the recorded
    cause note. Never a bare 'ok'."""
    from app.tefca_registry.rce import arc_pipeline as ap

    item = {"source": row.source, "disposition": row.disposition,
            "normalized_values": row.normalized_values or {}}
    state = ap.item_state(row.evidence_dimension, item)
    text = _s(row.disposition)
    if ap.is_registration_only_sam_miss(row.evidence_dimension, item):
        text += (" (SAM registration-only search; the exclusion question was "
                 "NOT screened)")
    if state and state.lower() != text.lower():
        text += f" -> {state}"
    when = _s(row.retrieved_at or row.generation_timestamp or _iso(row.created_at))
    text += f" | retrieved {when[:10]}" if when else ""
    if kind == "pecos" and _s(row.disposition).upper() not in ("PASS", "CORROBORATED"):
        note = _s(row.note).strip()
        if note:
            text += f" | recorded note: {note[:200]}"
    if limitation:
        text += f" | Limitation: {limitation}"
    return text


def _entity_type_link(nppes_row: Any, submitted_npi: str) -> Dict[str, str]:
    """Entity_Type only with an established ORIGINAL-SUBMITTED-NPI link: the NPI
    NPPES returned equals the submitted NPI. A blank submitted NPI (e.g. a
    record whose NPPES row was matched some other way) never establishes it."""
    if nppes_row is None:
        return {}
    orig = nppes_row.original_values or {}
    returned = _s(orig.get("npi")).strip()
    enum = _s(orig.get("enumeration_type")).strip()
    submitted = submitted_npi.strip()
    if enum and submitted and returned and returned == submitted:
        return {"entity_type": enum, "entity_type_basis": "SUBMITTED_NPI_MATCH"}
    return {}


async def _intake(db, intake_uuid):
    from app.tefca_registry.rce import models as m
    intake = await db.get(m.RceSourceIntake, intake_uuid)
    if intake is None:
        raise LoaderRefused(f"No delivery (intake) {intake_uuid}")
    return intake


async def load_record_facts(
        db, *, intake_id: Any, evidence_cutoff: Any, run_id: Any = None,
        annotations: Optional[Mapping[str, Mapping[str, Any]]] = None,
        comments: Optional[Mapping[str, Mapping[str, Any]]] = None,
        batch_size: int = BATCH_SIZE) -> Dict[str, Any]:
    """Return {"facts": [...], "meta": {...}, "assessment_date": "YYYY-MM-DD"}."""
    from app.Tefca.models import TEFCADimensionEvidence as E
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.assessment_context import resolve_assessment_date

    intake_uuid = _uuid(intake_id, "intake_id")
    cutoff = _cutoff(evidence_cutoff)
    intake = await _intake(db, intake_uuid)

    try:
        assessment = await resolve_assessment_date(db, intake_uuid, run_id=run_id)
    except ValueError as exc:
        raise LoaderRefused(str(exc))
    if not assessment.from_run:
        raise LoaderRefused(
            "the delivery has no completed assessment run, so there is no "
            "assessment date to report as of; run the quality assessment or "
            "name a completed run for THIS delivery")

    annotations = dict(annotations or {})
    comments = dict(comments or {})
    for agt, ann in annotations.items():
        extra = set(ann) - ANNOTATION_KEYS
        if extra:
            raise LoaderRefused(f"annotation for {agt} has unknown key(s) {sorted(extra)}")

    facts: List[Dict[str, Any]] = []
    seen: set = set()
    stats = {"held": 0, "rejected": 0, "no_curated_row": 0, "not_promoted": 0,
             "classified": 0, "no_review": 0, "review_without_bucket": 0,
             "records_with_multiple_reviews": 0, "evidence_rows_used": 0,
             "with_supplemental_npi": 0}
    last_line = -1
    while True:
        recs = (await db.execute(
            select(m.RceSourceRecord)
            .where(m.RceSourceRecord.source_intake_id == intake_uuid,
                   m.RceSourceRecord.line_number > last_line)
            .order_by(m.RceSourceRecord.line_number)
            .limit(batch_size))).scalars().all()
        if not recs:
            break
        last_line = recs[-1].line_number
        ids = [r.id for r in recs]

        curated = {c.source_record_id: c for c in (await db.execute(
            select(m.RceCuratedRecord).where(
                m.RceCuratedRecord.source_record_id.in_(ids),
                m.RceCuratedRecord.source_intake_id == intake_uuid))).scalars().all()}

        reviews = {r.source_record_id: r for r in (await db.execute(
            select(reg.ReviewRecord)
            .where(reg.ReviewRecord.source_record_id.in_(ids),
                   reg.ReviewRecord.created_at <= cutoff)
            .distinct(reg.ReviewRecord.source_record_id)
            .order_by(reg.ReviewRecord.source_record_id,
                      reg.ReviewRecord.created_at.desc(),
                      reg.ReviewRecord.id.desc()))).scalars().all()}
        stats["records_with_multiple_reviews"] += len((await db.execute(
            select(reg.ReviewRecord.source_record_id)
            .where(reg.ReviewRecord.source_record_id.in_(ids),
                   reg.ReviewRecord.created_at <= cutoff)
            .group_by(reg.ReviewRecord.source_record_id)
            .having(func.count() > 1))).all())

        entity_of = {sid: str(c.canonical_entity_id) for sid, c in curated.items()
                     if c.canonical_entity_id is not None}
        evidence: Dict[tuple, Any] = {}
        if entity_of:
            conds = []
            for source, dim in set(EVIDENCE_SOURCES.values()):
                conds.append(and_(E.source == source, E.evidence_dimension == dim))
            for row in (await db.execute(
                    select(E)
                    .where(E.entity_id.in_(sorted(set(entity_of.values()))),
                           or_(*conds), E.created_at <= cutoff)
                    .distinct(E.entity_id, E.source)
                    .order_by(E.entity_id, E.source, E.created_at.desc(),
                              E.id.desc()))).scalars().all():
                evidence[(row.entity_id, row.source)] = row

        for rec in recs:
            parsed = rec.parsed or {}
            cur = curated.get(rec.id)
            rev = reviews.get(rec.id)
            ent = entity_of.get(rec.id)
            agt = str(rec.id)
            ann = annotations.get(agt) or {}
            cm = comments.get(agt) or {}
            submitted = _s(parsed["NPI"]) if "NPI" in parsed else _s(rec.npi)

            status = cur.record_status if cur is not None else "NOT CURATED"
            if cur is None:
                stats["no_curated_row"] += 1
            elif cur.record_status == "HELD":
                stats["held"] += 1
            elif cur.record_status == "REJECTED":
                stats["rejected"] += 1
            if ent is None and (cur is None or cur.record_status != "HELD"):
                stats["not_promoted"] += 1

            bucket, basis, reasons = None, "", ""
            if rev is None:
                stats["no_review"] += 1
                basis = "no review record at or before the cutoff"
            elif rev.classification_bucket in BUCKETS:
                bucket = rev.classification_bucket
                stats["classified"] += 1
                basis = (f"Persisted classification: rule {_s(rev.classification_rule)} "
                         f"v{_s(rev.classification_rule_version)}; review {rev.review_id}")
                reasons = _s(rev.classification_rationale)
            else:
                stats["review_without_bucket"] += 1
                basis = f"latest review {rev.review_id} carries no classification"
            if cur is not None and cur.record_status in ("HELD", "REJECTED") and not reasons:
                reasons = _s(cur.status_reason)

            cells: Dict[str, str] = {}
            ev_refs: List[str] = []
            nppes_row = None
            for key, (source, _dim) in EVIDENCE_SOURCES.items():
                if ent is None:
                    cells[key] = NOT_PROMOTED
                    continue
                row = evidence.get((ent, source))
                if row is None:
                    cells[key] = NO_EVIDENCE
                    continue
                stats["evidence_rows_used"] += 1
                ev_refs.append(f"{source}:{row.id}@{_s(row.generation_timestamp)}")
                if key == "nppes":
                    nppes_row = row
                cells[key] = _evidence_cell(
                    key, row, _s(ann.get("pecos_limitation")) if key == "pecos" else "")
            if ent is None and ann.get("pecos_limitation"):
                cells["pecos"] += f" | Limitation: {_s(ann['pecos_limitation'])}"

            flag_parts = []
            if status in ("HELD", "REJECTED", "NOT CURATED"):
                flag_parts.append(status)
            if ann.get("flag"):
                flag_parts.append(_s(ann["flag"]))

            supplemental = _s(ann.get("supplemental_npi"))
            if supplemental:
                stats["with_supplemental_npi"] += 1

            fact: Dict[str, Any] = {
                "agt_record_id": agt,
                "source_line": rec.line_number,
                "qhin_number": _s(parsed.get("orgManagingOrg")),
                "hcid": _s(rec.hcid or parsed.get("HCID")),
                "tefca_id": _s(rec.tefcaid or parsed.get("TEFCAID")),
                "organization": _s(parsed.get("name")),
                "participation_level": _s(parsed.get("sequoiaorgtype")),
                "activity_status": _activity(parsed.get("active")),
                "purpose_of_use": _s(parsed.get("purposesofuse")),
                "submitted_npi": submitted,
                "supplemental_npi": supplemental,
                "bucket": bucket, "status": status, "bucket_basis": basis,
                "held_reason": _s(cur.status_reason) if cur is not None else "",
                "flag": "; ".join(flag_parts),
                "provisional": bool(ann.get("provisional")),
                "bucket_annotation": _s(ann.get("bucket_annotation")),
                "nppes": cells["nppes"], "pecos": cells["pecos"],
                "leie": cells["leie"], "sam": cells["sam"],
                # No persisted USPS validation exists in the evidence store:
                # the dataset renders "Not checked—unavailable".
                "usps": "",
                "submitted_address": _address(parsed),
                "proposed_address": _s(ann.get("proposed_address")),
                "reasons": reasons,
                "issue_type": _s(ann.get("issue_type")),
                "issue_severity": _s(ann.get("issue_severity")),
                "recommendation": _s(ann.get("recommendation")),
                "report_clarifications": _s(ann.get("report_clarifications")),
                "evidence_reference": "; ".join([
                    f"source_record={agt}",
                    f"source_rce_id={_s(rec.source_rce_id) or '-'}",
                    f"source_line={rec.line_number}",
                    f"entity={ent or '-'}",
                    f"review={rev.review_id if rev is not None else '-'}",
                    f"evidence={', '.join(ev_refs) or '-'}",
                    f"assessment_run={assessment.run_id}",
                    f"cutoff={cutoff.isoformat()}"]),
            }
            fact.update(_entity_type_link(nppes_row, submitted))
            orig = cm.get("original") or {}
            izaan = cm.get("izaan") or {}
            if orig.get("text"):
                fact["qa_comments"] = orig["text"]
                fact["qa_comments_source"] = orig.get("source")
            if izaan.get("text"):
                fact["qa_comments_izaan"] = izaan["text"]
                fact["qa_comments_izaan_source"] = izaan.get("source")
            if agt in seen:                                   # cannot happen
                raise LoaderRefused(f"duplicate AGT_Record_ID {agt} produced by the joins")
            seen.add(agt)
            facts.append(fact)

    # Fan-out / completeness guards, checked rather than trusted.
    total = int((await db.execute(
        select(func.count()).select_from(m.RceSourceRecord)
        .where(m.RceSourceRecord.source_intake_id == intake_uuid))).scalar() or 0)
    if len(facts) != total:
        raise LoaderRefused(f"loaded {len(facts)} facts for {total} source records")

    rejected_annotations = sorted(a for a in annotations if a not in seen)
    rejected_comments = sorted(c for c in comments if c not in seen)
    meta = {
        "loader_version": LOADER_VERSION,
        "intake_id": str(intake_uuid),
        "delivery_label": intake.delivery_label,
        "intake_sha256": intake.sha256,
        "intake_record_count": intake.record_count,
        "source_records_loaded": len(facts),
        "row_count_matches": (intake.record_count is None
                              or int(intake.record_count) == len(facts)),
        "assessment_run_id": assessment.run_id,
        "assessment_date": assessment.value.isoformat(),
        "assessment_date_source": assessment.source,
        "evidence_cutoff": cutoff.isoformat(),
        "stats": stats,
        "rejected_annotation_ids": rejected_annotations,
        "rejected_comment_ids": rejected_comments,
        "notes": [
            "AGT_Record_ID is rce_source_records.id; source_rce_id, entity id and "
            "review_id are separate and labelled in Evidence Reference.",
            "QHIN Number is the delivered orgManagingOrg value verbatim; its "
            "equivalence to the owner's QHIN Number column is UNVERIFIED.",
            "Bucket is the persisted classification_bucket only; no analyst "
            "reclassification and no spreadsheet analysis is substituted.",
            "Annotations and comments are caller-supplied inputs from retained "
            "files; the comment store is an open owner decision.",
            "No persisted USPS validation exists; USPS reads Not checked-unavailable."],
    }
    return {"facts": facts, "meta": meta,
            "assessment_date": assessment.value.isoformat()}


async def load_dataset(
        db, *, intake_id: Any, evidence_cutoff: Any, report_id: str,
        run_id: Any = None, annotations=None, comments=None,
        findings_count: int = 0, classification: str = "DRAFT") -> Dict[str, Any]:
    """Load the facts and build the shared dataset (the one every format renders)."""
    from app.reports.data import october_review_dataset as ds

    loaded = await load_record_facts(
        db, intake_id=intake_id, evidence_cutoff=evidence_cutoff, run_id=run_id,
        annotations=annotations, comments=comments)
    return ds.build_dataset(
        loaded["facts"], report_id=report_id, findings_count=findings_count,
        assessment_date=loaded["assessment_date"], classification=classification,
        load_meta=loaded["meta"])
