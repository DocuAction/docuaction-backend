"""`record_history_comparison.py` -- month-to-month record investigation.

Entirely synthetic data, reusing the exact seeding helpers
`test_issue_history_*` already use (`seed_delivery`, `entity_row`,
`settings_for`) so a delivery here is built the same way a real one is,
not a hand-made imitation. Covers: search by each identifier type, multiple
directory entries for one identifier, HCID delivery history (including the
"no delivery this month" case and ambiguous-HCID-within-one-delivery), and
field comparison across all five change-type outcomes.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.tefca_registry.rce import record_history_comparison as svc
from issue_history_support_2026_10_07 import (FEED, entity_row, seed_delivery,
                                              settings_for)
from rce_traceability_support import rolled_back_db, seed_entity  # noqa: F401

pytestmark = pytest.mark.regression


class _User:
    def __init__(self, role, allowed_modules=None):
        self.role, self.allowed_modules, self.id, self.email = role, allowed_modules, None, "t@example.test"


class TestSearch:
    async def test_search_by_npi_finds_the_record(self, rolled_back_db):
        db = rolled_back_db
        row = entity_row("9.99.777.9.S1", npi="1234567893", tag="S1")
        await seed_delivery(db, [row], received_at=datetime(2026, 9, 2), feed=FEED)

        result = await svc.search_records(
            db, npi="1234567893", reviewer_or_above=False, settings=settings_for())

        assert result["count"] == 1
        assert result["results"][0]["npi"] == "1234567893"
        assert result["results"][0]["matched_on"] == "npi"

    async def test_search_by_hcid_returns_multiple_directory_entries(self, rolled_back_db):
        """The same HCID appearing on more than one delivered line (a real
        condition confirmed in the real September file) must come back as
        multiple results, not be collapsed to one."""
        db = rolled_back_db
        hcid = "urn:oid:9.99.777.555555"
        row_a = entity_row("9.99.777.9.DUP1", tag="DUP1")
        row_a["HCID"] = hcid
        row_b = entity_row("9.99.777.9.DUP2", tag="DUP2")
        row_b["HCID"] = hcid
        await seed_delivery(db, [row_a, row_b], received_at=datetime(2026, 9, 2), feed=FEED)

        result = await svc.search_records(
            db, hcid=hcid, reviewer_or_above=False, settings=settings_for())

        assert result["count"] == 2
        assert {r["source_rce_id"] for r in result["results"]} == {"9.99.777.9.DUP1", "9.99.777.9.DUP2"}

    async def test_search_requires_at_least_one_field(self, rolled_back_db):
        with pytest.raises(ValueError):
            await svc.search_records(rolled_back_db, reviewer_or_above=False, settings=settings_for())

    async def test_search_sees_nothing_outside_allowed_feeds(self, rolled_back_db):
        db = rolled_back_db
        row = entity_row("9.99.777.9.HIDDEN", npi="1234567893", tag="HIDDEN")
        await seed_delivery(db, [row], received_at=datetime(2026, 9, 2), feed="OTHER-FEED")

        result = await svc.search_records(
            db, npi="1234567893", reviewer_or_above=False, settings=settings_for(viewer=FEED))

        assert result["count"] == 0


class TestHcidDeliveryHistory:
    async def test_history_spans_two_real_deliveries_no_interpolation_for_the_gap_month(
            self, rolled_back_db):
        """July and September both carry this HCID; August has no delivery
        at all (the real condition in this engagement's actual data) -- the
        response must simply have no August entry, not a fabricated one."""
        db = rolled_back_db
        hcid = "urn:oid:9.99.777.777001"
        july_row = entity_row("9.99.777.9.JULY", npi="1234567893", tag="JULY")
        july_row["HCID"] = hcid
        sept_row = entity_row("9.99.777.9.SEPT", npi="1234567893", tag="SEPT")
        sept_row["HCID"] = hcid
        await seed_delivery(db, [july_row], received_at=datetime(2026, 7, 20), feed=FEED)
        await seed_delivery(db, [sept_row], received_at=datetime(2026, 9, 2), feed=FEED)

        result = await svc.hcid_delivery_history(
            db, hcid=hcid, reviewer_or_above=False, settings=settings_for())

        assert len(result["deliveries"]) == 2
        months = [d["received_at"][:7] for d in result["deliveries"]]
        assert months == ["2026-07", "2026-09"]
        assert all(not d["ambiguous_linkage"] for d in result["deliveries"])

    async def test_hcid_repeated_within_one_delivery_is_flagged_ambiguous(self, rolled_back_db):
        db = rolled_back_db
        hcid = "urn:oid:9.99.777.777002"
        row_a = entity_row("9.99.777.9.AMBA", tag="AMBA")
        row_a["HCID"] = hcid
        row_b = entity_row("9.99.777.9.AMBB", tag="AMBB")
        row_b["HCID"] = hcid
        await seed_delivery(db, [row_a, row_b], received_at=datetime(2026, 9, 2), feed=FEED)

        result = await svc.hcid_delivery_history(
            db, hcid=hcid, reviewer_or_above=False, settings=settings_for())

        assert len(result["deliveries"]) == 1
        assert result["deliveries"][0]["ambiguous_linkage"] is True
        assert len(result["deliveries"][0]["records"]) == 2

    async def test_unknown_hcid_raises_not_found(self, rolled_back_db):
        db = rolled_back_db
        row = entity_row("9.99.777.9.X", tag="X")
        await seed_delivery(db, [row], received_at=datetime(2026, 9, 2), feed=FEED)
        with pytest.raises(svc.RecordHistoryNotFound):
            await svc.hcid_delivery_history(
                db, hcid="urn:oid:9.99.777.999999", reviewer_or_above=False, settings=settings_for())

    async def test_no_allowed_feed_raises_not_found_not_an_empty_list(self, rolled_back_db):
        db = rolled_back_db
        row = entity_row("9.99.777.9.Y", tag="Y")
        await seed_delivery(db, [row], received_at=datetime(2026, 9, 2), feed=FEED)
        with pytest.raises(svc.RecordHistoryNotFound):
            await svc.hcid_delivery_history(
                db, hcid="urn:oid:9.99.777.anything", reviewer_or_above=False,
                settings=settings_for(viewer=""))


async def _compare(db, earlier_id, later_id, **over):
    kwargs = dict(reviewer_or_above=False, settings=settings_for())
    kwargs.update(over)
    return await svc.compare_two_records(
        db, earlier_record_id=earlier_id, later_record_id=later_id, **kwargs)


class TestFieldComparison:
    async def _two_records(self, db, earlier_row, later_row, *,
                           earlier_feed=FEED, later_feed=FEED,
                           earlier_at=datetime(2026, 7, 20), later_at=datetime(2026, 9, 2)):
        earlier_id = await seed_delivery(db, [earlier_row], received_at=earlier_at,
                                         feed=earlier_feed, blob_salt="earlier")
        later_id = await seed_delivery(db, [later_row], received_at=later_at,
                                       feed=later_feed, blob_salt="later")
        from sqlalchemy import select
        from app.tefca_registry.rce import models as m
        earlier_rec = (await db.execute(select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == earlier_id))).scalars().first()
        later_rec = (await db.execute(select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == later_id))).scalars().first()
        return earlier_rec, later_rec

    async def test_unchanged_field_and_changed_field_and_formatting_only(self, rolled_back_db):
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMP1", tag="CMP1")
        earlier["name"] = "Example Clinic"
        earlier["address_city"] = "testville"
        later = dict(earlier)
        later["name"] = "Example Clinic Renamed"       # substantive change
        later["address_city"] = "  TESTVILLE  "        # formatting-only change
        earlier_rec, later_rec = await self._two_records(db, earlier, later)

        result = await _compare(db, earlier_rec.id, later_rec.id)

        by_field = {f["field"]: f for f in result["fields"]}
        assert by_field["name"]["change_type"] == svc.OUTCOME_CHANGED
        assert by_field["address_city"]["change_type"] == svc.OUTCOME_UNCHANGED_FORMATTING_ONLY
        assert by_field["TEFCAID"]["change_type"] == svc.OUTCOME_UNCHANGED  # untouched field
        assert result["earlier"]["received_at"].startswith("2026-07-20")
        assert result["later"]["received_at"].startswith("2026-09-02")

    async def test_deactivation_is_reported_as_deactivation_not_reactivation(self, rolled_back_db):
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMP2", tag="CMP2")
        earlier["active"] = "1"
        later = dict(earlier)
        later["active"] = "0"
        earlier_rec, later_rec = await self._two_records(db, earlier, later)

        result = await _compare(db, earlier_rec.id, later_rec.id)

        assert result["active_status_change"] == {
            "direction": svc.ACTIVE_DEACTIVATED, "earlier_value": "1", "later_value": "0"}
        assert result["inactive_reactivated"] is None

    async def test_reactivation_is_reported_as_reactivation(self, rolled_back_db):
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMP2R", tag="CMP2R")
        earlier["active"] = "0"
        later = dict(earlier)
        later["active"] = "1"
        earlier_rec, later_rec = await self._two_records(db, earlier, later)

        result = await _compare(db, earlier_rec.id, later_rec.id)

        assert result["active_status_change"]["direction"] == svc.ACTIVE_REACTIVATED
        assert result["inactive_reactivated"] == {"earlier_value": "0", "later_value": "1"}

    async def test_record_outside_the_callers_feeds_is_not_found_not_compared(self, rolled_back_db):
        """A viewer holding two UUIDs must not be able to read a record from a
        feed they are not allowed to see; the response is the same 404 as a
        non-existent id, so the id itself leaks nothing."""
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMPF", tag="CMPF")
        earlier_rec, later_rec = await self._two_records(
            db, earlier, dict(earlier), later_feed="OTHER-FEED")

        with pytest.raises(svc.RecordHistoryNotFound):
            await _compare(db, earlier_rec.id, later_rec.id, settings=settings_for(viewer=FEED))
        with pytest.raises(svc.RecordHistoryNotFound):
            await _compare(db, earlier_rec.id, later_rec.id, settings=settings_for(viewer=""))

    async def test_swapped_chronology_is_refused_not_silently_diffed(self, rolled_back_db):
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMPC", tag="CMPC")
        earlier_rec, later_rec = await self._two_records(db, earlier, dict(earlier))

        with pytest.raises(svc.ChronologyError):
            await _compare(db, later_rec.id, earlier_rec.id)
        assert isinstance(svc.ChronologyError("x"), ValueError)

    async def test_same_instant_deliveries_order_deterministically_by_intake_id(self, rolled_back_db):
        db = rolled_back_db
        row = entity_row("9.99.777.9.CMPT", tag="CMPT")
        same = datetime(2026, 9, 2, 12, 0, 0)
        rec_x, rec_y = await self._two_records(db, row, dict(row), earlier_at=same, later_at=same)
        first, second = sorted(
            (rec_x, rec_y), key=lambda r: svc.delivery_order_key(same, r.source_intake_id))

        result = await _compare(db, first.id, second.id)
        assert result["earlier"]["intake_id"] == str(first.source_intake_id)
        with pytest.raises(svc.ChronologyError):
            await _compare(db, second.id, first.id)

    async def test_field_present_only_later_is_newly_observed_not_created(self, rolled_back_db):
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMP3", tag="CMP3")
        later = dict(earlier)
        later["alias"] = "A Brand New Alias"
        earlier["alias"] = ""
        # Give the earlier delivery's `alias` an actually-absent key to
        # exercise NEWLY_OBSERVED (not merely blank-vs-filled).
        earlier_rec, later_rec = await self._two_records(db, earlier, later)
        earlier_rec.parsed = {k: v for k, v in earlier_rec.parsed.items() if k != "alias"}
        await db.flush()

        result = await _compare(db, earlier_rec.id, later_rec.id)

        by_field = {f["field"]: f for f in result["fields"]}
        assert by_field["alias"]["change_type"] == svc.OUTCOME_NEWLY_OBSERVED

    async def test_field_present_only_earlier_is_absent_from_later_not_deleted(self, rolled_back_db):
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMP4", tag="CMP4")
        later = dict(earlier)
        earlier_rec, later_rec = await self._two_records(db, earlier, later)
        later_rec.parsed = {k: v for k, v in later_rec.parsed.items() if k != "alias"}
        await db.flush()

        result = await _compare(db, earlier_rec.id, later_rec.id)

        by_field = {f["field"]: f for f in result["fields"]}
        assert by_field["alias"]["change_type"] == svc.OUTCOME_ABSENT_FROM_LATER

    async def test_unknown_record_id_is_not_found_indistinguishable_from_hidden(self, rolled_back_db):
        import uuid
        db = rolled_back_db
        row = entity_row("9.99.777.9.CMP5", tag="CMP5")
        _, later_rec = await self._two_records(db, row, dict(row))
        with pytest.raises(svc.RecordHistoryNotFound):
            await _compare(db, uuid.uuid4(), later_rec.id)


class TestQhinScopedSearch:
    async def _seed_scoped_pair(self, db):
        """Two promoted entities under two QHINs, delivered as two consecutive
        lines of one intake (A on line 2, B on line 3). Returns (qhin_a, qhin_b)."""
        import uuid as _uuid
        from datetime import date
        from sqlalchemy import select
        from app.tefca_registry import models as reg
        from app.tefca_registry.rce import models as m

        qhin_a = await seed_entity(db, oid="9.99.777.QHIN.A", name="QHIN A")
        qhin_b = await seed_entity(db, oid="9.99.777.QHIN.B", name="QHIN B")
        entity_a = await seed_entity(db, oid="9.99.777.ENT.A", name="Scoped Org A")
        entity_b = await seed_entity(db, oid="9.99.777.ENT.B", name="Scoped Org B")
        for parent, child in ((qhin_a, entity_a), (qhin_b, entity_b)):
            db.add(reg.TefcaEntityRelationship(
                id=_uuid.uuid4(), parent_entity_id=parent, child_entity_id=child,
                relationship_type="belongs_to", status="active", effective_date=date(2026, 1, 1)))
        await db.flush()
        row_a = entity_row("9.99.777.9.QA", tag="QA")
        row_a["name"] = "Scoped Org A Delivered"
        row_b = entity_row("9.99.777.9.QB", tag="QB")
        row_b["name"] = "Scoped Org B Delivered"
        intake_id = await seed_delivery(db, [row_a, row_b], received_at=datetime(2026, 9, 2), feed=FEED)
        for oid, entity in (("9.99.777.9.QA", entity_a), ("9.99.777.9.QB", entity_b)):
            rec = (await db.execute(select(m.RceSourceRecord).where(
                m.RceSourceRecord.source_intake_id == intake_id,
                m.RceSourceRecord.source_rce_id == oid))).scalar_one()
            rec.canonical_entity_id = entity
        await db.flush()
        return qhin_a, qhin_b

    async def test_qhin_scope_is_applied_before_the_limit(self, rolled_back_db):
        """With LIMIT 1 and the first-ordered line belonging to the OTHER QHIN,
        the matching record must still be returned: the scope is part of the
        SQL WHERE, not a filter over the first page."""
        db = rolled_back_db
        _qhin_a, qhin_b = await self._seed_scoped_pair(db)

        result = await svc.search_records(
            db, name="Scoped Org", qhin_entity_id=qhin_b, limit=1,
            reviewer_or_above=False, settings=settings_for())

        assert result["count"] == 1
        assert result["results"][0]["source_rce_id"] == "9.99.777.9.QB"

    async def test_search_filters_to_only_the_named_qhin(self, rolled_back_db):
        """Two promoted entities, same NPI-less search by name, different
        QHINs -- qhin_entity_id must exclude the one that does not belong."""
        import uuid as _uuid
        from sqlalchemy import select
        from app.tefca_registry import models as reg
        from app.tefca_registry.rce import models as m

        db = rolled_back_db
        qhin_a = await seed_entity(db, oid="9.99.777.QHIN.A", name="QHIN A")
        qhin_b = await seed_entity(db, oid="9.99.777.QHIN.B", name="QHIN B")
        entity_a = await seed_entity(db, oid="9.99.777.ENT.A", name="Scoped Org A")
        entity_b = await seed_entity(db, oid="9.99.777.ENT.B", name="Scoped Org B")
        from datetime import date
        db.add(reg.TefcaEntityRelationship(
            id=_uuid.uuid4(), parent_entity_id=qhin_a, child_entity_id=entity_a,
            relationship_type="belongs_to", status="active", effective_date=date(2026, 1, 1)))
        db.add(reg.TefcaEntityRelationship(
            id=_uuid.uuid4(), parent_entity_id=qhin_b, child_entity_id=entity_b,
            relationship_type="belongs_to", status="active", effective_date=date(2026, 1, 1)))
        await db.flush()

        row_a = entity_row("9.99.777.9.QA", tag="QA")
        row_a["name"] = "Scoped Org A Delivered"
        row_b = entity_row("9.99.777.9.QB", tag="QB")
        row_b["name"] = "Scoped Org B Delivered"
        intake_id = await seed_delivery(db, [row_a, row_b], received_at=datetime(2026, 9, 2), feed=FEED)

        rec_a = (await db.execute(select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id,
            m.RceSourceRecord.source_rce_id == "9.99.777.9.QA"))).scalar_one()
        rec_b = (await db.execute(select(m.RceSourceRecord).where(
            m.RceSourceRecord.source_intake_id == intake_id,
            m.RceSourceRecord.source_rce_id == "9.99.777.9.QB"))).scalar_one()
        rec_a.canonical_entity_id = entity_a
        rec_b.canonical_entity_id = entity_b
        await db.flush()

        result = await svc.search_records(
            db, name="Scoped Org", qhin_entity_id=qhin_a,
            reviewer_or_above=False, settings=settings_for())

        assert result["count"] == 1
        assert result["results"][0]["source_rce_id"] == "9.99.777.9.QA"


class TestFullPopulationChangeReport:
    async def test_two_consecutive_deliveries_report_only_actual_changes(self, rolled_back_db):
        db = rolled_back_db
        hcid = "urn:oid:9.99.777.888001"
        earlier = entity_row("9.99.777.9.CR1", tag="CR1")
        earlier["HCID"] = hcid
        earlier["name"] = "Change Report Org"
        later = dict(earlier)
        later["name"] = "Change Report Org Renamed"
        await seed_delivery(db, [earlier], received_at=datetime(2026, 7, 20), feed=FEED, blob_salt="cr-e")
        await seed_delivery(db, [later], received_at=datetime(2026, 9, 2), feed=FEED, blob_salt="cr-l")

        rows = await svc.full_population_change_report(
            db, reviewer_or_above=False, settings=settings_for())

        name_rows = [r for r in rows if r["hcid"] == hcid and r["field"] == "name"]
        assert len(name_rows) == 1
        assert name_rows[0]["change_type"] == svc.OUTCOME_CHANGED
        assert name_rows[0]["earlier_value"] == "Change Report Org"
        assert name_rows[0]["later_value"] == "Change Report Org Renamed"
        # Unchanged fields (e.g. TEFCAID, untouched) are not in the report.
        assert not [r for r in rows if r["hcid"] == hcid and r["field"] == "TEFCAID"]

    async def test_single_delivery_hcid_contributes_nothing(self, rolled_back_db):
        db = rolled_back_db
        hcid = "urn:oid:9.99.777.888002"
        row = entity_row("9.99.777.9.CR2", tag="CR2")
        row["HCID"] = hcid
        await seed_delivery(db, [row], received_at=datetime(2026, 9, 2), feed=FEED)

        rows = await svc.full_population_change_report(
            db, reviewer_or_above=False, settings=settings_for())

        assert not [r for r in rows if r["hcid"] == hcid]

    async def test_ambiguous_linkage_pair_is_reported_not_silently_compared(self, rolled_back_db):
        db = rolled_back_db
        hcid = "urn:oid:9.99.777.888003"
        earlier = entity_row("9.99.777.9.CR3", tag="CR3")
        earlier["HCID"] = hcid
        later_a = entity_row("9.99.777.9.CR3A", tag="CR3A")
        later_a["HCID"] = hcid
        later_b = entity_row("9.99.777.9.CR3B", tag="CR3B")
        later_b["HCID"] = hcid
        await seed_delivery(db, [earlier], received_at=datetime(2026, 7, 20), feed=FEED, blob_salt="cr3-e")
        await seed_delivery(db, [later_a, later_b], received_at=datetime(2026, 9, 2), feed=FEED, blob_salt="cr3-l")

        rows = await svc.full_population_change_report(
            db, reviewer_or_above=False, settings=settings_for())

        hcid_rows = [r for r in rows if r["hcid"] == hcid]
        assert len(hcid_rows) == 1
        assert hcid_rows[0]["change_type"] == "AMBIGUOUS_LINKAGE_NOT_COMPARED"


# ── trailing window on the record search (2026-10-25) ───────────────────────

class TestSearchWindow:
    def test_no_window_when_neither_bound_is_given(self):
        assert svc.search_window(None, None) is None

    def test_window_is_stated_explicitly_and_defaults_to_twelve_months(self):
        from datetime import date
        w = svc.search_window(date(2026, 10, 25), None)
        assert w["as_of"] == "2026-10-25" and w["months"] == 12
        assert w["received_to"] == "2026-10-25T23:59:59"
        assert w["received_from"] == "2025-10-25T23:59:59"  # 12 x 30.4 = 365 days
        assert w["applies_to"] == "rce_source_intakes.received_at"
        assert "interpolated" in w["note"]

    async def test_search_honours_the_window_and_reports_it(self, rolled_back_db):
        """July and September deliveries carry the same organisation; a one-month
        window ending 30 Sep returns only September, a twelve-month window both,
        and a window before either returns nothing (not an interpolated July)."""
        from datetime import date
        db = rolled_back_db
        july = entity_row("9.99.777.9.W1", tag="W1"); july["name"] = "Windowed Org July"
        sept = entity_row("9.99.777.9.W2", tag="W2"); sept["name"] = "Windowed Org Sept"
        await seed_delivery(db, [july], received_at=datetime(2026, 7, 20), feed=FEED, blob_salt="win-j")
        await seed_delivery(db, [sept], received_at=datetime(2026, 9, 2), feed=FEED, blob_salt="win-s")

        one_month = await svc.search_records(db, name="Windowed Org", reviewer_or_above=False,
                                             settings=settings_for(), as_of=date(2026, 9, 30), months=1)
        assert [r["name"] for r in one_month["results"]] == ["Windowed Org Sept"]
        assert one_month["window"]["months"] == 1

        twelve = await svc.search_records(db, name="Windowed Org", reviewer_or_above=False,
                                          settings=settings_for(), as_of=date(2026, 9, 30), months=12)
        assert sorted(r["name"] for r in twelve["results"]) == ["Windowed Org July", "Windowed Org Sept"]

        before = await svc.search_records(db, name="Windowed Org", reviewer_or_above=False,
                                          settings=settings_for(), as_of=date(2026, 6, 30), months=1)
        assert before["results"] == [] and before["count"] == 0 and "window" in before
