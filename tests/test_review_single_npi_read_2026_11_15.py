"""CodeRabbit Major on #225: the registered NPI was read three times during one review (match, probes,
classification), with awaited work between the reads; an identifier committed in a gap could change which NPI
the probes used. The registered NPI is read ONCE and that value is used throughout."""
from __future__ import annotations

import pytest
from sqlalchemy import event, select, text

from app.tefca_registry import models as reg
from app.tefca_registry import review_service as rs
from rce_traceability_support import rolled_back_db  # noqa: F401
from test_record_name_identity_db_2026_11_14 import _npi_less_record, _patch_edges  # noqa: F401

pytestmark = pytest.mark.regression


async def test_a_review_reads_the_registered_npi_exactly_once(rolled_back_db, monkeypatch):
    db = rolled_back_db
    ent, rec, _ = await _npi_less_record(db, "9.99.780.31", entity_name=str.lower)
    _patch_edges(monkeypatch)
    seen = []
    engine = db.get_bind()
    listener = lambda c, cur, stmt, p, ctx, many: seen.append(" ".join(stmt.split()))   # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        await rs.run_review(db, ent, source_record_id=rec.id)
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    reads = [s for s in seen if s.upper().startswith("SELECT") and "FROM tefca_entity_identifiers" in s
             and "identifier_type" in s and "identifier_value" in s.split("FROM")[0]]
    assert len(reads) == 1, reads


async def test_an_identifier_committed_after_the_snapshot_cannot_change_the_probes_npi(rolled_back_db, monkeypatch):
    """A new NPI appears while the review is between its match and its probes: the snapshot wins."""
    db = rolled_back_db
    ent, rec, _ = await _npi_less_record(db, "9.99.780.32", entity_name=str.lower)
    _patch_edges(monkeypatch)
    real_seed = rs.ensure_seed_rules

    async def seed_then_race(session):
        await real_seed(session)
        session.add(reg.TefcaEntityIdentifier(
            entity_id=ent.id, identifier_type="npi", identifier_value="1234567893",
            system_uri="urn:docuaction:tefca/identifier/npi", is_primary=False, identifier_status="active"))
        await session.flush()

    monkeypatch.setattr(rs, "ensure_seed_rules", seed_then_race)
    used = []
    real_probe = rs.probe_sources

    async def spy(session, entity_id, **kw):
        out = await real_probe(session, entity_id, **kw)
        used.append(out)
        return out

    monkeypatch.setattr(rs, "probe_sources", spy)
    out = await rs.run_review(db, ent, source_record_id=rec.id)
    nppes = out["verification"]["nppes"]
    assert nppes["status"] == rs.NOT_CHECKED and "no NPI identifier" in nppes["reason"], nppes
