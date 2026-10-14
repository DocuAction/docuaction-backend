"""IQVIA advisory assessment: lookup -> assessment -> evidence row -> report/CSV disclosure -> role redaction.

No database: the lookup is replaced by a synthetic APPROVED-snapshot result (the real shape returned by
`organisation_relationships`), the session is a recorder, and every identifier is synthetic. The real-Postgres
variant of the lookup itself is covered by test_iqvia_affiliation_org_lookup.py.
"""
from __future__ import annotations

import asyncio
import csv
import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.reports.engine.csv_engine import delivery_processing_to_csv
from app.tefca_registry.rce import iqvia_advisory_assessment as aa
from app.tefca_registry.rce import iqvia_affiliation_consumer as ac

pytestmark = pytest.mark.regression

NPI = "1043566623"  # Luhn-valid synthetic
SNAP = uuid.uuid4()
SECRET = "unit-test-secret"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def lookup_result(*, found=True, conflicts=(), notes=(), status="APPROVED"):
    return {
        "provenance": {"snapshot_id": str(SNAP), "source_system": "IQVIA_AFFILIATION", "status": status,
                       "record_count": 10, "sha256_prefix": "aaaaaaaaaaaa"},
        "determination": ac.DETERMINATION, "requires_analyst_review": True, "affects_verification": False,
        "found_in_extract": found,
        "organisations": [{"hco_record_key": "SYN-O1", "relationship_rows": 3}] if found else [],
        "organisations_omitted": 0, "conflicts": [{"code": c} for c in conflicts], "notes": list(notes),
        "registry_candidate": {"outcome": "CANDIDATE"}, "relationships": [], "total_relationships": 3 if found else 0,
        "returned": 0, "page_limit": 1, "truncated": found, "next_cursor": None}


class Db:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def user(role, email=None):
    return SimpleNamespace(role=role, email=email or f"{role}@test.local")


@pytest.fixture
def patch_lookup(monkeypatch):
    def _set(result=None, exc=None):
        async def fake(db, **kw):
            if exc:
                raise exc
            return result
        monkeypatch.setattr(ac, "organisation_relationships", fake)

        async def delivery_ok(db, **kw):  # delivery validation has its own tests below and a DB-backed file
            return None
        monkeypatch.setattr(aa, "require_delivery_context", delivery_ok)
    return _set


def record(db, u, decision, **kw):
    return run(aa.record_assessment(db, user=u, secret=SECRET, correlation_id="c1", build_sha="b" * 8,
                                    snapshot_id=SNAP, org_npi=NPI, decision=decision,
                                    rationale=kw.pop("rationale", "synthetic rationale text"), **kw))


def test_flags_are_off_by_default():
    fields = type(settings).model_fields
    assert fields["ENABLE_IQVIA_AFFILIATION_CONSUMPTION"].default is False


def test_evidence_row_binds_snapshot_inputs_result_and_timestamp_without_raw_identifiers(patch_lookup):
    patch_lookup(lookup_result())
    db = Db()
    row = record(db, user("reviewer"), aa.D_IDENTITY, delivery_intake_id=uuid.uuid4())
    assert db.added == [row]
    assert row.snapshot_id == SNAP and row.snapshot_status == "APPROVED" and row.snapshot_sha256_prefix == "aaaaaaaaaaaa"
    assert row.result_summary["found_in_extract"] is True and row.result_summary["total_relationships"] == 3
    assert row.determination == aa.DETERMINATION and row.affects_verification is False
    assert row.assessed_by == "reviewer@test.local" and row.assessed_role == "reviewer"
    blob = repr(row.query_inputs) + repr(row.result_summary)
    assert NPI not in blob                                  # raw identifier never stored
    assert row.query_inputs["org_npi_keyed_hash"] == aa.keyed_hash(NPI, SECRET)
    assert row.query_inputs["org_npi_masked"] == "10***(len10)"
    assert "assessed_at" in aa.IqviaAdvisoryAssessment.__table__.c            # DB default stamps the time


def test_relationship_corroboration_needs_a_relationship_row(patch_lookup):
    res = lookup_result()
    res["total_relationships"] = 0
    patch_lookup(res)
    db = Db()
    with pytest.raises(aa.AssessmentRefused) as e:
        record(db, user("reviewer"), aa.D_RELATIONSHIP)
    assert e.value.code == "NO_RELATIONSHIP_TO_CORROBORATE" and db.added == []
    assert record(db, user("reviewer"), aa.D_IDENTITY).decision == aa.D_IDENTITY  # identity may still be recorded


@pytest.mark.parametrize("decision,kw,code", [
    (aa.D_IDENTITY, dict(found=False), "NOT_FOUND_CANNOT_CORROBORATE"),
    (aa.D_RELATIONSHIP, dict(conflicts=[ac.CONFLICT_IDENTIFIER_SHARED]), "CONFLICT_CANNOT_CORROBORATE"),
    (aa.D_NOT_FOUND, dict(found=True), "FOUND_CANNOT_BE_NOT_FOUND"),
    (aa.D_CONFLICT, dict(found=True), "NO_CONFLICT_TO_REFER"),
    (aa.D_IDENTITY, dict(found=False, notes=["NO_USABLE_IDENTIFIER"]), "UNAVAILABLE_NO_USABLE_IDENTIFIER"),
    ("CLEARED", dict(), "UNKNOWN_DECISION"),
    ("B1", dict(), "UNKNOWN_DECISION"),
])
def test_decision_cannot_claim_more_than_the_lookup_supports(patch_lookup, decision, kw, code):
    patch_lookup(lookup_result(**kw))
    db = Db()
    with pytest.raises(aa.AssessmentRefused) as e:
        record(db, user("reviewer"), decision)
    assert e.value.code == code and db.added == []          # refusal writes nothing


def test_unavailable_state_records_insufficient_and_conflict_is_referred(patch_lookup):
    patch_lookup(lookup_result(found=False, notes=["NO_USABLE_IDENTIFIER"]))
    assert record(Db(), user("reviewer"), aa.D_INSUFFICIENT).decision == aa.D_INSUFFICIENT
    patch_lookup(lookup_result(conflicts=[ac.CONFLICT_NPI_CCN_DIFFERENT_ORGS]))
    assert record(Db(), user("reviewer"), aa.D_CONFLICT).result_summary["conflict_codes"] == [
        ac.CONFLICT_NPI_CCN_DIFFERENT_ORGS]


def test_non_approved_snapshot_is_refused_and_no_row_is_written(patch_lookup):
    patch_lookup(exc=ac.SnapshotNotEligible("snapshot is PENDING; only an approved snapshot may be read"))
    db = Db()
    with pytest.raises(ac.SnapshotNotEligible):
        record(db, user("reviewer"), aa.D_IDENTITY)
    assert db.added == []


def test_rationale_is_required(patch_lookup):
    patch_lookup(lookup_result())
    with pytest.raises(aa.AssessmentRefused) as e:
        record(Db(), user("reviewer"), aa.D_IDENTITY, rationale="   ")
    assert e.value.code == "RATIONALE_REQUIRED"


def stored(patch_lookup, assessor="reviewer@test.local"):
    patch_lookup(lookup_result())
    row = record(Db(), user("reviewer", assessor), aa.D_IDENTITY, entity_id=uuid.uuid4())
    row.id, row.assessed_at = uuid.uuid4(), datetime(2026, 10, 14, tzinfo=timezone.utc)
    return aa._row_dict(row)


def test_reviewer_view_is_masked_and_hides_keys_hashes_entity_and_foreign_rationale(patch_lookup):
    d = stored(patch_lookup, assessor="other@test.local")
    out = aa.redact_for_role(d, user("reviewer"))
    assert out["redaction_level"] == "REVIEWER"
    assert "hco_record_keys" not in out["result_summary"]
    assert not any(k.endswith("keyed_hash") for k in out["query_inputs"])
    assert out["entity_id"] is None and out["rationale"] is None and out["rationale_redacted"] is True
    assert out["query_inputs"]["org_npi_masked"] == "10***(len10)"
    assert NPI not in repr(out)


def test_author_sees_own_rationale_and_senior_analyst_is_still_redacted(patch_lookup):
    d = stored(patch_lookup)
    assert aa.redact_for_role(d, user("reviewer"))["rationale"] == "synthetic rationale text"
    sa = aa.redact_for_role(d, user("senior_analyst", "sa@test.local"))
    assert sa["redaction_level"] == "REVIEWER" and sa["rationale"] is None


@pytest.mark.parametrize("role", ["qalead", "program_manager", "admin"])
def test_qalead_and_above_see_full_row(patch_lookup, role):
    d = stored(patch_lookup)
    out = aa.redact_for_role(d, user(role, "x@test.local"))
    assert out["redaction_level"] == "FULL" and out["rationale"] and out["entity_id"]
    assert out["result_summary"]["hco_record_keys"] == ["SYN-O1"]
    assert out["query_inputs"]["org_npi_keyed_hash"]


def test_disclosure_states_actual_use_only_and_never_verification():
    none = aa.disclosure_from_rows(flag_enabled=True, rows=[])
    assert none["state"] == "IQVIA_NOT_USED" and "not use" in none["statement"]
    off = aa.disclosure_from_rows(flag_enabled=False, rows=[{"decision": aa.D_IDENTITY, "snapshot_id": SNAP}])
    assert off["state"] == "IQVIA_ADVISORY_USED"            # flag off must NOT erase recorded past use
    rows = [{"decision": aa.D_IDENTITY, "snapshot_id": SNAP, "snapshot_sha256_prefix": "aaaaaaaaaaaa",
             "assessed_at": datetime(2026, 10, 14, 9, tzinfo=timezone.utc), "assessed_by": "a@t"},
            {"decision": aa.D_CONFLICT, "snapshot_id": SNAP, "snapshot_sha256_prefix": "aaaaaaaaaaaa",
             "assessed_at": datetime(2026, 10, 14, 10, tzinfo=timezone.utc), "assessed_by": "b@t"}]
    used = aa.disclosure_from_rows(flag_enabled=True, rows=rows)
    assert used["state"] == "IQVIA_ADVISORY_USED" and used["assessments"] == 2
    assert used["by_decision"] == {aa.D_CONFLICT: 1, aa.D_IDENTITY: 1} and used["distinct_assessors"] == 2
    assert used["verification_source"] is False and "not exclusion clearance" in used["statement"]
    assert "a@t" not in repr(used)                          # no names in report text


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]


class _FakeDb:
    """Answers the catalogue probe first, then the assessment select."""

    def __init__(self, *, present=True, readable=True, rows=()):
        self.calls, self._answers = [], [_Rows([(present, readable)]), _Rows(list(rows))]

    async def execute(self, stmt, *a, **k):
        self.calls.append(str(stmt))
        return self._answers.pop(0)


def test_disclosure_reads_recorded_use_even_when_consumption_is_disabled():
    got = [(aa.D_IDENTITY, SNAP, "aaaaaaaaaaaa", datetime(2026, 10, 14, tzinfo=timezone.utc), "a@t")]
    out = run(aa.disclosure_for_intake(_FakeDb(rows=got), flag_enabled=False, intake_id=uuid.uuid4()))
    assert out["state"] == "IQVIA_ADVISORY_USED" and out["assessments"] == 1
    assert out["consumption_currently_enabled"] is False
    assert "currently disabled" in out["statement"] and "historical records of past use" in out["statement"]


def test_disclosure_for_intake_reads_rows_when_flag_on():
    got = [(aa.D_IDENTITY, SNAP, "aaaaaaaaaaaa", datetime(2026, 10, 14, tzinfo=timezone.utc), "a@t")]
    out = run(aa.disclosure_for_intake(_FakeDb(rows=got), flag_enabled=True, intake_id=uuid.uuid4()))
    assert out["state"] == "IQVIA_ADVISORY_USED" and out["assessments"] == 1
    assert "currently disabled" not in out["statement"]


def test_empty_readable_store_says_not_used_for_this_delivery():
    out = run(aa.disclosure_for_intake(_FakeDb(rows=[]), flag_enabled=False, intake_id=uuid.uuid4()))
    assert out["state"] == "IQVIA_NOT_USED" and out["assessments"] == 0


def test_unapplied_store_is_stated_not_reported_as_not_used():
    db = _FakeDb(present=False, readable=False)
    out = run(aa.disclosure_for_intake(db, flag_enabled=True, intake_id=uuid.uuid4()))
    assert out["state"] == "IQVIA_ASSESSMENT_STORE_NOT_APPLIED" and out["store"] == "NOT_APPLIED"
    assert "not present in this environment" in out["statement"] and len(db.calls) == 1  # table never selected


def test_unreadable_store_says_use_cannot_be_stated():
    db = _FakeDb(present=True, readable=False)
    out = run(aa.disclosure_for_intake(db, flag_enabled=False, intake_id=uuid.uuid4()))
    assert out["state"] == "IQVIA_ASSESSMENT_STORE_UNREADABLE" and "cannot be stated" in out["statement"]
    assert len(db.calls) == 1


def test_delivery_context_is_required_before_any_lookup_or_row(monkeypatch):
    async def boom(db, **kw):
        raise AssertionError("lookup must not run without a delivery context")
    monkeypatch.setattr(ac, "organisation_relationships", boom)
    db = Db()
    with pytest.raises(aa.AssessmentRefused) as e:
        run(aa.record_assessment(db, user=user("reviewer"), secret=SECRET, correlation_id="c1", build_sha="b" * 8,
                                 snapshot_id=SNAP, org_npi=NPI, decision=aa.D_INSUFFICIENT,
                                 rationale="synthetic rationale text", delivery_intake_id=None))
    assert e.value.code == "DELIVERY_CONTEXT_REQUIRED" and db.added == []


def _csv_rows(dataset):
    text = delivery_processing_to_csv(dataset, "RPT-1", "2026-10-14T00:00:00Z")
    return list(csv.reader(io.StringIO(text))), text


def test_csv_carries_the_actual_use_disclosure_and_no_identifiers(patch_lookup):
    patch_lookup(lookup_result())
    d = stored(patch_lookup)
    disc = aa.disclosure_from_rows(flag_enabled=True, rows=[
        {"decision": d["decision"], "snapshot_id": d["snapshot_id"], "snapshot_sha256_prefix": "aaaaaaaaaaaa",
         "assessed_at": d["assessed_at"], "assessed_by": d["assessed_by"]}])
    rows, text = _csv_rows({"iqvia_use": disc})
    flat = [c for r in rows for c in r]
    assert "## IQVIA use disclosure (supplemental; not verification, not exclusion clearance)" in flat
    assert ["Advisory assessments recorded", "1"] in rows and ["State", "IQVIA_ADVISORY_USED"] in rows
    assert NPI not in text and "SYN-O1" not in text and "reviewer@test.local" not in text


def test_csv_without_iqvia_use_says_not_used():
    rows, _ = _csv_rows({})
    assert ["State", "IQVIA_NOT_USED"] in rows and ["Advisory assessments recorded", "0"] in rows


def test_no_decision_value_can_express_clearance_or_bucket():
    assert not {"CLEAR", "CLEARED", "B1", "B4", "VERIFIED", "EXCLUSION_CLEAR"} & set(aa.DECISIONS)
    assert aa.IqviaAdvisoryAssessment.__table__.c.affects_verification.server_default.arg.text == "false"
