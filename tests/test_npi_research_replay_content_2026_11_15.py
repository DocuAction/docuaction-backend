"""CodeRabbit on #233 (reproduced): the idempotency key omits match_rationale and change_reason, so a repeated request that
changed ONLY one of them was answered `already_present` with the OLD row - the client believed its new text was saved.
An exact replay stays idempotent; a changed payload is refused. Synthetic data, disposable database."""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.tefca_registry.rce import supplemental_research as sr
from app.tefca_registry.rce.supplemental_research_models import RceSupplementalNpiResearch as R
from rce_traceability_support import rolled_back_db  # noqa: F401
from test_npi_research_review_fixes_2026_11_15 import SECOND_NPI, _record, _seed

pytestmark = pytest.mark.regression


async def _count(db, intake_id):
    return (await db.execute(select(text("count(*)")).select_from(R).where(R.source_intake_id == intake_id))).scalar()


async def test_a_replay_with_a_changed_rationale_is_refused_not_answered_with_the_old_row(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.783.01", n=2)
    rec = recs[0]
    first = await _record(db, intake_id, rec)
    changed = "A DIFFERENT identity-match rationale that the client believes was saved by this request"
    with pytest.raises(sr.ResearchRefused):
        await _record(db, intake_id, rec, match_rationale=changed)
    same = await _record(db, intake_id, rec)
    assert same["status"] == "already_present" and same["entry"]["id"] == first["entry"]["id"]
    assert await _count(db, intake_id) == 1


async def test_a_replay_of_an_amend_with_a_changed_reason_is_refused(rolled_back_db):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.783.02", n=2)
    rec = recs[0]
    root = (await _record(db, intake_id, rec))["entry"]
    kw = dict(action="AMEND", researched_npi=SECOND_NPI, supersedes_id=root["id"], change_reason="the first lookup matched the wrong organization")
    amended = await _record(db, intake_id, rec, **kw)
    with pytest.raises(sr.ResearchRefused):
        await _record(db, intake_id, rec, **{**kw, "change_reason": "a different reason the client expects to be stored"})
    again = await _record(db, intake_id, rec, **kw)
    assert again["status"] == "already_present" and again["entry"]["id"] == amended["entry"]["id"]
    assert await _count(db, intake_id) == 2
