"""`GET /api/tefca/workflow/deliveries/{intake_id}/review-cycle/identity-precheck` (2026-11-25).

Owner: "Establish a verified way to ingest the delivery and count identity refusals before classification starts."

The route is a READ-ONLY prediction of what the review cycle's own identity pre-check would decide for every member
of the frame `finalize_plan` uses, before any cycle exists. Proved here, on one synthetic delivery that contains one
member for every outcome:

  * it writes nothing (the row count of every table is unchanged) and asks no source (gatherer and evidence builder
    are never awaited; the whole file also runs with outbound network refused);
  * what it predicts is what a REAL cycle call then reports for the same delivery: same members set aside, same
    reasons, same kinds, same number reviewed;
  * paging covers every member exactly once and the pages add up;
  * HELD records (outside the frame) and members that are currently HELD are reported separately;
  * the submitted-NPI refusals are split into "record delivered no NPI" and "record delivered a different NPI";
  * no NPI value appears anywhere in the response;
  * the role is program_manager; a resolver other than the registry one is refused, never reported as all-clear.

Synthetic data only.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.security import ROLE_HIERARCHY
from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from app.tefca_registry import workflow_routes
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import models as m
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_intake  # noqa: F401
from test_review_lineage_writers_db_2026_11_12 import _curate, _entity, _records, _run

pytestmark = pytest.mark.asyncio

NPI = "1234567893"
OTHER_NPI = "1245319599"
NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
ROUTE = "/api/tefca/workflow/deliveries/{intake_id}/review-cycle/identity-precheck"
_USER = SimpleNamespace(id=None, email="precheck@synthetic-test.docuaction.invalid", role="program_manager")

# One member per outcome. `delivered` is what the record submitted; `registry` the NPI the registry entity carries.
CASES = [
    # name                  delivered  registry   expected reason (None = reviewable)
    ("reviewable",          NPI,       NPI,       None),
    ("blank_submitted",     "",        NPI,       ap.REFUSAL_SUBMITTED_NPI_DIFFERS),
    ("different_submitted", NPI,       OTHER_NPI, ap.REFUSAL_SUBMITTED_NPI_DIFFERS),
    ("resolves_elsewhere",  NPI,       NPI,       rc.NOT_REVIEWABLE_OTHER_ENTITY),
    ("does_not_resolve",    NPI,       NPI,       rc.NOT_REVIEWABLE_UNRESOLVED),
    ("no_reference",        NPI,       NPI,       rc.NOT_REVIEWABLE_NO_REFERENCE),
    ("rce_block_npi",       NPI,       NPI,       ap.REFUSAL_ENROLLMENT_PROBE_NPI),
    ("also_held",           NPI,       NPI,       None),          # a member with a second, HELD record
    ("blank_both",          "",        "",        None),          # no NPI anywhere: nothing to disagree about
]
KIND = {rc.NOT_REVIEWABLE_UNRESOLVED: rc.KIND_TRANSIENT}


class _Delivery:
    """A synthetic delivery whose members cover every pre-check outcome, with the two edges a cycle has replaced:
    the resolver (`resolves_to` / `registry_npi` / `rce_npi`) and the source gatherer (`gather`, never real)."""

    async def build(self, db, monkeypatch, arc="9.99.779.1"):
        self.db = db
        monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
        rows = make_rows(len(CASES) + 2, arc=arc)               # + the HELD-only record, + also_held's HELD record
        for row, (_name, delivered, _registry, _reason) in zip(rows, CASES):
            row["NPI"] = delivered
        self.intake_id, _ = await seed_intake(db, rows, with_job=False)
        await _run(db, self.intake_id)
        recs = await _records(db, self.intake_id)

        qhin = uuid.uuid4()
        db.add(reg.TefcaRegEntity(id=qhin, name=f"{SYN} QHIN {arc}", display_name=f"{SYN} QHIN",
                                  entity_level="qhin", entity_type="health_information_network",
                                  operational_status="active", verification_status="not_verified",
                                  current_version=1, is_active=True, is_test_record=False, rce_attributes={}))
        await db.flush()

        async def _member(tag):
            ent = await _entity(db, tag)
            db.add(reg.TefcaEntityRelationship(
                id=uuid.uuid4(), parent_entity_id=qhin, child_entity_id=ent, relationship_type="managed_by_qhin",
                status="active", source="import", effective_date=date(2026, 11, 1)))
            return ent

        self.entity, self.ref, self.resolves_to, self.registry_npi, self.rce_npi = {}, {}, {}, {}, {}
        for index, (name, _delivered, registry, _reason) in enumerate(CASES, start=1):
            ent = await _member(name)
            await _curate(db, self.intake_id, recs[index], ent)
            ref = rows[index - 1]["id"]
            self.entity[name], self.ref[name] = ent, ref
            self.resolves_to[ref], self.registry_npi[ref] = ent, registry
        # the member with no delivered reference
        curated = (await db.execute(select(m.RceCuratedRecord).where(
            m.RceCuratedRecord.source_intake_id == self.intake_id,
            m.RceCuratedRecord.canonical_entity_id == self.entity["no_reference"]))).scalars().one()
        curated.rce_org_oid = None
        self.ref["no_reference"] = None
        # the reference that resolves to another entity / to nothing / with a different RCE-block NPI
        self.elsewhere = await _entity(db, "elsewhere")
        self.resolves_to[self.ref["resolves_elsewhere"]] = self.elsewhere
        self.resolves_to[self.ref["does_not_resolve"]] = None
        self.rce_npi[self.ref["rce_block_npi"]] = OTHER_NPI
        # a HELD-only record (outside the frame) and a second, HELD record of the member `also_held`
        self.held_only = await _member("held_only")
        await _curate(db, self.intake_id, recs[len(CASES) + 1], self.held_only, status="HELD")
        await _curate(db, self.intake_id, recs[len(CASES) + 2], self.entity["also_held"], status="HELD")
        for extra in (rows[len(CASES)]["id"], rows[len(CASES) + 1]["id"]):
            self.resolves_to[extra] = (self.held_only if extra == rows[len(CASES)]["id"]
                                       else self.entity["also_held"])
            self.registry_npi[extra] = ""
        await db.flush()

        def _shape(ref):
            ent = self.resolves_to.get(ref)
            if ent is None:
                return None
            npi = self.registry_npi.get(ref)
            return {"id": str(ent), "name": f"entity {ref}", "_registry_entity_id": ent,
                    "_rce": ({"NPI": self.rce_npi[ref]} if ref in self.rce_npi else {}),
                    "identifier": ([{"system": NPI_SYSTEM, "value": npi}] if npi else []),
                    "_resolution_source": "test"}

        async def _resolve(_db, ref):
            return _shape(ref)

        async def _gather(refs, timing_log=None):
            # Like the real gatherer: evidence is built for the entity the writer resolved and gated (which is the
            # no-NPI view for a record classified on its submitted values), not for a second resolution.
            pre = ap._PRE_RESOLVED.get() or {}
            out = []
            for ref in refs:
                entity = pre[ref] if ref in pre else _shape(ref)
                out.append({"ref": ref, "entity": entity, "evidence": ({
                    "generated_at": "2026-11-25T12:00:00Z", "applicability": {}, "sufficiency": {},
                    "data_quality_flags": [], "dimensions": []} if entity else None)})
            return out

        self.gather = AsyncMock(side_effect=_gather)
        monkeypatch.setattr("app.Tefca.entity_resolution.resolve_entity", _resolve)
        monkeypatch.setattr(ap, "_gather_all_evidence", self.gather)
        self.members = sorted(self.entity.values())
        return self

    def expected(self):
        """{entity id: (reason, kind)} for every member the pre-check should not call reviewable."""
        return {str(self.entity[name]): (reason, KIND.get(reason, rc.KIND_IDENTITY))
                for name, _d, _r, reason in CASES if reason}


async def _table_counts(db):
    tables = (await db.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))).scalars().all()
    return {t: (await db.execute(text(f'SELECT count(*) FROM "{t}"'))).scalar() for t in tables}


async def _page(db, delivery, **kw):
    return await rc.preview_identity_precheck(db, delivery.intake_id, **kw)


async def test_the_precheck_writes_nothing_and_asks_no_source(rolled_back_db, monkeypatch):
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    build = AsyncMock()
    before = await _table_counts(db)
    # Every table of the schema is being watched - among them the ones a cycle or a verification writes.
    assert {"review_samples", "sample_entities", "review_cycles", "review_records", "tefca_dimension_evidence",
            "tefca_verifications", "tefca_reg_audit_log", "audit_logs", "rce_issues",
            "tefca_reg_entities", "tefca_entity_identifiers"} <= set(before), sorted(before)
    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build), \
            patch.object(db, "commit", new=AsyncMock(side_effect=AssertionError("the pre-check committed"))), \
            patch.object(db, "flush", new=AsyncMock(side_effect=AssertionError("the pre-check flushed"))):
        result = await _page(db, delivery)
        again = await _page(db, delivery, limit=2)
    assert not db.new and not db.dirty and not db.deleted      # nothing even pending
    assert await _table_counts(db) == before                   # no plan, sample, review, evidence or audit row
    delivery.gather.assert_not_awaited()
    build.assert_not_awaited()
    assert result["read_only"] is True and "PREDICTION" in result["note"] and again["page"]["examined"] == 2


async def test_the_counts_reasons_and_split_are_right_and_no_npi_value_is_returned(rolled_back_db, monkeypatch):
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    result = await _page(db, delivery)

    assert result["frame"]["members_total"] == len(CASES) and result["page"]["complete"] is True
    assert result["page"]["examined"] == len(CASES) and result["page"]["next_after_entity_id"] is None
    counts = result["counts"]
    assert (counts["reviewable"], counts["not_reviewable"], counts["reference_not_resolved"]) == (3, 5, 1)
    assert {(r["reason"], r["kind"]): r["count"] for r in counts["by_reason"]} == {
        (ap.REFUSAL_SUBMITTED_NPI_DIFFERS, rc.KIND_IDENTITY): 2,
        (ap.REFUSAL_ENROLLMENT_PROBE_NPI, rc.KIND_IDENTITY): 1,
        (rc.NOT_REVIEWABLE_OTHER_ENTITY, rc.KIND_IDENTITY): 1,
        (rc.NOT_REVIEWABLE_NO_REFERENCE, rc.KIND_IDENTITY): 1,
        (rc.NOT_REVIEWABLE_UNRESOLVED, rc.KIND_TRANSIENT): 1}
    # The split the owner asked for: a record that delivered NO NPI vs one that delivered a different NPI.
    assert counts["submitted_npi_gate"] == {"submitted_npi_blank": 1, "submitted_npi_present_and_different": 1}
    listed = {n["entity_id"]: (n["reason"], n["kind"]) for n in result["not_reviewable"]}
    assert listed == delivery.expected()
    by_id = {n["entity_id"]: n for n in result["not_reviewable"]}
    assert by_id[str(delivery.entity["no_reference"])]["entity_ref"] is None
    assert by_id[str(delivery.entity["blank_submitted"])]["entity_ref"] == delivery.ref["blank_submitted"]
    assert result["not_reviewable_total"] == 6 and result["not_reviewable_truncated"] is False
    # A record with no NPI on an entity with no NPI is NOT refused: there is nothing to disagree about.
    assert str(delivery.entity["blank_both"]) not in listed
    body = json.dumps(result)
    assert NPI not in body and OTHER_NPI not in body           # never a delivered or registry NPI value


async def test_held_records_and_held_members_are_reported_separately(rolled_back_db, monkeypatch):
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    monkeypatch.delenv(rc.PROPOSED_VERIFY_SKIP_HELD_ENV, raising=False)
    result = await _page(db, delivery)
    frame = result["frame"]
    # Two HELD curated records exist. Neither is a member: HELD is outside the frame (include_held=False).
    assert frame["held_records_excluded_from_frame"] == 2 and frame["not_in_frame_total"] == 2
    assert str(delivery.held_only) not in {n["entity_id"] for n in result["not_reviewable"]}
    assert delivery.held_only not in delivery.members
    # One MEMBER also has a HELD record: counted, still pre-checked, and flagged as what the D-15 flag would skip.
    assert frame["members_currently_held"] == 1 and result["counts"]["members_currently_held"] == 1
    assert frame["verify_skip_held_flag_on"] is False
    monkeypatch.setenv(rc.PROPOSED_VERIFY_SKIP_HELD_ENV, "true")
    assert (await _page(db, delivery))["frame"]["verify_skip_held_flag_on"] is True


async def test_paging_covers_every_member_exactly_once_and_the_pages_add_up(rolled_back_db, monkeypatch):
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    whole = await _page(db, delivery)
    pages, after = [], None
    while True:
        page = await _page(db, delivery, after_entity_id=after, limit=2)
        pages.append(page)
        assert page["frame"] == whole["frame"]                 # the delivery-wide summary is on every page
        if page["page"]["complete"]:
            break
        after = page["page"]["next_after_entity_id"]
        assert after and len(pages) < 20
    assert [p["page"]["examined"] for p in pages] == [2, 2, 2, 2, 1]
    assert sum(p["page"]["examined"] for p in pages) == whole["frame"]["members_total"] == len(CASES)
    listed = [n["entity_id"] for p in pages for n in p["not_reviewable"]]
    assert len(listed) == len(set(listed)) and set(listed) == set(delivery.expected())
    for key in ("reviewable", "not_reviewable", "reference_not_resolved", "members_currently_held"):
        assert sum(p["counts"][key] for p in pages) == whole["counts"][key], key
    for key in ("submitted_npi_blank", "submitted_npi_present_and_different"):
        assert sum(p["counts"]["submitted_npi_gate"][key] for p in pages) == 1
    assert pages[0]["page"]["members_after_this_page"] == len(CASES) - 2
    clamped = await _page(db, delivery, limit=10 ** 6)
    assert clamped["page"]["limit"] == rc.PRECHECK_MAX_LIMIT


async def test_what_the_precheck_predicts_is_what_a_real_cycle_then_reports(rolled_back_db, monkeypatch):
    """The comparison that makes the pre-check worth having: run it, then create the REAL full-population review
    cycle for the same delivery (real plan, real members, real walk, real writer; only reconciliation, the
    processing barrier and the source gatherer are replaced). Same members set aside, same reasons, same kinds; and
    the members the pre-check called reviewable are exactly the ones the cycle reviewed."""
    import app.tefca_registry.processing_controls as pc
    import app.tefca_registry.rce.reconciliation as rec
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    predicted = await _page(db, delivery)

    async def _enter(_db):
        return None

    async def _reconciled(_db, _intake):
        return {"passed": True, "checks": []}
    monkeypatch.setattr(pc, "enter_processing", _enter)
    monkeypatch.setattr(rec, "reconcile_delivery", _reconciled)
    cycle = await rc._create_review_cycle(db, delivery.intake_id, user=_USER, full_population=True, batch=100)

    assert cycle["members"] == predicted["frame"]["members_total"] == len(CASES)
    actual = {n["entity_id"]: (n["reason"], n["kind"]) for n in cycle["not_reviewable_for_identity"]}
    assert actual == {n["entity_id"]: (n["reason"], n["kind"]) for n in predicted["not_reviewable"]}
    assert actual == delivery.expected()
    assert cycle["linked_this_call"] == predicted["counts"]["reviewable"] == 3
    assert cycle["not_reviewable_identity_count"] == predicted["counts"]["not_reviewable"] == 5
    assert cycle["not_reviewable_transient_count"] == predicted["counts"]["reference_not_resolved"] == 1
    assert cycle["remaining"] == 6 and cycle["complete"] is False
    # The sources were asked only about what the pre-check called reviewable (plus the reference that does not
    # resolve, which only the writer can find out and for which no evidence exists to gather).
    asked = [ref for call in delivery.gather.await_args_list for ref in call.args[0]]
    assert sorted(asked) == sorted(delivery.ref[name] for name in
                                   ("reviewable", "also_held", "blank_both", "does_not_resolve"))


async def test_a_resolver_other_than_the_registry_one_is_refused_not_reported_as_all_clear(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    for source in ("mock", "db_then_mock", ""):
        if source:
            monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", source)
        else:
            monkeypatch.delenv("ENTITY_RESOLVER_SOURCE", raising=False)
        with pytest.raises(rc.IdentityPrecheckUnavailable):
            await _page(db, delivery)
        with pytest.raises(HTTPException) as refused:
            await workflow_routes.review_cycle_identity_precheck_route(
                delivery.intake_id, after_entity_id=None, limit=2000, db=db, user=_USER)
        assert refused.value.status_code == 409 and "ENTITY_RESOLVER_SOURCE=db" in refused.value.detail


async def test_the_route_is_program_manager_only_read_only_and_answers_through_the_function(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    route = next(r for r in workflow_routes.router.routes if r.path == ROUTE)
    assert route.methods == {"GET"}

    def _deps(dependant):
        for dep in dependant.dependencies:
            yield dep.call
            yield from _deps(dep)
    floors = [ROLE_HIERARCHY[fn.minimum_role] for fn in _deps(route.dependant) if getattr(fn, "minimum_role", None)]
    assert floors and max(floors) == ROLE_HIERARCHY["program_manager"]
    post = next(r for r in workflow_routes.router.routes
                if r.path == ROUTE.rsplit("/", 1)[0] and "POST" in r.methods)
    post_floors = [ROLE_HIERARCHY[fn.minimum_role] for fn in _deps(post.dependant)
                   if getattr(fn, "minimum_role", None)]
    assert max(post_floors) == max(floors)                     # the role of the act it previews

    out = await workflow_routes.review_cycle_identity_precheck_route(
        delivery.intake_id, after_entity_id=None, limit=2000, db=db, user=_USER)
    assert out["counts"]["not_reviewable"] == 5 and out["read_only"] is True
    with pytest.raises(HTTPException) as missing:
        await workflow_routes.review_cycle_identity_precheck_route(
            uuid.uuid4(), after_entity_id=None, limit=2000, db=db, user=_USER)
    assert missing.value.status_code == 404


async def test_the_cycles_own_precheck_is_the_function_the_preview_calls(rolled_back_db, monkeypatch):
    """`_identity_precheck` (what the cycle calls) returns `_identity_precheck_detail`'s reason: one decision, two
    callers. Checked for every case of the synthetic delivery."""
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    intake = delivery.intake_id
    for name, _delivered, _registry, reason in CASES:
        ref = delivery.ref[name]
        if ref is None:
            continue
        detail = await rc._identity_precheck_detail(db, intake, delivery.entity[name], ref)
        assert await rc._identity_precheck(db, intake, delivery.entity[name], ref) == detail["reason"]
        assert detail["reason"] == (None if reason == rc.NOT_REVIEWABLE_UNRESOLVED else reason), name
        assert detail["resolved"] is (name != "does_not_resolve")
    blank = await rc._identity_precheck_detail(db, intake, delivery.entity["blank_submitted"],
                                               delivery.ref["blank_submitted"])
    differs = await rc._identity_precheck_detail(db, intake, delivery.entity["different_submitted"],
                                                 delivery.ref["different_submitted"])
    assert (blank["submitted_npi_blank"], differs["submitted_npi_blank"]) == (True, False)


# ═══ the blank-NPI rule in the pre-check (2026-11-25, after the owner decision) ══════════════════════════════════════
#
# Every test above runs on a delivery whose assessment date is pinned at 2026-11-08 (`_run`), i.e. BEFORE
# `arc_pipeline.BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE`: the record that submitted no NPI is refused there, exactly as
# before the decision. The tests below move the same delivery to the effective date.

async def _assess_on(db, delivery, day):
    run = (await db.execute(select(m.RceIngestionRun).where(
        m.RceIngestionRun.source_intake_id == delivery.intake_id))).scalars().first()
    run.assessment_metadata = {"assessment_date": day, "source": "pinned_param"}
    await db.flush()


async def test_before_the_effective_date_the_precheck_says_blank_npi_records_will_be_refused(
        rolled_back_db, monkeypatch):
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)                 # assessed 2026-11-08
    result = await _page(db, delivery)
    rule = result["blank_npi_rule"]
    assert rule["effective_date"] == ap.BLANK_NPI_SUBMITTED_VALUES_EFFECTIVE.isoformat() == "2026-11-09"
    assert rule["delivery_assessment_date"] == "2026-11-08" and rule["applies_to_this_delivery"] is False
    assert "refused as before" in rule["effect"]
    assert result["counts"]["classified_on_submitted_values"] == 0 and result["classified_on_submitted_values"] == []
    assert result["counts"]["submitted_npi_gate"]["submitted_npi_blank"] == 1
    assert str(delivery.entity["blank_submitted"]) in {n["entity_id"] for n in result["not_reviewable"]}


async def test_from_the_effective_date_the_precheck_says_they_will_be_classified_on_submitted_values(
        rolled_back_db, monkeypatch):
    """The same delivery assessed on the effective date: the record that submitted no NPI is no longer refused. It
    is counted as reviewable AND named as one that will be classified on its submitted values; the record that
    submitted a DIFFERENT NPI is still refused. A real cycle then does exactly that."""
    import app.tefca_registry.processing_controls as pc
    import app.tefca_registry.rce.reconciliation as rec
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    await _assess_on(db, delivery, "2026-11-09")
    predicted = await _page(db, delivery)

    rule = predicted["blank_npi_rule"]
    assert rule["delivery_assessment_date"] == "2026-11-09" and rule["applies_to_this_delivery"] is True
    assert "classified on their submitted values" in rule["effect"]
    counts = predicted["counts"]
    assert (counts["reviewable"], counts["not_reviewable"], counts["reference_not_resolved"]) == (4, 4, 1)
    assert counts["classified_on_submitted_values"] == 1
    assert predicted["classified_on_submitted_values"] == [{
        "entity_id": str(delivery.entity["blank_submitted"]), "entity_ref": delivery.ref["blank_submitted"],
        "currently_held": False}]
    assert predicted["classified_on_submitted_values_truncated"] is False
    assert counts["submitted_npi_gate"] == {"submitted_npi_blank": 0, "submitted_npi_present_and_different": 1}
    refused = {n["entity_id"]: n["reason"] for n in predicted["not_reviewable"]}
    assert str(delivery.entity["blank_submitted"]) not in refused
    assert refused[str(delivery.entity["different_submitted"])] == ap.REFUSAL_SUBMITTED_NPI_DIFFERS
    body = json.dumps(predicted)
    assert NPI not in body and OTHER_NPI not in body                    # still never an NPI value

    async def _enter(_db):
        return None

    async def _reconciled(_db, _intake):
        return {"passed": True, "checks": []}
    monkeypatch.setattr(pc, "enter_processing", _enter)
    monkeypatch.setattr(rec, "reconcile_delivery", _reconciled)
    cycle = await rc._create_review_cycle(db, delivery.intake_id, user=_USER, full_population=True, batch=100)
    assert cycle["linked_this_call"] == counts["reviewable"] == 4
    assert {n["entity_id"]: n["reason"] for n in cycle["not_reviewable_for_identity"]} == refused
    verification = cycle["verification"]
    assert verification["classified_on_submitted_values_count"] == counts["classified_on_submitted_values"] == 1
    assert verification["classified_on_submitted_values"] == [delivery.ref["blank_submitted"]]


async def test_frame_consistency_and_the_held_split_are_reported(rolled_back_db, monkeypatch):
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    result = await _page(db, delivery)
    frame = result["frame"]
    assert frame["frame_consistent"] is True and frame["eligible_units"] == frame["members_total"]
    counts = result["counts"]
    assert (counts["held_and_reviewable"], counts["held_and_not_reviewable"]) == (1, 0)
    assert counts["held_and_reviewable"] + counts["held_and_not_reviewable"] == counts["members_currently_held"]

    # A second ELIGIBLE record of one member (the same entity twice in the frame): the frame is not consistent.
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == delivery.intake_id))).scalars().all()
    assert recs
    held_row = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == delivery.intake_id,
        m.RceCuratedRecord.canonical_entity_id == delivery.entity["also_held"],
        m.RceCuratedRecord.record_status == "HELD"))).scalars().one()
    held_row.record_status = "CLEAN"                                    # the member's HELD record is released
    await db.flush()
    again = await _page(db, delivery)
    assert again["frame"]["eligible_units"] == again["frame"]["members_total"] + 1
    assert again["frame"]["frame_consistent"] is False


# ═══ an entity with more than one record in the delivery (delta review of a6a257c) ═══════════════════════════════════

async def _two_blank_records_of_one_entity(db, delivery):
    """Release the HELD record of the member `also_held`, and make BOTH of that entity's records blank-NPI records
    on an entity the registry holds an NPI for. Returns (entity, lower reference, higher reference)."""
    entity = delivery.entity["also_held"]
    rows = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == delivery.intake_id,
        m.RceCuratedRecord.canonical_entity_id == entity)
        .order_by(m.RceCuratedRecord.rce_org_oid))).scalars().all()
    assert len(rows) == 2
    for row in rows:
        row.record_status = "CLEAN"
        record = await db.get(m.RceSourceRecord, row.source_record_id)
        record.parsed = {**(record.parsed or {}), "NPI": ""}
        record.npi = None
        delivery.registry_npi[row.rce_org_oid] = NPI
    await db.flush()
    return entity, rows[0].rce_org_oid, rows[1].rce_org_oid


async def test_a_member_with_two_records_is_never_sent_back_to_the_record_already_reviewed(
        rolled_back_db, monkeypatch):
    """The reference picked for a member is deterministic (the lowest), and once that record has its own review
    classified on submitted values the member is given its OTHER record - the reviewed one is not verified a second
    time through the sibling's membership."""
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    await _assess_on(db, delivery, "2026-11-09")
    entity, low, high = await _two_blank_records_of_one_entity(db, delivery)
    for _ in range(3):                                                  # deterministic, call after call
        assert (await rc._refs_for(db, delivery.intake_id, [entity])) == {entity: low}

    first = await ap.verify_and_classify(db, [low], intake_id=delivery.intake_id)
    assert first["classified_on_submitted_values"] == [low]
    assert (await rc._refs_for(db, delivery.intake_id, [entity])) == {entity: high}   # never `low` again

    await ap.verify_and_classify(db, [high], intake_id=delivery.intake_id)
    assert (await rc._refs_for(db, delivery.intake_id, [entity])) == {}               # both reviewed: nothing left
    reviews = (await db.execute(select(reg.ReviewRecord.source_record_id).where(
        reg.ReviewRecord.entity_id == entity))).scalars().all()
    assert len(reviews) == 2 and len(set(reviews)) == 2                 # one review per record, no duplicate
    # every one-record member is exactly as before: its single reference
    single = delivery.entity["reviewable"]
    assert (await rc._refs_for(db, delivery.intake_id, [single])) == {single: delivery.ref["reviewable"]}


async def test_frame_consistency_is_not_hidden_by_an_already_reviewed_record(rolled_back_db, monkeypatch):
    """Two eligible records of one entity: `frame_consistent` is false. After ONE of them is reviewed on its
    submitted values it leaves the frame, so eligible units equal members again - but the entity still has two
    records, and the pre-check keeps saying so (`entities_with_several_records`, `frame_consistent: false`)."""
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    await _assess_on(db, delivery, "2026-11-09")
    before = (await _page(db, delivery))["frame"]
    assert before["frame_consistent"] is True and before["entities_with_several_records"] == 0   # one HELD: not two

    _entity, low, _high = await _two_blank_records_of_one_entity(db, delivery)
    two = (await _page(db, delivery))["frame"]
    assert two["entities_with_several_records"] == 1 and two["frame_consistent"] is False
    assert two["eligible_units"] == two["members_total"] + 1

    await ap.verify_and_classify(db, [low], intake_id=delivery.intake_id)
    after = (await _page(db, delivery))["frame"]
    assert after["eligible_units"] == after["members_total"]            # the exclusion made the counts agree ...
    assert after["entities_with_several_records"] == 1 and after["frame_consistent"] is False    # ... not the truth


# ═══ the pre-check and a member whose record is already reviewed (second delta review of #249) ══════════════════════

async def test_the_precheck_says_a_member_will_be_linked_to_its_existing_review_not_that_it_has_no_reference(
        rolled_back_db, monkeypatch):
    """A member that is still in the frame through a record with no delivered reference, whose OTHER record already
    has its own review classified on submitted values: the cycle will link the member to that review. The pre-check
    says exactly that (reviewable, named, with the review id) instead of "no delivered reference"; and when that
    review belongs to another sample it says why it will not be linked, with a kind of its own."""
    db = rolled_back_db
    delivery = await _Delivery().build(db, monkeypatch)
    await _assess_on(db, delivery, "2026-11-09")
    entity, low, high = await _two_blank_records_of_one_entity(db, delivery)
    first = await ap.verify_and_classify(db, [low], intake_id=delivery.intake_id)
    review_id = first["outcomes"][0]["review_id"]
    other_row = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == delivery.intake_id,
        m.RceCuratedRecord.rce_org_oid == high))).scalars().one()
    other_row.rce_org_oid = None                                        # the member's remaining record has no reference
    await db.flush()

    page = await _page(db, delivery)
    assert page["counts"]["will_link_existing_submitted_values_review"] == 1
    assert page["will_link_existing_submitted_values_review"] == [
        {"entity_id": str(entity), "entity_ref": low, "existing_review": review_id}]
    listed = {n["entity_id"]: n for n in page["not_reviewable"]}
    assert str(entity) not in listed                                    # not "no delivered reference"

    review = (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == review_id))).scalars().one()
    other_sample = reg.ReviewSample(id=uuid.uuid4(), sample_name="synthetic other sample", review_type="weekly",
                                    population_size=1, sample_size=1, confidence_level=0.95,
                                    margin_of_error=0.05, proportion=0.5)
    db.add(other_sample)
    await db.flush()
    review.sample_id = other_sample.id
    await db.flush()
    page = await _page(db, delivery)
    assert page["counts"]["will_link_existing_submitted_values_review"] == 0
    entry = {n["entity_id"]: n for n in page["not_reviewable"]}[str(entity)]
    assert entry["kind"] == rc.KIND_ALREADY_REVIEWED
    assert entry["reason"] == rc.NOT_REVIEWABLE_ALREADY_REVIEWED.format(review=review_id, why=rc.WHY_OTHER_SAMPLE)
    assert "no delivered reference" not in entry["reason"]
