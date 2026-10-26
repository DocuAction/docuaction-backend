"""Route-level checks for Case Management (TEFCA ARC) — PR #176, added 2026-10-26.

GET /api/tefca/arc/cases: role floor, the stated trailing window, pass-through of the exact
identifier to the ONE existing queue (`supervisor_ops.work_queue`), refusal mapping.
POST /api/tefca/arc/cases/import/{preview,apply}: role floors and the apply pre-conditions that
must refuse BEFORE any file is staged or any row is read.

Database-backed (skips without DATABASE_URL; CI job `isolation-postgres` runs it): the three
identifier resolution paths — identifier row, entity family column, curated-record NPI — and
the created_at window, with two entities sharing one NPI returned as two rows (nothing merged).
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta

import pytest

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import RCE_FIELDS
from rce_traceability_support import (  # noqa: F401  (fixture registered by import)
    curated_direct, rolled_back_db, seed_entity, seed_intake)
from test_rbac_roles import _Session, _User

CASES = "/api/tefca/arc/cases"
PREVIEW = "/api/tefca/arc/cases/import/preview"
APPLY = "/api/tefca/arc/cases/import/apply"
SYN = "SYNTHETIC-CASES-2026-10-26"


@pytest.fixture
def as_role(client):
    """(role) -> caller(method, path, **kw). In-memory user + session, real signed token."""
    installed = []

    def _make(role):
        user = _User(role)

        async def _override():
            yield _Session(user)

        app.dependency_overrides[get_db] = _override
        installed.append(get_db)
        token = create_access_token({"sub": user.id, "role": role})

        def call(method, path, **kw):
            kw.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
            return client.request(method, path, **kw)

        return call

    yield _make
    for dep in installed:
        app.dependency_overrides.pop(dep, None)


@pytest.fixture
def captured_queue(monkeypatch):
    """Replace the queue with a recorder; the route must call it with the window it states."""
    from app.tefca_registry import supervisor_ops

    calls = []

    async def fake_work_queue(db, **kw):
        calls.append(kw)
        return {"items": [], "total": 0, "offset": kw.get("offset", 0), "limit": kw.get("limit", 50),
                "returned": 0, "has_more": False, "sort": kw.get("sort")}

    monkeypatch.setattr(supervisor_ops, "work_queue", fake_work_queue)
    return calls


# ── GET /cases ───────────────────────────────────────────────────────────────

def test_cases_requires_a_signed_in_user(client, captured_queue):
    assert client.get(CASES).status_code == 401
    assert captured_queue == []


def test_viewer_may_list_cases_and_the_window_is_stated_not_implied(as_role, captured_queue):
    r = as_role("viewer")("GET", f"{CASES}?identifier=1234567893&as_of=2026-10-25")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(captured_queue) == 1
    kw = captured_queue[0]
    created_to = datetime(2026, 10, 25, 23, 59, 59)
    assert kw["identifier"] == "1234567893"
    assert kw["created_to"] == created_to
    assert kw["created_from"] == created_to - timedelta(days=round(12 * 30.4))   # default 12 months
    assert body["window"] == {"as_of": "2026-10-25", "months": 12,
                              "created_from": kw["created_from"].isoformat(),
                              "created_to": created_to.isoformat(),
                              "applies_to": "review_records.created_at"}
    assert body["identifier"] == "1234567893"
    assert "never a determination" in body["labels"]["bucket"]
    assert "not stored" in body["labels"]["next_action_follow_up"]


def test_months_is_honoured_and_bounded(as_role, captured_queue):
    call = as_role("viewer")
    assert call("GET", f"{CASES}?as_of=2026-10-25&months=3").status_code == 200
    assert captured_queue[-1]["created_from"] == datetime(2026, 10, 25, 23, 59, 59) - timedelta(days=round(3 * 30.4))
    n = len(captured_queue)
    assert call("GET", f"{CASES}?months=0").status_code == 422
    assert call("GET", f"{CASES}?months=37").status_code == 422
    assert len(captured_queue) == n, "a rejected window must never reach the queue"


def test_queue_refusal_is_a_400_not_a_500(as_role, monkeypatch):
    from app.tefca_registry import supervisor_ops

    async def refusing(db, **kw):
        raise supervisor_ops.SupervisorRefused("sort must be one of ...")

    monkeypatch.setattr(supervisor_ops, "work_queue", refusing)
    r = as_role("viewer")("GET", f"{CASES}?sort=bogus")
    assert r.status_code == 400 and "sort" in r.text


# ── import routes: role floors and pre-conditions ────────────────────────────

def _upload():
    return {"file": ("case_updates.csv", io.BytesIO(b"review_id,assign_to_email\r\n"), "text/csv")}


def test_preview_needs_reviewer_and_apply_needs_senior_analyst(as_role):
    assert as_role("viewer")("POST", f"{PREVIEW}?purpose=case_updates", files=_upload()).status_code == 403
    assert as_role("reviewer")("POST", f"{APPLY}?purpose=case_updates&confirm=true", files=_upload()).status_code == 403
    assert as_role("manager")("POST", f"{APPLY}?purpose=case_updates&confirm=true", files=_upload()).status_code == 403


def test_apply_refuses_without_confirm_and_for_other_purposes_before_staging(as_role, monkeypatch):
    from app.tefca_registry.rce import case_update_import as cui

    async def must_not_run(*a, **k):  # pragma: no cover - the assertion is that it is never reached
        raise AssertionError("build_preview must not run when the pre-conditions fail")

    monkeypatch.setattr(cui, "build_preview", must_not_run)
    call = as_role("senior_analyst")
    r = call("POST", f"{APPLY}?purpose=case_updates", files=_upload())
    assert r.status_code == 400 and "confirm=true" in r.text
    r = call("POST", f"{APPLY}?purpose=source_delivery&confirm=true", files=_upload())
    assert r.status_code == 400 and "only purpose=case_updates" in r.text
    r = call("POST", f"{APPLY}?purpose=reference_data&confirm=true", files=_upload())
    assert r.status_code == 400


# ── database: identifier resolution paths + window ───────────────────────────

def _row(**over):
    base = {f: "" for f in RCE_FIELDS}
    base.update({"sequoiaorgtype": "Participant", "active": "1", "partOf": "", "address_line": "1 Synthetic Way",
                 "address_city": "Testville", "address_state": "MA", "address_postalCode": "02101",
                 "address_country": "US", "purposesofuse": "T-TRTMNT"})
    base.update(over)
    return base


async def _case(db, entity_id, review_id, created_at):
    db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id=review_id, entity_id=entity_id,
                            source_record_id=uuid.uuid4(), created_at=created_at,
                            verification_results={"queue_source": "TEST", "priority": 50}))
    await db.flush()


class TestIdentifierResolutionPaths:
    NPI_ROW = "1234567893"       # path 1: identifier row
    HCID = f"{SYN}-HCID-FAMILY"  # path 2: entity family column, no identifier row
    NPI_CURATED = "1467865563"   # path 3: curated-record NPI only (withheld identifier row)

    async def _seed(self, db):
        from app.tefca_registry.supervisor_ops import work_queue  # noqa: F401  (import check)
        now = datetime.utcnow()
        a = await seed_entity(db, oid=f"{SYN}.1", name=f"{SYN} A", npi=self.NPI_ROW)
        b_id = uuid.uuid4()
        db.add(reg.TefcaRegEntity(id=b_id, name=f"{SYN} B", display_name=f"{SYN} B", entity_level="participant",
                                  entity_type="provider", operational_status="active",
                                  verification_status="not_verified", current_version=1, is_active=True,
                                  rce_hcid=self.HCID, rce_org_oid=f"{SYN}.2", is_test_record=False, rce_attributes={}))
        c_id = uuid.uuid4()
        db.add(reg.TefcaRegEntity(id=c_id, name=f"{SYN} C", display_name=f"{SYN} C", entity_level="participant",
                                  entity_type="provider", operational_status="active",
                                  verification_status="not_verified", current_version=1, is_active=True,
                                  rce_org_oid=f"{SYN}.3", is_test_record=False, rce_attributes={}))
        c2_id = uuid.uuid4()   # shares the curated NPI with C: two rows, never merged
        db.add(reg.TefcaRegEntity(id=c2_id, name=f"{SYN} C2", display_name=f"{SYN} C2", entity_level="participant",
                                  entity_type="provider", operational_status="active",
                                  verification_status="not_verified", current_version=1, is_active=True,
                                  rce_org_oid=f"{SYN}.4", is_test_record=False, rce_attributes={}))
        await db.flush()
        rows = [_row(id=f"{SYN}.3", name=f"{SYN} C", NPI=self.NPI_CURATED, HCID=f"{SYN}-H3", TEFCAID=f"{SYN}-T3"),
                _row(id=f"{SYN}.4", name=f"{SYN} C2", NPI=self.NPI_CURATED, HCID=f"{SYN}-H4", TEFCAID=f"{SYN}-T4")]
        intake_id, _ = await seed_intake(db, rows, with_job=False)
        await curated_direct(db, intake_id, rows, overrides={1: {"canonical_entity_id": c_id},
                                                                2: {"canonical_entity_id": c2_id}})
        await _case(db, a, "REV-9100-000001", now - timedelta(days=3))
        await _case(db, b_id, "REV-9100-000002", now - timedelta(days=3))
        await _case(db, c_id, "REV-9100-000003", now - timedelta(days=3))
        await _case(db, c2_id, "REV-9100-000004", now - timedelta(days=3))
        await _case(db, a, "REV-9100-000005", now - timedelta(days=400))   # same entity, outside 12 months
        return now

    @staticmethod
    def _ids(page):
        return sorted(i["review_id"] for i in page["items"])

    async def test_identifier_row_family_column_and_curated_npi_all_resolve(self, rolled_back_db):
        from app.tefca_registry.supervisor_ops import work_queue
        db = rolled_back_db
        now = await self._seed(db)
        window = dict(created_from=now - timedelta(days=365), created_to=now + timedelta(seconds=1))
        assert self._ids(await work_queue(db, identifier=self.NPI_ROW, **window)) == ["REV-9100-000001"]
        assert self._ids(await work_queue(db, identifier=self.HCID, **window)) == ["REV-9100-000002"]
        shared = await work_queue(db, identifier=self.NPI_CURATED, **window)
        assert self._ids(shared) == ["REV-9100-000003", "REV-9100-000004"], "two entities, two rows, no merge"
        assert shared["total"] == 2
        assert self._ids(await work_queue(db, identifier=f"{SYN}.2", **window)) == ["REV-9100-000002"]  # org OID

    async def test_identifier_is_exact_and_the_window_applies_to_created_at(self, rolled_back_db):
        from app.tefca_registry.supervisor_ops import work_queue
        db = rolled_back_db
        now = await self._seed(db)
        # prefix of a real NPI: exact match only, nothing returned
        assert self._ids(await work_queue(db, identifier=self.NPI_ROW[:6])) == []
        # no window: both of A's cases; 12-month window: only the recent one
        assert self._ids(await work_queue(db, identifier=self.NPI_ROW)) == ["REV-9100-000001", "REV-9100-000005"]
        inside = await work_queue(db, identifier=self.NPI_ROW, created_from=now - timedelta(days=365),
                                  created_to=now + timedelta(seconds=1))
        assert self._ids(inside) == ["REV-9100-000001"]
