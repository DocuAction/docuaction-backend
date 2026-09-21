"""September 2026 ONC snapshot — rule set 1.3.0, relationship supersession,
persisted delta / presence / staleness.

Pure sections run anywhere; the two-delivery end-to-end needs the isolated
PostgreSQL (`rolled_back_db`). Every row is synthetic (ARC 9.99.777.9x); no
delivered ONC value appears here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import field_map as fm
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import quality_rules as qr
from app.tefca_registry.rce import reader
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import (  # noqa: F401
    QHIN_OID, SYN, base_row, make_rows, rolled_back_db, run_quality_and_curation,
    seed_intake,
)

OTHER_QHIN = "2.16.840.1.113883.3.9960"
ARC = "9.99.777.93"
P300, P700 = f"{ARC}.300", f"{ARC}.700"


# ── reader ───────────────────────────────────────────────────────────────────

def test_reader_honours_rfc4180_quoting_for_comma_files():
    headers = ["id", "name", "purposesofuse", "doa", "active", "NAIC"]
    blob = ("id,name,purposesofuse,doa,active,NAIC\r\n"
            '9.1,"Clinic, Inc.","T-TRTMNT,T-PYMNT","2.16.840.1.113883.17.4186",1,"04918"\r\n'
            ).encode("utf-8")
    read = reader.read_delivery(blob, expected_fields=tuple(headers))
    assert read.delimiter == ","
    line = read.lines[0]
    assert line.parse_status == reader.PARSE_OK
    assert line.parsed["name"] == "Clinic, Inc."
    assert line.parsed["purposesofuse"] == "T-TRTMNT,T-PYMNT"
    assert line.parsed["NAIC"] == "04918"          # leading zero preserved
    assert line.raw_line.startswith('9.1,"Clinic, Inc."')   # verbatim


def test_reader_pipe_file_without_quotes_is_split_exactly_as_before():
    values, note = reader.split_fields('a|b|c 12" wide|d', "|", 4)
    assert values == ["a", "b", 'c 12" wide', "d"] and note is None


def test_reader_stray_quote_falls_back_to_plain_split_with_note():
    # A quote that opens mid-cell would swallow the rest of the line under csv
    # rules; the plain split matches the header count, so it wins, noted.
    values, note = reader.split_fields('"open,b,c,d', ",", 4)
    assert len(values) == 4 and note and "plain split" in note


# ── normalisers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("1", "1"), ("0", "0"), ("1.0", "1"), ("0.0", "0"), ("", ""),
    ("yes", "UNSUPPORTED:yes"), ("2", "UNSUPPORTED:2"),
])
def test_normalize_active(raw, expected):
    assert fm.normalize_active(raw) == expected


def test_normalize_naic_keeps_leading_zero_and_strips_float_artifact():
    assert fm.normalize_naic("04918") == {"raw": "04918", "normalized": "04918",
                                          "format": "int", "valid": True}
    assert fm.normalize_naic("4918.0")["normalized"] == "4918"
    assert fm.normalize_naic("4918.0")["format"] == "float_artifact"
    assert fm.normalize_naic("abc")["valid"] is False


def test_purpose_vocabulary_is_the_eleven_delivered_tokens():
    assert len(fm.PURPOSE_VOCABULARY) == 11
    info = fm.classify_purpose_tokens("T-TRTMNT, T-PYMNT,T-TRTMNT,T-NOPE")
    assert info["tokens"] == ["T-TRTMNT", "T-PYMNT", "T-NOPE"]
    assert info["unknown"] == ["T-NOPE"]
    assert fm.classify_purpose_tokens("T-TRTMNT,")["malformed"] is True


def test_org_node_type_vocabulary_has_three_values():
    assert set(fm.ORG_NODE_TYPE_VOCABULARY) == {"initiating-node", "no-node", "passthrough-node"}


@pytest.mark.parametrize("value,ok", [
    ("2.16.840.1.113883.17.4186", True), ("1.2.3", True), ("2.16.840.1.", False),
    ("3.1.2", False), ("abc", False), ("", False), ("2.016.1", False),
])
def test_oid_syntax(value, ok):
    assert fm.is_oid_syntax(value) is ok


# ── rules (pure) ─────────────────────────────────────────────────────────────

def _ctx(dataset=None, **over) -> qr.RecordContext:
    values = base_row(**over)
    if not values.get("id"):          # base_row blanks every field, incl. id
        values["id"] = f"{ARC}.1"
    return qr.RecordContext(line_number=2, parse_status="ok",
                            field_count=len(fm.RCE_FIELDS), values=values,
                            dataset=dataset or {"known_source_ids": {values["id"]},
                                                "qhin_oids": {QHIN_OID}})


def _types(findings):
    return {(f.rule_id, f.issue_type, f.severity, f.correction_authority) for f in findings}


def test_rule_set_is_1_3_0_with_the_september_rules():
    assert qr.RULE_SET_VERSION == "1.3.0"
    ids = {r.rule_id for r in qr.RULES}
    assert {"SCH-003", "SO-002", "PUR-001", "PUR-002", "DOA-001", "DOA-002", "ACT-001"} <= ids


def test_con_003_active_forms():
    assert _types(qr._con_003(_ctx(active="1"))) == set()
    assert ("CON-003", "INACTIVE_RECORD", qr.INFO, qr.NO_CORRECTION) in _types(qr._con_003(_ctx(active="0")))
    got = _types(qr._con_003(_ctx(active="1.0")))
    assert ("CON-003", "ACTIVE_FORMAT_NORMALIZED", qr.INFO, qr.NO_CORRECTION) in got
    got = _types(qr._con_003(_ctx(active="0.0")))
    assert ("CON-003", "ACTIVE_FORMAT_NORMALIZED", qr.INFO, qr.NO_CORRECTION) in got
    assert ("CON-003", "INACTIVE_RECORD", qr.INFO, qr.NO_CORRECTION) in got
    assert _types(qr._con_003(_ctx(active="yes"))) == {
        ("CON-003", "UNSUPPORTED_ACTIVE_VALUE", qr.HIGH, qr.HUMAN_REQUIRED)}
    assert _types(qr._con_003(_ctx(active=""))) == {
        ("CON-003", "MISSING_ACTIVE_VALUE", qr.HIGH, qr.HUMAN_REQUIRED)}


def test_con_004_node_type_vocabulary():
    for ok in fm.ORG_NODE_TYPE_VOCABULARY:
        assert {f.issue_type for f in qr._con_004(_ctx(organizationNodeType=ok))} == {"NODE_TYPE_IS_NOT_HIERARCHY"}
    assert _types(qr._con_004(_ctx(organizationNodeType="root-node"))) == {
        ("CON-004", "UNKNOWN_ORG_NODE_TYPE", qr.HIGH, qr.HUMAN_REQUIRED)}


def test_so_002_naic_for_payers():
    assert _types(qr._so_002(_ctx(hl7orgrole="payer", NAIC=""))) == {
        ("SO-002", "MISSING_NAIC_FOR_PAYER", qr.HIGH, qr.HUMAN_REQUIRED)}
    assert qr._so_002(_ctx(hl7orgrole="payer", NAIC="04918")) == []
    got = qr._so_002(_ctx(hl7orgrole="payer", NAIC="4918.0"))
    assert _types(got) == {("SO-002", "NAIC_FLOAT_ARTIFACT", qr.INFO, qr.NO_CORRECTION)}
    assert got[0].suggested_value == "4918"
    assert _types(qr._so_002(_ctx(hl7orgrole="payer", NAIC="12"))) == {
        ("SO-002", "NAIC_FORMAT_INVALID", qr.MEDIUM, qr.HUMAN_REQUIRED)}
    assert qr._so_002(_ctx(hl7orgrole="provider", NAIC="")) == []
    assert {f.issue_type for f in qr._so_002(_ctx(hl7orgrole="provider", NAIC="04918"))} == {"NAIC_ON_NON_PAYER"}


def test_pur_rules():
    assert qr._pur_001(_ctx(purposesofuse="T-TRTMNT,T-PYMNT")) == []
    assert _types(qr._pur_001(_ctx(purposesofuse="T-TRTMNT,T-XYZ"))) == {
        ("PUR-001", "UNKNOWN_PURPOSE_TOKEN", qr.HIGH, qr.HUMAN_REQUIRED)}
    assert _types(qr._pur_002(_ctx(purposesofuse="T-TRTMNT,"))) == {
        ("PUR-002", "MALFORMED_PURPOSE_LIST", qr.MEDIUM, qr.HUMAN_REQUIRED)}
    got = qr._pur_002(_ctx(purposesofuse="T-TRTMNT; T-PYMNT"))
    assert _types(got) == {("PUR-002", "PURPOSE_LIST_NORMALIZED", qr.INFO, qr.NO_CORRECTION)}
    assert got[0].suggested_value == "T-TRTMNT,T-PYMNT"
    assert qr._pur_002(_ctx(purposesofuse="T-TRTMNT,T-PYMNT")) == []


def test_doa_rules_resolve_only_inside_the_tefca_namespace():
    # 2.999 is the ISO example arc; the synthetic 9.99.x ARC is not OID syntax.
    oa = "2.999.777.93"
    ds = {"known_source_ids": {f"{oa}.1", f"{oa}.2"}, "registry_oids": {f"{oa}.77"},
          "qhin_oids": {QHIN_OID}}
    assert qr._doa_001(_ctx(ds, doa=f"{oa}.2")) == []
    assert _types(qr._doa_001(_ctx(ds, doa="not-an-oid"))) == {
        ("DOA-001", "DOA_NOT_AN_OID", qr.MEDIUM, qr.HUMAN_REQUIRED)}
    assert qr._doa_002(_ctx(ds, doa=f"{oa}.2")) == []          # in delivery
    assert qr._doa_002(_ctx(ds, doa=f"{oa}.77")) == []         # in registry
    assert qr._doa_002(_ctx(ds, doa=QHIN_OID)) == []            # a QHIN
    assert _types(qr._doa_002(_ctx(ds, doa="2.16.840.1.113883.17.9999"))) == {
        ("DOA-002", "EXTERNAL_REFERENCE_UNVERIFIED", qr.MEDIUM, qr.HUMAN_REQUIRED)}


def test_act_001_inactive_new_entrant_needs_a_previous_delivery():
    ds = {"new_entrant_ids": {f"{ARC}.1"}}
    assert _types(qr._act_001(_ctx(ds, active="0"))) == {
        ("ACT-001", "INACTIVE_NEW_ENTRANT", qr.HIGH, qr.HUMAN_REQUIRED)}
    assert qr._act_001(_ctx(ds, active="1")) == []
    assert qr._act_001(_ctx({"new_entrant_ids": set()}, active="0")) == []   # baseline
    assert qr._act_001(_ctx({"new_entrant_ids": {f"{ARC}.9"}}, active="0")) == []


def test_sch_003_duplicate_id_is_critical():
    ds = {"source_id_duplicates": {f"{ARC}.1": 2}}
    assert _types(qr._sch_003(_ctx(ds))) == {
        ("SCH-003", "DUPLICATE_SOURCE_ID", qr.CRITICAL, qr.HUMAN_REQUIRED)}
    assert qr._sch_003(_ctx({"source_id_duplicates": {}})) == []


def test_int_002_resolves_against_the_registry():
    ds = {"known_source_ids": set(), "registry_oids": {f"{ARC}.77"}, "qhin_oids": {QHIN_OID}}
    assert {f.issue_type for f in qr._int_002(_ctx(ds, partOf=f"{ARC}.77"))} == {"PART_OF_RESOLVED_IN_REGISTRY"}
    assert {f.issue_type for f in qr._int_002(_ctx(ds, partOf=f"{ARC}.78"))} == {"PART_OF_UNRESOLVED"}


# ── end to end: July → September ─────────────────────────────────────────────

def _july_rows():
    rows = []
    def row(i, **over):
        r = base_row(**over)
        r["id"] = f"{ARC}.{i}" if isinstance(i, int) else i
        r["TEFCAID"] = f"{SYN}-{ARC}-T-{str(i)[-4:]}"
        r["HCID"] = f"urn:oid:{r['id']}"
        r["name"] = over.get("name") or f"{SYN} {ARC} ORG {i}"
        rows.append(r)
        return r
    row(P300, name=f"{SYN} KONZA-LIKE 300", sequoiaorgtype="Participant", partOf=QHIN_OID)
    row(P700, name=f"{SYN} KONZA-LIKE 700", sequoiaorgtype="Participant", partOf=QHIN_OID)
    row(f"{ARC}.9", name=f"{SYN} OTHER-QHIN PARTICIPANT", sequoiaorgtype="Participant",
        partOf=OTHER_QHIN, orgManagingOrg=OTHER_QHIN)
    for i in (1, 2, 3, 4):
        row(i, sequoiaorgtype="Subparticipant", partOf=P300)
    row(5, name=f"{SYN} PAYER", hl7orgrole="payer", NAIC="04918")
    row(6)  # non-material change in September (postal code)
    return rows


def _september_rows():
    rows = {r["id"]: dict(r) for r in _july_rows()}
    rows[f"{ARC}.1"]["partOf"] = P700                 # moved .300 → .700
    rows[f"{ARC}.2"]["partOf"] = P700                 # moved .300 → .700
    rows[f"{ARC}.3"]["partOf"] = f"{ARC}.9"           # cross-QHIN parent — must be refused
    del rows[f"{ARC}.4"]                              # absent in September
    rows[f"{ARC}.5"]["active"] = "0.0"                # float round-trip, inactive
    rows[f"{ARC}.6"]["address_postalCode"] = "02102"  # non-material
    new = base_row(sequoiaorgtype="Subparticipant", partOf=P700, active="0")
    new["id"] = f"{ARC}.7"; new["TEFCAID"] = f"{SYN}-{ARC}-T-0007"
    new["HCID"] = f"urn:oid:{new['id']}"; new["name"] = f"{SYN} {ARC} NEW INACTIVE 7"
    rows[new["id"]] = new
    return list(rows.values())


async def _entity_by_oid(db, oid):
    return (await db.execute(
        select(reg.TefcaEntityIdentifier.entity_id)
        .where(reg.TefcaEntityIdentifier.identifier_type == "rce_org_oid",
               reg.TefcaEntityIdentifier.identifier_value == oid))).scalar_one()


async def _active_parent(db, child_id, rel_type):
    return (await db.execute(
        select(reg.TefcaEntityRelationship)
        .where(reg.TefcaEntityRelationship.child_entity_id == child_id,
               reg.TefcaEntityRelationship.relationship_type == rel_type,
               reg.TefcaEntityRelationship.end_date.is_(None)))).scalars().all()


@pytest.mark.asyncio
async def test_july_then_september_supersedes_marks_and_persists(rolled_back_db):
    from app.tefca_registry.rce import snapshot_effects as se
    from app.tefca_registry.rce.promotion import REL_SUB_PARTICIPANT_OF, promote_delivery
    from app.tefca_registry.rce.relationship_history import history_for_entity

    db = rolled_back_db
    # Far past: `previous_delivery` is global (latest earlier intake), and the
    # shared test database may hold committed intakes from other suites.
    now = datetime(2001, 9, 2, 12, 0, 0)

    # ── July (baseline) ──
    july_id, _ = await seed_intake(db, _july_rows())
    july = await db.get(m.RceSourceIntake, july_id)
    july.received_at = now - timedelta(days=44)
    await db.commit()
    await run_quality_and_curation(db, july_id)
    jp = await promote_delivery(db, july_id, actor=SYN)
    assert jp["relationships_sub_participant_of"] == 4
    assert jp["relationships_superseded"] == 0
    july_effects = await se.apply_snapshot_effects(db, july_id, actor=SYN)
    assert july_effects["delta"]["state"] in ("BASELINE_DELIVERY", "COMPARED")
    assert july_effects["source_snapshot"]["status"] == sm.SNAPSHOT_APPROVED

    sub1 = await _entity_by_oid(db, f"{ARC}.1")
    sub3 = await _entity_by_oid(db, f"{ARC}.3")
    sub4 = await _entity_by_oid(db, f"{ARC}.4")
    p300 = await _entity_by_oid(db, P300)
    p700 = await _entity_by_oid(db, P700)
    old_edge = (await _active_parent(db, sub1, REL_SUB_PARTICIPANT_OF))[0]
    assert str(old_edge.parent_entity_id) == str(p300)

    # An ARC result on the moved Subparticipant, and one on the unchanged-ish org 6.
    org6 = await _entity_by_oid(db, f"{ARC}.6")
    db.add(reg.ReviewRecord(review_id=f"REV-9993-{uuid.uuid4().hex[:6].upper()}",
                            entity_id=sub1, verification_results={"source_intake_id": str(july_id)}))
    db.add(reg.ReviewRecord(review_id=f"REV-9992-{uuid.uuid4().hex[:6].upper()}",
                            entity_id=org6, verification_results={"source_intake_id": str(july_id)}))
    await db.commit()

    # ── September ──
    sept_id, _ = await seed_intake(db, _september_rows())
    sept = await db.get(m.RceSourceIntake, sept_id)
    sept.received_at = now
    await db.commit()
    boundary = now.date()

    quality, curated = await run_quality_and_curation(db, sept_id)
    issues = (await db.execute(
        select(m.RceIssue.rule_id, m.RceIssue.issue_type, m.RceSourceRecord.source_rce_id)
        .join(m.RceSourceRecord, m.RceSourceRecord.id == m.RceIssue.source_record_id)
        .where(m.RceSourceRecord.source_intake_id == sept_id))).all()
    by_oid = {}
    for rule_id, issue_type, oid in issues:
        by_oid.setdefault(oid, set()).add((rule_id, issue_type))
    assert ("ACT-001", "INACTIVE_NEW_ENTRANT") in by_oid[f"{ARC}.7"]
    assert ("CON-003", "ACTIVE_FORMAT_NORMALIZED") in by_oid[f"{ARC}.5"]
    assert ("CON-003", "INACTIVE_RECORD") in by_oid[f"{ARC}.5"]
    assert not any(r == "INT-002" and t == "PART_OF_UNRESOLVED" for r, t in by_oid.get(f"{ARC}.1", set()))
    assert not any(r == "SO-002" and t != "NAIC_ON_NON_PAYER" for r, t in by_oid.get(f"{ARC}.5", set()))
    assert curated["status_counts"]["HELD"] == 1   # only the inactive new entrant

    cur5 = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == sept_id,
        m.RceCuratedRecord.rce_org_oid == f"{ARC}.5"))).scalar_one()
    assert cur5.is_active is False and cur5.operational_status == "inactive"
    assert cur5.rce_attributes["active_raw"] == "0.0"
    assert cur5.rce_attributes["NAIC"] == "04918" and cur5.rce_attributes["NAIC_normalized"] == "04918"

    sp = await promote_delivery(db, sept_id, actor=SYN)
    assert sp["relationship_boundary"] == boundary.isoformat()
    assert sp["relationships_superseded"] == 2          # .1 and .2 moved
    assert sp["relationship_observations"]["cross_qhin_refused"] == 1   # .3
    assert sp["relationships_sub_participant_of"] == 2  # two new edges; .7 held, .3 refused

    # old edge ended at the boundary, status historical, otherwise untouched
    await db.refresh(old_edge)
    assert old_edge.end_date == boundary and old_edge.status == "historical"
    assert str(old_edge.parent_entity_id) == str(p300)
    current = await _active_parent(db, sub1, REL_SUB_PARTICIPANT_OF)
    assert len(current) == 1 and str(current[0].parent_entity_id) == str(p700)
    assert current[0].effective_date == boundary

    # cross-QHIN refused: .3 still under .300, nothing ended
    kept = await _active_parent(db, sub3, REL_SUB_PARTICIPANT_OF)
    assert len(kept) == 1 and str(kept[0].parent_entity_id) == str(p300)
    hist3 = await history_for_entity(db, sub3)
    assert {o["observation"] for o in hist3["observations"]} >= {"ASSERTED", "CROSS_QHIN_REFUSED"}
    hist1 = await history_for_entity(db, sub1)
    kinds = [o["observation"] for o in hist1["observations"]
             if o["relationship_type"] == REL_SUB_PARTICIPANT_OF]
    # July assertion, September supersession of it, September assertion.
    assert "SUPERSEDED" in kinds and kinds.count("ASSERTED") == 2
    asserted_new = [o for o in hist1["observations"] if o["observation"] == "ASSERTED"
                    and o["supersedes_relationship_id"]]
    assert asserted_new and asserted_new[0]["supersedes_relationship_id"] == str(old_edge.id)

    # ── snapshot effects ──
    eff = await se.apply_snapshot_effects(db, sept_id, actor=SYN)
    assert eff["id_guard"]["unique"] is True
    d = eff["delta"]
    assert d["state"] == "COMPARED" and d["previous_intake_id"] == str(july_id) or d["previous_intake_id"] == july_id
    assert d["counts"][sm.DELTA_NEW] == 1
    assert d["counts"][sm.DELTA_NOT_PRESENT] == 1
    assert d["counts"][sm.DELTA_CHANGED] == 5   # .1 .2 .3 .5 .6
    assert d["counts"][sm.DELTA_UNCHANGED] == 3 # .300 .700 .9
    assert d["material_changes"] == 4           # .6 is postal code only
    assert eff["presence"]["absent"] == 1 and eff["presence"]["present"] == 8
    assert eff["source_snapshot"]["status"] == sm.SNAPSHOT_APPROVED

    marks = (await db.execute(select(sm.ArcStaleMark)
                              .where(sm.ArcStaleMark.intake_id == sept_id))).scalars().all()
    by_entity = {}
    for mk in marks:
        by_entity.setdefault(str(mk.entity_id), []).append(mk)
    assert str(org6) not in by_entity                       # non-material: no mark
    assert {mk.reason for mk in by_entity[str(sub1)]} == {"PART_OF_CHANGED"}
    assert all(mk.review_id for mk in by_entity[str(sub1)])  # tied to the ARC result
    assert {mk.reason for mk in by_entity[str(sub4)]} == {"ABSENT_FROM_DELIVERY"}
    assert (await db.execute(select(reg.TefcaRegEntity.is_active)
                             .where(reg.TefcaRegEntity.id == sub4))).scalar_one() is True  # never deactivated by absence

    presence4 = (await db.execute(select(sm.RceEntityPresence).where(
        sm.RceEntityPresence.entity_id == sub4, sm.RceEntityPresence.intake_id == sept_id))).scalar_one()
    assert presence4.present is False
    presence5 = (await db.execute(select(sm.RceEntityPresence).where(
        sm.RceEntityPresence.rce_org_oid == f"{ARC}.5", sm.RceEntityPresence.intake_id == sept_id))).scalar_one()
    assert presence5.active_raw == "0.0" and presence5.active_normalized == "0"

    # idempotent re-application: nothing duplicated
    eff2 = await se.apply_snapshot_effects(db, sept_id, actor=SYN)
    assert eff2["delta"]["persisted"] == 0 and eff2["delta"]["already"] == 10
    assert eff2["presence"]["present"] == 0 and eff2["presence"]["absent"] == 0
    assert eff2["stale"]["marked"] == 0
    assert eff2["source_snapshot"]["already"] is True

    # resolve one mark: append-only, the STALE row remains
    mark = by_entity[str(sub1)][0]
    res = await se.resolve_stale(db, mark.id, actor=SYN, reason="re-evaluated")
    live = await se.stale_for_entities(db, [sub1])
    assert live.get(str(sub1), []) == []
    assert await db.get(sm.ArcStaleMark, mark.id) is not None
    assert (await db.get(sm.ArcStaleMark, uuid.UUID(res["resolution_id"]))).kind == "RESOLVED"

    # historical July delivery untouched: still 10 curated rows, same shas
    stored = (await db.execute(select(m.RceSourceRecord.record_sha256)
                               .where(m.RceSourceRecord.source_intake_id == july_id))).scalars().all()
    assert len(stored) == 9


@pytest.mark.asyncio
async def test_older_snapshot_cannot_end_a_newer_edge(rolled_back_db):
    """A file received BEFORE the current edge's effective date is refused
    (SNAPSHOT_MISMATCH): the registry is never rewound by a late backfill."""
    from app.tefca_registry.rce.promotion import REL_SUB_PARTICIPANT_OF, promote_delivery

    db = rolled_back_db
    now = datetime(2001, 9, 2, 12, 0, 0)
    cur_id, _ = await seed_intake(db, _july_rows())
    cur = await db.get(m.RceSourceIntake, cur_id)
    cur.received_at = now
    await db.commit()
    await run_quality_and_curation(db, cur_id)
    await promote_delivery(db, cur_id, actor=SYN)
    sub1 = await _entity_by_oid(db, f"{ARC}.1")
    p300 = await _entity_by_oid(db, P300)

    late_rows = [dict(r) for r in _july_rows()]
    for r in late_rows:
        if r["id"] == f"{ARC}.1":
            r["partOf"] = P700
    late_id, _ = await seed_intake(db, late_rows)
    late = await db.get(m.RceSourceIntake, late_id)
    late.received_at = now - timedelta(days=10)
    await db.commit()
    await run_quality_and_curation(db, late_id)
    lp = await promote_delivery(db, late_id, actor=SYN)
    assert lp["relationship_observations"]["snapshot_mismatch"] >= 1
    assert lp["relationships_superseded"] == 0
    edges = await _active_parent(db, sub1, REL_SUB_PARTICIPANT_OF)
    assert len(edges) == 1 and str(edges[0].parent_entity_id) == str(p300)
    assert edges[0].end_date is None


@pytest.mark.asyncio
async def test_duplicate_id_refuses_snapshot_effects(rolled_back_db):
    from app.tefca_registry.rce import snapshot_effects as se

    db = rolled_back_db
    rows = make_rows(3, arc=f"{ARC}.dup")
    rows[2]["id"] = rows[1]["id"]
    intake_id, _ = await seed_intake(db, rows)
    with pytest.raises(se.SnapshotRefused):
        await se.assert_ids_unique(db, intake_id)
    quality, curated = await run_quality_and_curation(db, intake_id)
    dup = (await db.execute(
        select(m.RceIssue).join(m.RceSourceRecord, m.RceSourceRecord.id == m.RceIssue.source_record_id)
        .where(m.RceSourceRecord.source_intake_id == intake_id,
               m.RceIssue.rule_id == "SCH-003"))).scalars().all()
    assert len(dup) == 2 and all(i.severity == "CRITICAL" for i in dup)
    assert curated["status_counts"]["HELD"] >= 2
