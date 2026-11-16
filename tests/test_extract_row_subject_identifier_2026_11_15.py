"""CodeRabbit on #233 (3rd review, reproduced) - ONE common cause for two findings.

The SAM public-extract D3 item was the only evidence item that did not record WHAT it was queried by
(`query_identifier`), although the extract screen's query parameters (npi / org) were known. Consequences:
  * the single-record selection (`persisted_evidence_is_for_record`) can never accept it (a null identifier is never
    "keyed by an identifier this record carries"), so a record-specific review never used the extract state;
  * the bulk path's name-screen withholding is prefix based (`org=` / `legal_name=`) and silently skipped the
    extract's name-keyed result;
  * a name-keyed persisted row replacing a live source dropped the name-only marker (coverage_note counted a
    name screen as verified).
The writer now records the subject in the bulk path's own vocabulary (`npi=` / `org=`). Synthetic data only; these tests go
through the REAL writer and the REAL predicates, not a mocked selection."""
from types import SimpleNamespace

import pytest

from app.Tefca.connectors import SourceResult
from app.Tefca.evidence_assembly import sam_extract_evidence_items
from app.tefca_registry import review_service as rs
from app.tefca_registry.rce import arc_pipeline as ap

pytestmark = pytest.mark.regression
NPI = "1234567893"
OTHER_NPI = "1245319599"
NAME = "Synthetic Test Org"


def _result(npi=None, org=None, candidate=False, ok=True):
    qp = {"npi": npi, "org": org}
    if not ok:
        return SourceResult.unavailable("SAM_EXTRACT", "stale_source: edition stale", qp, "v")
    return SourceResult.ok("SAM_EXTRACT", {"candidate_match": candidate, "candidate_count": int(candidate)}, qp, "v",
                           raw_for_hash={"m": int(candidate)})


def _item(**kw):
    items, _hit = sam_extract_evidence_items("EXCLUSION_REVOCATION", _result(**kw))
    assert len(items) == 1
    return items[0]


@pytest.mark.parametrize("kw, expected", [
    (dict(npi=NPI), f"npi={NPI}"),
    (dict(org=NAME), f"org={NAME}"),
    (dict(npi=NPI, org=NAME), f"npi={NPI}"),        # NPI is the identity; the name leg is never claimed as the subject
    (dict(npi=NPI, candidate=True), f"npi={NPI}"),
    (dict(npi=NPI, ok=False), f"npi={NPI}"),         # an unavailable attempt keeps its subject too
])
def test_the_extract_item_records_what_it_was_queried_by(kw, expected):
    assert _item(**kw).query_identifier == expected


def test_no_subject_means_no_identifier_and_no_record_accepts_it():
    item = _item()
    assert item.query_identifier in (None, "")
    assert not rs.persisted_evidence_is_for_record(
        "SAM_EXTRACT_SUPPLEMENTARY", item.query_identifier, submitted_npi=NPI,
        record_identity=rs.record_name_identity(NAME), names_usable=True)


def test_the_real_selection_predicate_accepts_only_the_records_own_subject():
    npi_item = _item(npi=NPI)
    name_item = _item(org=NAME)
    ident = rs.record_name_identity(NAME)
    ok = lambda ident_value, **kw: rs.persisted_evidence_is_for_record("SAM_EXTRACT_SUPPLEMENTARY", ident_value, **kw)
    assert ok(npi_item.query_identifier, submitted_npi=NPI, record_identity=ident, names_usable=True)
    assert not ok(npi_item.query_identifier, submitted_npi=OTHER_NPI, record_identity=ident, names_usable=True)
    assert ok(name_item.query_identifier, submitted_npi="", record_identity=ident, names_usable=True)
    assert not ok(name_item.query_identifier, submitted_npi="", record_identity=rs.record_name_identity("Another Org"), names_usable=True)
    assert not ok(name_item.query_identifier, submitted_npi="", record_identity=ident, names_usable=False)


def _evidence_with(item):
    return {"dimensions": [{"dimension": "EXCLUSION_REVOCATION", "disposition": "PASS",
                            "evidence": [item.to_dict() if hasattr(item, "to_dict") else item.__dict__]}]}


def test_the_bulk_withholding_now_covers_a_name_keyed_extract_result():
    name_item, npi_item = _item(org=NAME), _item(npi=NPI)
    out, withheld = ap.withhold_unestablished_name_screens(
        _evidence_with(name_item), delivered_name="A Different Delivered Name", entity_name=NAME)
    assert [w["source"] for w in withheld] == ["SAM_EXTRACT_SUPPLEMENTARY"] and out["dimensions"][0]["evidence"] == []
    kept, withheld2 = ap.withhold_unestablished_name_screens(
        _evidence_with(npi_item), delivered_name="A Different Delivered Name", entity_name=NAME)
    assert withheld2 == [] and len(kept["dimensions"][0]["evidence"]) == 1       # NPI-keyed evidence untouched


class _Row(SimpleNamespace):
    pass


def _row(identifier, disposition="NOT_FOUND"):
    return _Row(source="OIG_LEIE", disposition=disposition, generation_timestamp="2026-11-06T00:00:00", id="r1",
                review_id=None, rule_applied="X", normalized_values=None, query_identifier=identifier)


def test_a_name_keyed_persisted_row_keeps_the_name_only_marker_so_it_is_never_counted_as_verified():
    sources = {"oig_leie": {"status": "unavailable"}}
    rs.apply_persisted_exclusion_evidence(sources, {"OIG_LEIE": _row("org=Synthetic Test Org")})
    assert sources["oig_leie"]["status"] == "clear"
    assert sources["oig_leie"].get("matched_by") == rs.MATCHED_BY_ORG_NAME
    note = rs.coverage_note({"oig_leie": sources["oig_leie"]})
    assert "oig_leie" not in " ".join(note.get("sources_verified") or []) if isinstance(note.get("sources_verified"), list) \
        else "oig_leie" not in str(note.get("sources_verified"))


def test_an_npi_keyed_persisted_row_does_not_get_the_name_only_marker():
    sources = {"oig_leie": {"status": "unavailable", "matched_by": rs.MATCHED_BY_ORG_NAME}}
    rs.apply_persisted_exclusion_evidence(sources, {"OIG_LEIE": _row(f"npi={NPI}")})
    assert sources["oig_leie"].get("matched_by") != rs.MATCHED_BY_ORG_NAME     # a stale live marker is not copied over


# ── through the database: the real selection (`latest_persisted_extract_evidence`) with the record-identity rule ──
from datetime import datetime  # noqa: E402

from rce_traceability_support import rolled_back_db, seed_entity  # noqa: E402,F401
from app.Tefca.models import TEFCADimensionEvidence as DE  # noqa: E402


async def test_a_record_specific_review_selects_the_extract_row_keyed_by_its_own_subject_only(rolled_back_db):
    db = rolled_back_db
    ent = await seed_entity(db, oid="9.99.777.58.ENT", name="Synthetic Extract Org")
    for ident, stamp in ((_item(npi=NPI).query_identifier, "2026-11-06T00:00:01"),
                         (_item(npi=OTHER_NPI).query_identifier, "2026-11-06T00:00:02"),     # newer, another record's
                         (None, "2026-11-06T00:00:03")):                                    # newest, no subject
        db.add(DE(id=__import__("uuid").uuid4(), entity_id=str(ent), evidence_dimension="EXCLUSION_REVOCATION",
                  source="SAM_EXTRACT_SUPPLEMENTARY", disposition="PASS", original_values={}, normalized_values={},
                  query_identifier=ident, retrieved_at="2026-11-06", generation_timestamp=stamp, created_at=datetime.utcnow()))
    await db.flush()

    def accept_for(npi):
        return lambda src, row: rs.persisted_evidence_is_for_record(
            src, row.query_identifier, submitted_npi=npi, record_identity="", names_usable=False)

    mine = await rs.latest_persisted_extract_evidence(db, ent, accept=accept_for(NPI))
    assert mine is not None and mine.query_identifier == f"npi={NPI}"            # not hidden by the newer foreign/null rows
    assert await rs.latest_persisted_extract_evidence(db, ent, accept=accept_for("")) is None
