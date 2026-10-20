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


class TestFieldComparison:
    async def _two_records(self, db, earlier_row, later_row):
        from rce_traceability_support import SYN
        earlier_id = await seed_delivery(db, [earlier_row], received_at=datetime(2026, 7, 20),
                                         feed=FEED, blob_salt="earlier")
        later_id = await seed_delivery(db, [later_row], received_at=datetime(2026, 9, 2),
                                       feed=FEED, blob_salt="later")
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

        result = await svc.compare_two_records(
            db, earlier_record_id=earlier_rec.id, later_record_id=later_rec.id)

        by_field = {f["field"]: f for f in result["fields"]}
        assert by_field["name"]["change_type"] == svc.OUTCOME_CHANGED
        assert by_field["address_city"]["change_type"] == svc.OUTCOME_UNCHANGED_FORMATTING_ONLY
        assert by_field["TEFCAID"]["change_type"] == svc.OUTCOME_UNCHANGED  # untouched field
        assert result["earlier"]["received_at"].startswith("2026-07-20")
        assert result["later"]["received_at"].startswith("2026-09-02")

    async def test_active_field_change_surfaces_as_inactive_reactivated(self, rolled_back_db):
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMP2", tag="CMP2")
        earlier["active"] = "1"
        later = dict(earlier)
        later["active"] = "0"
        earlier_rec, later_rec = await self._two_records(db, earlier, later)

        result = await svc.compare_two_records(
            db, earlier_record_id=earlier_rec.id, later_record_id=later_rec.id)

        assert result["inactive_reactivated"] == {"earlier_value": "1", "later_value": "0"}

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

        result = await svc.compare_two_records(
            db, earlier_record_id=earlier_rec.id, later_record_id=later_rec.id)

        by_field = {f["field"]: f for f in result["fields"]}
        assert by_field["alias"]["change_type"] == svc.OUTCOME_NEWLY_OBSERVED

    async def test_field_present_only_earlier_is_absent_from_later_not_deleted(self, rolled_back_db):
        db = rolled_back_db
        earlier = entity_row("9.99.777.9.CMP4", tag="CMP4")
        later = dict(earlier)
        earlier_rec, later_rec = await self._two_records(db, earlier, later)
        later_rec.parsed = {k: v for k, v in later_rec.parsed.items() if k != "alias"}
        await db.flush()

        result = await svc.compare_two_records(
            db, earlier_record_id=earlier_rec.id, later_record_id=later_rec.id)

        by_field = {f["field"]: f for f in result["fields"]}
        assert by_field["alias"]["change_type"] == svc.OUTCOME_ABSENT_FROM_LATER

    async def test_unknown_record_id_raises_value_error(self, rolled_back_db):
        import uuid
        db = rolled_back_db
        row = entity_row("9.99.777.9.CMP5", tag="CMP5")
        _, later_rec = await self._two_records(db, row, dict(row))
        with pytest.raises(ValueError):
            await svc.compare_two_records(
                db, earlier_record_id=uuid.uuid4(), later_record_id=later_rec.id)


class TestQhinScopedSearch:
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
