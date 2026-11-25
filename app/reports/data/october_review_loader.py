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
    ATTRIBUTION (2026-11-24): a row whose RECORDED query identifier positively
    names a different NPI, or an organisation name that is not the delivered
    name, is another record's result. It is never shown as this record's: the
    record's own newest row at/before the cutoff is used instead, and where
    none exists the cell says "Not attributed to this record". LIMITS, stated
    rather than guessed, for OIG LEIE / SAM / SAM extract: a row is judged only
    when its identifier is an NPI or the source's own name key; a `uei=` SAM
    row or an older row with no identifier is NOT judged and is shown as
    before. The reselect is stricter than the first pass: it accepts
    only a row positively keyed by this record's NPI or delivered name, so a
    foreign newest row over an older `uei=` row yields "Not attributed". A
    name-keyed row is withheld whenever the queried name is not the delivered
    name, including a screen run on the entity's name for this same record.
    NPPES AND CMS PPEF ENROLLMENT (2026-11-25) are compared by the
    NPI the STORED row carries (`_stored_evidence_npis`), because neither
    stores an `npi=` query identifier: NPPES rows have a NULL query identifier
    and carry the NPI of the stored answer (`source_record_identifier` /
    `original_values.npi`: the NPI NPPES returned, or the NPI asked about for a
    not-found answer); CMS rows carry `<dataset>:<component>:NPI=<npi>` when
    answered by the CMS data API and `snapshot:<id>` when answered from an
    ingested edition, plus the NPI on each returned enrolment record. A result
    of these two sources is placed in a record's cell ONLY when that stored NPI
    equals the record's submitted NPI, and the cell then says "NPI match only
    [N1]". THAT IS AN IDENTIFIER MATCH, NOT RECORD ATTRIBUTION: stored evidence
    carries no delivery id and no delivered-record id, so an equal NPI does not
    establish that the check was run for this delivery or for this delivered
    row. A stored NPI that differs (or any stored NPI for a record that
    submitted none) is another identifier's answer: the newest row whose stored
    NPI equals the submitted one is used instead, else the cell says "Not
    attributed to this record [N2]" and shows no result. A row that stores NO
    NPI at all (every UNAVAILABLE row; a CMS not-found answered from an
    ingested edition; rows older than the fields) is "Not attributed to this
    record [N3]" followed by what is stored, as the entity's - never dropped,
    never promoted to the record. An NPI-matching row written more than a day
    before the delivery's recorded receipt date, or recorded under a review of
    a different delivered record, is kept and labelled "carried from ... [N4]"
    with its observation date; a row written after receipt by another
    delivery's run cannot be told apart and gets no such label. The markers
    are explained once, in `meta.evidence_notes` (`EVIDENCE_NOTES`), which
    every rendering prints; [N5] states that the OIG LEIE / SAM cells are not
    compared this way. Presentation only: no bucket, rule, classification or
    stored evidence row is read differently or changed.

ASSESSMENT RUN AND EVIDENCE CUTOFF
    The assessment date comes from the selected run (explicit `run_id`, else the
    intake's own current completed run) via `assessment_context`, validated
    against the intake - never "the latest run in the table". The evidence cutoff
    is REQUIRED and explicit: no evidence, review or comment newer than it is
    read. ONE EXCEPTION: curated status and hold reasons (`rce_curated_records`,
    one mutable row per record with no status history) are read as they are when
    the loader runs; the dataset states this as `curated_status_basis`. The
    export job loads ONCE per delivery + run + cutoff and freezes the result
    (`record_review_freeze`): every format states the freeze time, and the
    status is the status at that time, never the status at the cutoff. A row
    count that does not match the delivery's recorded count is a refusal. No completed
    run, or no cutoff, is a refusal.

REVIEW LINEAGE (review_records.source_record_id)
    New reviews carry the delivered record they are ABOUT: the bulk writer takes it
    from the NAMED delivery's own curated row (exactly one, linking the resolved
    entity), the single-record writer only from an explicit, validated id. Reviews
    created before that, and entity-only reviews (priority review, entity verify),
    carry NO anchor. They are never attached - not by entity, not by guesswork -
    and never rewritten: a record whose only reviews are unanchored stays
    unclassified and says so (`unanchored entity reviews`), so the gap is explicit
    and the dataset INCOMPLETE rather than silently filled.

PERSISTED CLASSIFICATION ONLY
    A bucket is `review_records.classification_bucket` of the selected review
    and nothing else. No analyst `reclassified_to`, no spreadsheet analysis. A
    non-HELD record without a usable one gets NO bucket and the dataset reports
    itself INCOMPLETE. HELD is reporting-only (counted once in B4 by the dataset).

STORED COMMENTS (rce_record_comments, when the migration has been applied)
    The CURRENT (not superseded) comment of each kind is read for the record, VERBATIM,
    with its source file/hash/sheet/column: QA_ORIGINAL -> "QA Comments (verbatim)",
    QA_IZAAN -> "QA Comments - Izaan (verbatim)", REPORT_CLARIFICATION -> "Report
    Clarifications". The kinds are never merged. They are commentary only: they feed no
    bucket, status or basis. If the table does not exist yet the loader says so in
    `meta.comment_store` and carries no stored comments - it never fails or invents.
    Comments of another delivery cannot appear (scoped to the intake AND the records).

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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import and_, exists, func, or_, select, text

from app.reports.data import reviewed_disposition as rd
from app.reports.data.october_review_dataset import STORED_COMMENT_KEY

LOADER_VERSION = "1.6.0"
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
    # The GSA public exclusions EXTRACT is a different source with different limits
    # from the live api.sam.gov check: it writes its own rows (never SAM_GOV rows),
    # so it is read and reported on its own. An unavailable API result is never
    # relabelled as an extract result, nor the reverse.
    "sam_extract": ("SAM_EXTRACT_SUPPLEMENTARY", "EXCLUSION_REVOCATION"),
}
NOT_PROMOTED = ("Not established by this loader: the record has no linked entity, "
                "so no entity-linked verification evidence is available to it "
                "here. This does not show that no source screening occurred "
                "elsewhere")
NO_EVIDENCE = ("Not established by this loader: no evidence row for the linked "
               "entity at or before the cutoff exists in the evidence store it "
               "reads. This does not show the source was never checked elsewhere")
NOT_ATTRIBUTED = ("Not attributed to this record: the evidence on file for the linked entity at or "
                  "before the cutoff was queried by {why}. It is not established to be this record's "
                  "result and is not shown as this one's. This does not show the source was never "
                  "checked for this record elsewhere")
#: how many of an entity's rows of one source are read when looking for the record's own row
OWN_ROW_LOOKBACK = 200
NO_ANCHORED_REVIEW = "no review anchored to this source record at or before the cutoff"

#: Sources whose rows are compared with a delivered record by the NPI the STORED row carries (they store no
#: `npi=` query identifier, so the query-identifier rule above cannot judge them). source -> label.
NPI_ANSWER_SOURCES = {"NPPES": "NPPES", "CMS_PPEF_ENROLLMENT": "CMS PPEF enrolment"}
#: Cell texts are short and carry a marker; each marker is explained ONCE in `EVIDENCE_NOTES`, which every
#: rendering prints (first CSV line, the Excel "Evidence Notes" sheet, the PDF, the frozen JSON meta).
NPI_MATCH_ONLY = "NPI match only [N1]"
NOT_ATTRIBUTED_ANSWER = ("Not attributed to this record [N2]: the stored row concerns {why} ({basis}); "
                         "observed {observed} UTC")
NOT_ATTRIBUTED_UNRECORDED = ("Not attributed to this record [N3]: no identifier stored on the row; entity-level, as "
                             "recorded (observed {observed} UTC): {stored}")
CARRIED_BEFORE_RECEIPT = ("carried from an earlier check [N4]: observed {observed} UTC, more than a day before the "
                          "recorded receipt date {received}")
CARRIED_OTHER_RECORD = ("carried from a check recorded for a different delivered record [N4]: observed {observed} "
                        "UTC")
#: An evidence row is called "before receipt" only when it was written more than this long before the recorded
#: receipt value. `rce_source_intakes.received_at` is the operator-stated receipt date (often a bare date) with no
#: time zone, while evidence `created_at` is UTC; a full day covers every time zone the date could have meant.
RECEIPT_MARGIN = timedelta(days=1)
EVIDENCE_NOTES = (
    ("N1", "Identifier match only. The NPI stored on the NPPES / CMS PPEF enrolment evidence row equals the NPI "
           "submitted on this record. This does NOT establish that the check was run for this delivery or for "
           "this delivered row: stored evidence is keyed by the linked entity and carries no delivery or record "
           "id."),
    ("N2", "The evidence row on file for the linked entity at or before the cutoff stores a different NPI than "
           "this record submitted (or an NPI where this record submitted none). It is not established to be this "
           "record's result, so no result is shown; the row is listed in Evidence Reference. This does not show "
           "the source was never checked for this record elsewhere."),
    ("N3", "The newest evidence row on file for the linked entity at or before the cutoff stores no identifier "
           "(an unavailable answer, or a CMS not-found answered from an ingested edition). Its recorded status "
           "is quoted without interpretation as the ENTITY's; it is NOT established as this record's result."),
    ("N4", "Carried evidence: the row was written more than a day before this delivery's recorded receipt date "
           "(the receipt date carries no time zone; evidence times are UTC), or is recorded under a review of a "
           "different delivered record. LIMIT: a row written after receipt by another delivery's coverage or "
           "recheck run cannot be told apart and carries NO carried label."),
    ("N5", "OIG LEIE, SAM.gov and SAM public-extract cells are NOT compared with the NPI of a stored answer and "
           "never carry N1 or N4. They are withheld only when the recorded query identifier names another NPI or "
           "organisation name; a row with no recorded identifier, or keyed by a UEI, is shown without any "
           "attribution check."),
)


def _stored_evidence_npis(source: str, row: Any) -> tuple:
    """(NPIs the stored row carries, where they were read from). Empty when the row stores none.

    Exactly what is stored, in order of how directly it states the QUERY: a recorded `npi=` / bare-NPI query
    identifier; the CMS data-API filter (`<dataset>:<component>:NPI=<npi>`); then the NPI on the stored ANSWER
    (NPPES `source_record_identifier` / `original_values.npi`; the `npi` of each stored CMS enrolment record).
    Nothing is inferred from the entity, from a sibling row of the same generation, or from the delivery."""
    import re

    from app.tefca_registry import review_service as rs

    qid = _s(getattr(row, "query_identifier", None)).strip()
    queried = rs.evidence_query_npi(qid)
    if queried and (qid.casefold().startswith("npi=") or (queried.isdigit() and len(queried) == 10)):
        return {queried}, "the recorded query identifier"
    orig = row.original_values if isinstance(row.original_values, dict) else {}
    if source == "CMS_PPEF_ENROLLMENT":
        m_ = re.search(r"(?:^|[:,])NPI=([^,]+)", qid)
        if m_ and m_.group(1).strip():
            return {m_.group(1).strip()}, "the recorded CMS query filter"
        records = orig.get("records") if isinstance(orig.get("records"), list) else []
        npis = {_s(r.get("npi") or r.get("NPI")).strip() for r in records if isinstance(r, dict)}
        npis.discard("")
        return npis, "the NPI on the stored enrolment record(s)"
    if source == "NPPES":
        stored = _s(row.source_record_identifier).strip() or _s(orig.get("npi")).strip()
        return ({stored} if stored else set()), "the NPI on the stored NPPES answer"
    return set(), ""


def _answer_attribution(source: str, row: Any, submitted_npi: str) -> tuple:
    """("npi_match" | "foreign" | "unrecorded", reason, basis) for a row of an `NPI_ANSWER_SOURCES` source.

    npi_match: the stored NPI is exactly this record's submitted NPI - an IDENTIFIER match and nothing more (the
    row carries no delivery or record id, so it does not show the check was run for this delivered row).
    foreign: the row stores an NPI and it is not (or the record submitted none). unrecorded: the row stores no
    NPI, so neither can be shown."""
    npis, basis = _stored_evidence_npis(source, row)
    submitted = _s(submitted_npi).strip()
    if not npis:
        return "unrecorded", "", ""
    if not submitted:
        return "foreign", "an NPI although this record's submitted NPI is blank", basis
    if npis == {submitted}:
        return "npi_match", "", basis
    return "foreign", "a different NPI than this record's submitted NPI", basis


def _observed(row: Any) -> str:
    """The day the evidence row was written (the instant the cutoff is applied to), or 'date not recorded'."""
    return row.created_at.date().isoformat() if isinstance(row.created_at, datetime) else "date not recorded"


def _carried_note(row: Any, *, received_at: Any, record_id: Any, review_anchor: Mapping[str, Any]) -> str:
    """'' unless the stored row shows the check was not made for this delivered record: it is recorded under a
    review anchored to a DIFFERENT delivered record, or it was written more than `RECEIPT_MARGIN` before this
    delivery's recorded receipt value. Evidence rows carry no delivery or run id, so a row written after receipt
    by a coverage or recheck run of another delivery cannot be told apart here and gets no note (note N4).

    TIME ZONES, EXPLICITLY. Evidence `created_at` is written by the application as naive UTC. `received_at` is
    the operator-stated receipt date with no zone recorded (a value that does carry an offset is converted to
    UTC). The two are therefore not compared as instants: the label needs a full day of clearance, which no
    time-zone reading of the receipt date can close. Neither the database's nor the server's time zone is used."""
    anchor = review_anchor.get(_s(row.review_id)) if row.review_id else None
    if anchor is not None and anchor != record_id:
        return CARRIED_OTHER_RECORD.format(observed=_observed(row))
    if isinstance(row.created_at, datetime) and isinstance(received_at, datetime):
        received = (received_at.astimezone(timezone.utc).replace(tzinfo=None)
                    if received_at.tzinfo is not None else received_at)
        written = (row.created_at.astimezone(timezone.utc).replace(tzinfo=None)
                   if row.created_at.tzinfo is not None else row.created_at)
        if written < received - RECEIPT_MARGIN:
            return CARRIED_BEFORE_RECEIPT.format(observed=_observed(row), received=received.date().isoformat())
    return ""


async def _own_answer_row(db, E, *, entity_id: str, source: str, dimension: str, cutoff, submitted_npi: str):
    """The newest row of this entity and source, at or before the cutoff, whose STORED NPI equals this record's
    submitted NPI (an identifier match only), or None. Used only after the entity's newest row was found to be another NPI's answer."""
    for row in (await db.execute(
            select(E).where(E.entity_id == entity_id, E.source == source,
                            E.evidence_dimension == dimension, E.created_at <= cutoff)
            .order_by(E.created_at.desc(), E.id.desc()).limit(OWN_ROW_LOOKBACK))).scalars().all():
        if _answer_attribution(source, row, submitted_npi)[0] == "npi_match":
            return row
    return None


def _foreign_evidence_reason(source: str, query_identifier: Any, submitted_npi: str,
                             delivered_name: str) -> str:
    """'' unless the row's RECORDED query identifier positively names an NPI or an organisation
    name this record does not carry; otherwise a short reason (no identifier value is printed).

    Evidence rows are stored under the canonical ENTITY, which other deliveries (and coverage /
    recheck runs) share. The identifier a row was queried by is what ties it to a delivered
    record, under the same rules the single-record path uses (`review_service`): an NPI must be
    the record's own submitted NPI exactly; a name must equal the delivered name under
    `record_name_identity`. Deliberately conservative: a row with NO recorded identifier (older
    rows), an ambiguous bare value, or a key that is neither NPI nor name (`uei=`) is NOT judged
    here and is shown as before."""
    from app.tefca_registry import review_service as rs

    value = _s(query_identifier).strip()
    if not value:
        return ""
    submitted = _s(submitted_npi).strip()
    queried_npi = rs.evidence_query_npi(value)
    if queried_npi and (value.casefold().startswith("npi=") or (queried_npi.isdigit() and len(queried_npi) == 10)):
        if queried_npi.casefold() == submitted.casefold():
            return ""
        return ("an NPI although this record's submitted NPI is blank" if not submitted
                else "a different NPI than this record's submitted NPI")
    if rs.persisted_evidence_is_name_keyed(source, value):
        if rs.names_establish_one_identity(rs.evidence_query_name(source, value), delivered_name):
            return ""
        return "an organisation name that is not this record's delivered name"
    return ""


async def _own_evidence_row(db, E, *, entity_id: str, source: str, dimension: str, cutoff,
                            submitted_npi: str, delivered_name: str):
    """The newest row of this entity and source, at or before the cutoff, whose recorded query
    identifier IS one this record carries (`review_service.persisted_evidence_is_for_record`), or
    None. Used only after the entity's newest row was found to be another record's."""
    from app.tefca_registry import review_service as rs

    identity = rs.record_name_identity(delivered_name)
    for row in (await db.execute(
            select(E).where(E.entity_id == entity_id, E.source == source,
                            E.evidence_dimension == dimension, E.created_at <= cutoff)
            .order_by(E.created_at.desc(), E.id.desc()).limit(OWN_ROW_LOOKBACK))).scalars().all():
        if rs.persisted_evidence_is_for_record(source, row.query_identifier, submitted_npi=submitted_npi,
                                               record_identity=identity, names_usable=bool(identity)):
            return row
    return None


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


def _evidence_cell(kind: str, row: Any, limitation: str = "", *, interpret: bool = True) -> str:
    """The source's ACTUAL recorded status, its interpreted classifier state
    where that differs, when it was retrieved, and - for PECOS - the recorded
    cause note. Never a bare 'ok'. `interpret=False` quotes the recorded status
    only: a row that is not attributed to the record is never given a
    classifier reading ("-> verified") in that record's cell."""
    from app.tefca_registry.rce import arc_pipeline as ap

    item = {"source": row.source, "disposition": row.disposition,
            "normalized_values": row.normalized_values or {}}
    state = ap.item_state(row.evidence_dimension, item)
    text = _s(row.disposition)
    if ap.is_registration_only_sam_miss(row.evidence_dimension, item):
        text += (" (SAM registration-only search; the exclusion question was "
                 "NOT screened)")
    if interpret and state and state.lower() != text.lower():
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


def _sam_extract_cell(row: Any) -> str:
    """Extract result with its edition, check date and candidate status. A
    candidate is NEVER a confirmed exclusion; an unavailable extract says why."""
    orig = row.original_values or {}
    disp = _s(row.disposition).upper()
    if disp == "REVIEW":
        n = orig.get("candidate_count")
        text = (f"CANDIDATE MATCH ({n if n is not None else 'count not recorded'}) - NOT a confirmed "
                f"exclusion; analyst determination required")
    elif disp == "PASS":
        text = "No candidate match in the extract"
    elif disp == "UNAVAILABLE":
        text = "Extract screen unavailable - not performed"
        note = _s(row.note).strip()
        if note:
            text += f": {note[:160]}"
    else:
        text = _s(row.disposition)
    parts = [text]
    if orig.get("extract_filename") or orig.get("publication_date"):
        parts.append(f"edition {_s(orig.get('publication_date')) or 'date not recorded'} "
                     f"({_s(orig.get('extract_filename')) or 'file not recorded'})")
    if orig.get("extract_sha256"):
        parts.append(f"sha256 {_s(orig['extract_sha256'])[:12]}")
    when = _s(row.query_timestamp or row.retrieved_at or _iso(row.created_at))
    parts.append(f"checked {when[:10]}" if when else "check date not recorded")
    parts.append("supplementary to, not a substitute for, the live SAM.gov API check")
    return " | ".join(parts)


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


async def _comment_store_exists(db) -> bool:
    return (await db.execute(
        text("SELECT to_regclass('public.rce_record_comments')"))).scalar() is not None


def _src(c: Any) -> Dict[str, Any]:
    # `stored_comment_id` is carried for traceability. It does not by itself make
    # the dataset treat the comment as stored: the loader also records the id in
    # its own `stored_comments` result, and the dataset believes a source only
    # when the two agree. Caller-supplied sources have the key removed.
    return {"file": c.source_file_name, "sha256": c.source_file_sha256,
            "sheet": c.source_sheet, "column": c.source_column,
            STORED_COMMENT_KEY: str(c.id)}


#: Store kind -> the dataset's name for it.
_STORED_KIND = {"QA_ORIGINAL": "original", "QA_IZAAN": "izaan",
                "REPORT_CLARIFICATION": "clarification"}


def _caller_source(src: Any) -> Any:
    """A caller-supplied source can never claim to be a stored comment."""
    if isinstance(src, dict) and STORED_COMMENT_KEY in src:
        return {k: v for k, v in src.items() if k != STORED_COMMENT_KEY}
    return src


async def load_record_facts(
        db, *, intake_id: Any, evidence_cutoff: Any, run_id: Any = None,
        annotations: Optional[Mapping[str, Mapping[str, Any]]] = None,
        comments: Optional[Mapping[str, Mapping[str, Any]]] = None,
        include_stored_comments: bool = True,
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
    store_applied = bool(include_stored_comments) and await _comment_store_exists(db)
    if store_applied:
        from app.tefca_registry.rce.record_comment_models import RceRecordComment as C
        successor = C.__table__.alias("successor")
    for agt, ann in annotations.items():
        extra = set(ann) - ANNOTATION_KEYS
        if extra:
            raise LoaderRefused(f"annotation for {agt} has unknown key(s) {sorted(extra)}")

    # B1 evidence gate (owner authorization 2026-11-17): read once. Policy rows are the CURRENTLY active ones
    # (`load_active_policies` has no as-of); rule rows are loaded per recorded rule version, on demand.
    from app.tefca_registry.rce import shadow_reassessment as _sh
    from app.tefca_registry.rce import source_policy as _rce_source_policy
    policy_active = bool(await _rce_source_policy.load_active_policies(db))
    rules_by_version: Dict[Any, Any] = {}

    async def _rules_at(version):
        if version is None:
            return None
        if version not in rules_by_version:
            try:
                rules_by_version[version] = await _sh.rules_for_version(db, version)
            except _sh.ShadowRefused:
                rules_by_version[version] = None
        return rules_by_version[version]

    review_anchor: Dict[str, Any] = {}

    async def _anchors_for(review_ids):
        wanted = sorted({_s(r) for r in review_ids if r} - set(review_anchor))
        if not wanted:
            return
        for rid in wanted:
            review_anchor[rid] = None
        for rid, anchored_to in (await db.execute(
                select(reg.ReviewRecord.review_id, reg.ReviewRecord.source_record_id)
                .where(reg.ReviewRecord.review_id.in_(wanted)))).all():
            review_anchor[_s(rid)] = anchored_to

    facts: List[Dict[str, Any]] = []
    # AGT_Record_ID -> {kind: comment id}: which comment texts came from the store.
    stored_registry: Dict[str, Dict[str, str]] = {}
    seen: set = set()
    stats = {"held": 0, "rejected": 0, "no_curated_row": 0, "not_promoted": 0,
             "classified": 0, "no_review": 0, "review_without_bucket": 0,
             "records_with_multiple_reviews": 0, "evidence_rows_used": 0,
             "evidence_rows_not_attributed": 0, "evidence_rows_reselected_for_record": 0,
             "evidence_rows_identifier_not_recorded": 0, "evidence_rows_carried_from_another_check": 0,
             "evidence_rows_npi_match_only": 0,
             "records_with_unanchored_entity_reviews": 0,
             "stored_comments": {"QA_ORIGINAL": 0, "QA_IZAAN": 0, "REPORT_CLARIFICATION": 0},
             "records_with_multiple_current_comments_of_one_kind": 0,
             "caller_comments_overriding_stored": 0,
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
        from app.tefca_registry.rce import supplemental_research as _sr
        research = await _sr.current_by_record(db, intake_uuid, ids, cutoff=cutoff)     # {} until the migration is applied
        decisions = await rd.load_for_reviews(
            db, [r.review_id for r in reviews.values()], cutoff)
        stats["records_with_multiple_reviews"] += len((await db.execute(
            select(reg.ReviewRecord.source_record_id)
            .where(reg.ReviewRecord.source_record_id.in_(ids),
                   reg.ReviewRecord.created_at <= cutoff)
            .group_by(reg.ReviewRecord.source_record_id)
            .having(func.count() > 1))).all())

        stored_comments: Dict[Any, Dict[str, Any]] = {}
        if store_applied:
            for row in (await db.execute(
                    select(C).where(C.source_intake_id == intake_uuid,
                                    C.source_record_id.in_(ids),
                                    C.imported_at <= cutoff.replace(tzinfo=timezone.utc),
                                    ~exists().where(successor.c.supersedes_comment_id == C.id,
                                                    successor.c.imported_at <= cutoff.replace(tzinfo=timezone.utc)))
                    .order_by(C.imported_at, C.id))).scalars().all():
                slot = stored_comments.setdefault(row.source_record_id, {})
                if row.comment_kind in slot:
                    stats["records_with_multiple_current_comments_of_one_kind"] += 1
                slot[row.comment_kind] = row          # newest current wins; counted above
        entity_of = {sid: str(c.canonical_entity_id) for sid, c in curated.items()
                     if c.canonical_entity_id is not None}
        # Historical / entity-level reviews that name the linked entity but NO
        # source record. Counted for disclosure only - never attached.
        unanchored: Dict[str, int] = {}
        if entity_of:
            for ent_id, n_unanchored in (await db.execute(
                    select(reg.ReviewRecord.entity_id, func.count())
                    .where(reg.ReviewRecord.entity_id.in_(
                               [uuid.UUID(e) for e in sorted(set(entity_of.values()))]),
                           reg.ReviewRecord.source_record_id.is_(None),
                           reg.ReviewRecord.created_at <= cutoff)
                    .group_by(reg.ReviewRecord.entity_id))).all():
                unanchored[str(ent_id)] = int(n_unanchored)
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
        # review_id -> the delivered record that review is anchored to (None = unanchored), for the "carried from
        # a check recorded for a different delivered record" label. Read once per batch; reselected rows add to it.
        await _anchors_for(r.review_id for r in evidence.values() if r.source in NPI_ANSWER_SOURCES)

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
                basis = NO_ANCHORED_REVIEW
                n_unanchored = unanchored.get(ent or "", 0)
                if n_unanchored:
                    stats["records_with_unanchored_entity_reviews"] += 1
                    basis += (f"; {n_unanchored} historical review(s) of the linked "
                              f"entity carry no source-record anchor and were NOT "
                              f"attached (they cannot be mapped to this delivery's "
                              f"record), so the record stays unclassified")
            elif rev.classification_bucket in BUCKETS:
                bucket = rev.classification_bucket
                stats["classified"] += 1
                basis = (f"Persisted classification: rule {_s(rev.classification_rule)} "
                         f"v{_s(rev.classification_rule_version)}; review {rev.review_id}")
                reasons = _s(rev.classification_rationale)
            else:
                stats["review_without_bucket"] += 1
                basis = f"latest review {rev.review_id} carries no classification"
            if cur is not None and cur.record_status in ("HELD", "REJECTED"):
                # The exact hold reason is always shown. A review rationale may also exist; the two stay
                # distinguishable (rationale first) so a HELD record never loses why it is held.
                hold = _s(cur.status_reason)
                if hold and hold not in reasons:
                    reasons = f"{reasons} | Hold reason: {hold}" if reasons else hold

            cells: Dict[str, str] = {}
            # source -> the evidence row whose recorded identifier MATCHES this record's (identifier match only:
            # evidence carries no delivery or record id, so no row is established as "this record's check")
            own_rows: Dict[str, Any] = {}
            withheld_sources: List[str] = []   # sources whose row at the cutoff was NOT attributed to this record
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
                if source in NPI_ANSWER_SOURCES:
                    # NPPES / CMS: compared by the NPI the STORED row carries. An equal NPI is an identifier
                    # match only - never record attribution (see the module docstring and note N1).
                    limitation = _s(ann.get("pecos_limitation")) if key == "pecos" else ""
                    verdict, why, basis = _answer_attribution(source, row, submitted)
                    if verdict == "unrecorded":
                        # Nothing on the row says whose answer it is. Quoted exactly as stored, labelled as the
                        # entity's - not dropped, and not presented as this record's.
                        stats["evidence_rows_identifier_not_recorded"] += 1
                        withheld_sources.append(source)
                        ev_refs.append(f"{source}:{row.id}@{_s(row.generation_timestamp)} (not attributed to this "
                                       f"record: no identifier recorded)")
                        cells[key] = NOT_ATTRIBUTED_UNRECORDED.format(
                            observed=_observed(row),
                            stored=_evidence_cell(key, row, limitation, interpret=False))
                        continue
                    if verdict == "foreign":
                        foreign_row = row
                        row = await _own_answer_row(db, E, entity_id=ent, source=source, dimension=_dim,
                                                    cutoff=cutoff, submitted_npi=submitted)
                        if row is None:
                            stats["evidence_rows_not_attributed"] += 1
                            withheld_sources.append(source)
                            ev_refs.append(f"{source}:{foreign_row.id}@{_s(foreign_row.generation_timestamp)} "
                                           f"(not attributed to this record: other identifier)")
                            cells[key] = NOT_ATTRIBUTED_ANSWER.format(
                                why=why, basis=basis, observed=_observed(foreign_row))
                            continue
                        stats["evidence_rows_reselected_for_record"] += 1
                        await _anchors_for([row.review_id])
                    own_rows[source] = row
                    stats["evidence_rows_used"] += 1
                    ev_refs.append(f"{source}:{row.id}@{_s(row.generation_timestamp)}")
                    if key == "nppes":
                        nppes_row = row
                    stats["evidence_rows_npi_match_only"] += 1
                    cells[key] = f"{_evidence_cell(key, row, limitation)} | {NPI_MATCH_ONLY}"
                    carried = _carried_note(row, received_at=intake.received_at, record_id=rec.id,
                                            review_anchor=review_anchor)
                    if carried:
                        stats["evidence_rows_carried_from_another_check"] += 1
                        cells[key] += f" | {carried}"
                    continue
                # The entity's newest row may be ANOTHER delivered record's result (another delivery, a
                # coverage or recheck run keyed by a different identifier). It is never shown as this
                # record's: the record's own newest row is used instead, or the cell says so.
                foreign = _foreign_evidence_reason(source, row.query_identifier, submitted, _s(parsed.get("name")))
                if foreign:
                    row = await _own_evidence_row(db, E, entity_id=ent, source=source, dimension=_dim, cutoff=cutoff,
                                                  submitted_npi=submitted, delivered_name=_s(parsed.get("name")))
                    if row is None:
                        stats["evidence_rows_not_attributed"] += 1
                        withheld_sources.append(source)
                        cells[key] = NOT_ATTRIBUTED.format(why=foreign)
                        continue
                    stats["evidence_rows_reselected_for_record"] += 1
                own_rows[source] = row
                stats["evidence_rows_used"] += 1
                ev_refs.append(f"{source}:{row.id}@{_s(row.generation_timestamp)}")
                if key == "nppes":
                    nppes_row = row
                cells[key] = (_sam_extract_cell(row) if key == "sam_extract" else _evidence_cell(
                    key, row, _s(ann.get("pecos_limitation")) if key == "pecos" else ""))
            if ent is None and ann.get("pecos_limitation"):
                cells["pecos"] += f" | Limitation: {_s(ann['pecos_limitation'])}"

            flag_parts = []
            if status in ("HELD", "REJECTED", "NOT CURATED"):
                flag_parts.append(status)
            if ann.get("flag"):
                flag_parts.append(_s(ann["flag"]))

            stored_research = research.get(rec.id) or {}
            # A caller annotation still wins (history); otherwise the STORED research, which never touches the
            # submitted NPI above and is shown only as supplemental evidence.
            supplemental = _s(ann.get("supplemental_npi")) or _s(stored_research.get("researched_npi"))
            if supplemental:
                stats["with_supplemental_npi"] += 1

            approved = (rd.final_bucket_decision(decisions.get(rev.review_id, []), automated_bucket=bucket)
                        if rev is not None else {"bucket": "", "note": ""})
            b1 = {"established": False, "reason": ""}
            if approved["bucket"] == "B1" and bucket == "B4":
                b1 = rd.b1_evidence_verdict(
                    rev, rules=await _rules_at(rev.classification_rule_version), policy_active=policy_active,
                    # Only rows whose recorded identifier matches this record's are evaluated. A source whose row at the cutoff was
                    # another identifier's is named as not evaluated - never read as "no candidate".
                    cutoff_rows={"SAM_GOV": own_rows.get("SAM_GOV"), "OIG_LEIE": own_rows.get("OIG_LEIE"),
                                 "SAM extract": own_rows.get("SAM_EXTRACT_SUPPLEMENTARY")} if ent else None,
                    not_attributed=[s for s in withheld_sources
                                    if s in ("SAM_GOV", "OIG_LEIE", "SAM_EXTRACT_SUPPLEMENTARY")])

            # Part (a), owner authorization 2026-11-17: an approved B4 -> B2 is checked against the governing
            # review's STORED snapshot for a pending SAM / LEIE candidate (nothing saved later is read), and the
            # governing determination's actors and texts are carried verbatim to their own columns.
            b2 = (rd.stored_pending_candidates(rev) if approved["bucket"] == "B2" and bucket == "B4"
                  else {"evaluated": None, "pending": [], "undetermined": []})
            details = rd.decision_details(decisions.get(rev.review_id, []) if rev is not None else [])

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
                "supplemental_research": (
                    f"researched NPI {stored_research['researched_npi']} | NPPES reference: "
                    f"{stored_research['nppes_reference']} | research date {stored_research['nppes_check_date']} | "
                    f"researcher {stored_research['researcher']} | entered {stored_research['entered_at']} | "
                    f"rationale: {stored_research['match_rationale']} | entry {stored_research['id']}"
                    if stored_research else ""),
                "bucket": bucket, "status": status, "bucket_basis": basis,
                "held_reason": _s(cur.status_reason) if cur is not None else "",
                "flag": "; ".join(flag_parts),
                # Analyst/QA disposition, SEPARATE from the automated bucket above.
                **{"reviewed_" + k: v for k, v in (
                    rd.summarize(decisions.get(rev.review_id, []), automated_bucket=bucket).items()
                    if rev is not None else
                    {"disposition": "", "date": "", "qa_status": rd.NOT_REVIEWED, "basis": ""}.items())},
                # Owner authorization 2026-11-16: an independently QA-approved RECLASSIFY visible at the cutoff is
                # offered to the dataset as the reporting bucket. `bucket` above stays the persisted automated one.
                **{"approved_" + k: v for k, v in approved.items()},
                # A B1 target is reported only when saved evidence establishes it; today it never does (see
                # reviewed_disposition.b1_evidence_verdict) and the reason is carried to the Bucket Basis.
                "approved_b1_established": b1["established"], "approved_b1_reason": b1["reason"],
                "approved_b2_candidate_evaluated": b2["evaluated"], "approved_b2_pending": list(b2["pending"]),
                "approved_b2_undetermined": list(b2["undetermined"]),
                **{"decision_" + k: v for k, v in details.items()},
                "provisional": bool(ann.get("provisional")),
                "bucket_annotation": _s(ann.get("bucket_annotation")),
                "nppes": cells["nppes"], "pecos": cells["pecos"],
                "leie": cells["leie"], "sam": cells["sam"],
                "sam_extract": cells["sam_extract"],
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
            held_comments = stored_comments.get(rec.id) or {}
            for kind, text_key, src_key in (
                    ("QA_ORIGINAL", "qa_comments", "qa_comments_source"),
                    ("QA_IZAAN", "qa_comments_izaan", "qa_comments_izaan_source"),
                    ("REPORT_CLARIFICATION", "report_clarifications",
                     "report_clarifications_source")):
                row = held_comments.get(kind)
                if row is None:
                    continue
                stats["stored_comments"][kind] += 1
                if text_key == "report_clarifications" and fact.get("report_clarifications"):
                    continue                           # an explicit annotation wins
                fact[text_key] = row.comment_text      # VERBATIM
                fact[src_key] = _src(row)
                # The loader's own statement that THIS text came from the store.
                stored_registry.setdefault(agt, {})[_STORED_KIND[kind]] = str(row.id)
            orig = cm.get("original") or {}
            izaan = cm.get("izaan") or {}
            if orig.get("text"):
                if "qa_comments" in fact:
                    stats["caller_comments_overriding_stored"] += 1
                stored_registry.get(agt, {}).pop("original", None)
                fact["qa_comments"] = orig["text"]
                fact["qa_comments_source"] = _caller_source(orig.get("source"))
            if izaan.get("text"):
                if "qa_comments_izaan" in fact:
                    stats["caller_comments_overriding_stored"] += 1
                stored_registry.get(agt, {}).pop("izaan", None)
                fact["qa_comments_izaan"] = izaan["text"]
                fact["qa_comments_izaan_source"] = _caller_source(izaan.get("source"))
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
    # ENFORCED (2026-11-25): `row_count_matches` used to be computed and reported, and a report was produced
    # anyway. A dataset that does not reconcile with the delivery's own recorded count is refused with the two
    # numbers; nothing is rendered or frozen from it.
    if intake.record_count is not None and int(intake.record_count) != len(facts):
        raise LoaderRefused(
            f"row count mismatch: the delivery records {int(intake.record_count)} record(s) "
            f"(rce_source_intake.record_count) but {len(facts)} source record(s) were loaded for it. No report "
            f"is produced from a dataset that does not reconcile with the delivery; resolve the difference "
            f"(or correct the recorded count) and request the export again")

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
        "comment_store": ("applied" if store_applied else
                          "not_applied" if include_stored_comments else "not_requested"),
        "stats": stats,
        # Printed by every rendering; the cell markers [N1]..[N4] refer to these, [N5] is a standing limitation.
        "evidence_notes": [{"marker": marker, "text": text_} for marker, text_ in EVIDENCE_NOTES],
        "rejected_annotation_ids": rejected_annotations,
        "rejected_comment_ids": rejected_comments,
        "notes": [
            "AGT_Record_ID is rce_source_records.id; source_rce_id, entity id and "
            "review_id are separate and labelled in Evidence Reference.",
            "QHIN Number is the delivered orgManagingOrg value verbatim; its "
            "equivalence to the owner's QHIN Number column is UNVERIFIED.",
            "Reviews are attached only through review_records.source_record_id. "
            "Reviews created before that anchor existed, and entity-only reviews, "
            "carry none: they are not attached, not inferred from a shared entity "
            "and not rewritten - the record stays unclassified and says so.",
            "Bucket is the reporting bucket: the persisted classification_bucket, replaced only by an "
            "analyst RECLASSIFY of a B4 record to B2/B3 that a different recorded user approved in the "
            "QA gate at or before the cutoff (never for a HELD, rejected, not-curated or unclassified "
            "record). An approved move to B1 is NOT applied unless saved evidence establishes B1; the "
            "report has no as-of evaluator for evidence saved after the review, so such a record stays "
            "B4 with the reason in Bucket Basis and the analyst decision and QA approval in their own "
            "columns. The persisted bucket is kept in its own column; no spreadsheet analysis is "
            "substituted.",
            "An approved B4 -> B2 stays B4 when the governing review's STORED snapshot holds a pending SAM or "
            "LEIE candidate; that check does not see a candidate added after the review was saved. Every "
            "applied B2 move is marked PROVISIONAL. Analyst and QA reviewer identifiers, the analyst rationale "
            "and the QA reason are shown verbatim as stored. No finding type, evidence reference or manual "
            "check date is captured for a determination: the manual check date reads 'not recorded' and an "
            "applied move is flagged 'finding details not recorded'.",
            "Stored comments (rce_record_comments) are read verbatim by kind when the "
            "store exists; caller-supplied annotations/comments are inputs from retained "
            "files. Comments are commentary only and decide nothing.",
            "No persisted USPS validation exists; USPS reads Not checked-unavailable.",
            "Evidence cells are NOT record-attributed. Evidence is stored under the linked entity and carries no "
            "delivery or record id. For NPPES and CMS PPEF enrolment a result is placed in a record's cell only "
            "when the NPI stored on the evidence row equals the record's submitted NPI, and the cell says 'NPI "
            "match only [N1]': an identifier match, which does not establish that the check was run for this "
            "delivery or this delivered row. See evidence_notes N1-N5 for the markers and the standing limits "
            "(no carried label for another delivery's later run; OIG LEIE / SAM cells are not compared this "
            "way). The observation date is the UTC day the evidence row was written. No bucket or classification "
            "depends on these labels."],
    }
    return {"facts": facts, "meta": meta,
            "assessment_date": assessment.value.isoformat(),
            "stored_comments": {k: v for k, v in stored_registry.items() if v}}


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
        load_meta=loaded["meta"], stored_comments=loaded["stored_comments"])
