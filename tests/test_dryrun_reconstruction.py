"""The historical reconstruction tool: classifier fixtures (no database), the
refusal gates, and proof that --dry-run writes nothing.

Reconstructed dispositions are a records decision. What is pinned here is that
the tool never overstates its evidence - every proposed field is classified as
directly evidenced, deterministically recomputed, inferred or unavailable - and
that the default mode cannot write.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dryrun_reconstruct_dispositions.py"


def _load():
    spec = importlib.util.spec_from_file_location("dryrun_reconstruct_dispositions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
WINDOW = (T0, T0 + timedelta(minutes=10))


def _source(**kw):
    base = {"id": uuid.uuid4(), "line_number": 2, "parse_status": "ok",
            "promotion_status": "promoted", "canonical_entity_id": None}
    base.update(kw)
    return base


def _curated(**kw):
    base = {"id": uuid.uuid4(), "record_status": "CLEAN", "canonical_entity_id": None,
            "rce_org_oid": "9.99.999.1.1", "name": "SYNTHETIC ORG", "is_test_record": False,
            "status_reason": None, "promoted_at": T0 + timedelta(minutes=3)}
    base.update(kw)
    return base


class TestClassifier:
    def setup_method(self):
        self.m = _load()

    def test_unparseable_line_is_rejected_from_the_parse_status_column(self):
        p = self.m.propose(_source(parse_status="field_count_mismatch"), None, None, [], WINDOW)
        assert p["disposition"] == "REJECTED" and p["reason_code"] == "REJECTED_UNPARSEABLE"
        assert p["fields"]["disposition"] == "directly_evidenced"
        assert p["fields"]["reason_code"] == "deterministically_recomputed"
        assert p["fields"]["reason"] == "unavailable"
        assert p["confidence"] == "high"

    def test_missing_curated_row_is_unavailable_not_guessed(self):
        p = self.m.propose(_source(), None, None, [], WINDOW)
        assert p["disposition"] is None
        assert p["fields"]["disposition"] == "unavailable"
        assert "never ran" in p["basis"][0]

    def test_held_record_reads_the_status_but_infers_the_reason(self):
        p = self.m.propose(_source(promotion_status="held"),
                           _curated(record_status="HELD", status_reason="NPI-003 checksum"),
                           None, [], WINDOW)
        assert p["disposition"] == "HELD" and p["reason_code"] == "HELD_QUALITY_ISSUE"
        assert p["fields"]["disposition"] == "directly_evidenced"
        assert p["fields"]["reason_code"] == "inferred"
        conflict = self.m.propose(_source(), _curated(record_status="HELD",
                                                      status_reason="identifier conflict NPI-008"),
                                  None, [], WINDOW)
        assert conflict["reason_code"] == "HELD_IDENTIFIER_CONFLICT"

    def test_test_record_is_excluded_when_the_profile_excludes_it(self):
        p = self.m.propose(_source(), _curated(is_test_record=True), None, [], WINDOW)
        assert p["disposition"] == "EXCLUDED" and p["fields"]["disposition"] == "directly_evidenced"
        kept = self.m.propose(_source(), _curated(is_test_record=True), None, [], WINDOW,
                              profile_excludes_test_records=False)
        assert kept["disposition"] != "EXCLUDED"

    def test_no_oid_or_name_is_missing_key_deterministically(self):
        for missing in ({"rce_org_oid": ""}, {"name": None}):
            p = self.m.propose(_source(), _curated(**missing), None, [], WINDOW)
            assert p["disposition"] == "MISSING_KEY"
            assert p["fields"]["disposition"] == "deterministically_recomputed"

    def test_entity_created_inside_the_job_window_is_created(self):
        eid = uuid.uuid4()
        p = self.m.propose(_source(), _curated(canonical_entity_id=eid),
                           {"id": eid, "created_at": T0 + timedelta(minutes=4)}, [], WINDOW)
        assert p["disposition"] == "CREATED" and p["entity_id"] == str(eid)
        assert p["fields"]["disposition"] == "deterministically_recomputed"
        assert p["fields"]["entity_id"] == "directly_evidenced"

    def test_existing_entity_with_a_version_in_window_is_updated_inferred(self):
        eid = uuid.uuid4()
        versions = [{"created_at": T0 + timedelta(minutes=5),
                     "snapshot_data": {"address_city": "Newtown", "name": "X"}}]
        p = self.m.propose(_source(), _curated(canonical_entity_id=eid),
                           {"id": eid, "created_at": T0 - timedelta(days=30)}, versions, WINDOW)
        assert p["disposition"] == "UPDATED" and p["fields"]["disposition"] == "inferred"
        assert p["changed_fields"] == ["address_city", "name"]
        assert p["fields"]["changed_fields"] == "inferred"

    def test_existing_entity_without_a_version_is_matched_unchanged_low_confidence(self):
        eid = uuid.uuid4()
        p = self.m.propose(_source(), _curated(canonical_entity_id=eid),
                           {"id": eid, "created_at": T0 - timedelta(days=30)},
                           [{"created_at": T0 - timedelta(days=2), "snapshot_data": {}}], WINDOW)
        assert p["disposition"] == "MATCHED_UNCHANGED" and p["confidence"] == "low"
        assert p["fields"]["disposition"] == "inferred"
        assert p["fields"]["changed_fields"] == "unavailable"

    def test_promoted_record_whose_entity_is_gone_is_unavailable(self):
        eid = uuid.uuid4()
        p = self.m.propose(_source(), _curated(canonical_entity_id=eid), None, [], WINDOW)
        assert p["disposition"] is None and p["entity_id"] == str(eid)
        assert p["fields"]["disposition"] == "unavailable"

    def test_clean_record_never_promoted_is_unavailable(self):
        p = self.m.propose(_source(promotion_status="pending"), _curated(), None, [], WINDOW)
        assert p["disposition"] is None and "promotion did not reach" in p["basis"][0]

    def test_without_a_job_window_the_promotion_time_is_the_anchor(self):
        eid = uuid.uuid4()
        near = self.m.propose(_source(), _curated(canonical_entity_id=eid),
                              {"id": eid, "created_at": T0 + timedelta(minutes=3, seconds=30)},
                              [], None)
        assert near["disposition"] == "CREATED" and near["fields"]["disposition"] == "inferred"
        far = self.m.propose(_source(), _curated(canonical_entity_id=eid),
                             {"id": eid, "created_at": T0 - timedelta(days=1)}, [], None)
        assert far["disposition"] == "MATCHED_UNCHANGED"
        blind = self.m.propose(_source(), _curated(canonical_entity_id=eid, promoted_at=None),
                               {"id": eid, "created_at": T0 - timedelta(days=1)}, [], None)
        assert blind["disposition"] is None

    def test_summary_totals_equation_and_limitations(self):
        eid = uuid.uuid4()
        proposals = [
            self.m.propose(_source(parse_status="bad"), None, None, [], WINDOW),
            self.m.propose(_source(), _curated(canonical_entity_id=eid),
                           {"id": eid, "created_at": T0 + timedelta(minutes=1)}, [], WINDOW),
            self.m.propose(_source(), None, None, [], WINDOW),
        ]
        s = self.m.summarise(proposals, received=3)
        assert s["totals"]["REJECTED"] == 1 and s["totals"]["CREATED"] == 1
        assert s["totals"]["unavailable"] == 1 and s["totals"]["total"] == 2
        assert s["equation"]["holds"] is False and s["equation"]["difference"] == 1
        assert s["classification_summary"]["disposition"]["unavailable"] == 1
        assert any("cannot close" in item for item in s["limitations"])
        assert any("reason text is never reconstructed" in item for item in s["limitations"])

    def test_equation_matches_the_application_definition(self):
        from app.tefca_registry.rce import dispositions

        counts = {"CREATED": 3, "UPDATED": 1, "HELD": 2}
        assert self.m.equation(counts, 6) == dispositions.equation(counts, 6)
        assert self.m.DISPOSITIONS == dispositions.tm.DISPOSITIONS

    def test_shared_host_detection(self):
        assert self.m.is_shared_host("postgresql://u:p@docuaction-db-geo.postgres.database.azure.com/db")
        assert self.m.is_shared_host("postgresql+asyncpg://u:p@10.0.0.5:5432/db")
        assert not self.m.is_shared_host("postgresql+asyncpg://test:test@127.0.0.1:5499/test")
        assert not self.m.is_shared_host("postgresql://x@localhost/db")


class TestRefusals:
    """The write gates refuse BEFORE opening a database connection."""

    def _run(self, *args):
        env = dict(os.environ, DATABASE_URL="postgresql://nobody:x@shared.invalid:5432/db",
                   SECRET_KEY="t" * 64, ALLOWED_HOSTS="*")
        return subprocess.run([sys.executable, str(SCRIPT), "--intake-id", str(uuid.uuid4()), *args],
                              cwd=REPO, env=env, capture_output=True, text=True, timeout=120)

    def test_write_without_approval_is_refused(self):
        r = self._run("--write")
        assert r.returncode == 1 and "--approved-by" in r.stderr

    def test_write_against_a_shared_host_is_refused_without_allow_shared(self):
        r = self._run("--write", "--approved-by", "Name, Role", "--approval-ref", "T-1")
        assert r.returncode == 1 and "shared" in r.stderr and "--allow-shared" in r.stderr

    def test_source_never_updates_or_deletes(self):
        body = SCRIPT.read_text(encoding="utf-8").lower()
        for banned in ("update rce_", "delete from", "truncate"):
            assert banned not in body, banned
        assert "set transaction read only" in body
        assert "postgresql_readonly" in body
        assert "reconstructed" in body and "'reconstruction'" in body.replace('"', "'")


# ── with a database: --dry-run writes nothing ────────────────────────────────

def _counts(conn):
    from sqlalchemy import text
    return tuple(conn.execute(text(
        "select (select count(*) from rce_disposition_events), "
        "(select count(*) from rce_reconciliation_snapshots), "
        "(select count(*) from rce_source_records), (select count(*) from audit_logs)")).first())


def test_dry_run_cli_writes_nothing(db_required):
    """Real subprocess, real read-only connection, an intake id that does not
    exist: exit 0, a JSON report saying so, and every row count unchanged."""
    import sqlalchemy as sa

    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        before = _counts(conn)
    r = subprocess.run([sys.executable, str(SCRIPT), "--intake-id", str(uuid.uuid4()), "--dry-run"],
                       cwd=REPO, env=dict(os.environ, SECRET_KEY="t" * 64, ALLOWED_HOSTS="*"),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-800:]
    payload = json.loads(r.stdout)
    assert payload["mode"] == "dry-run" and payload["writes_performed"] == 0
    assert payload["found"] is False and payload["records"] == 0
    with engine.connect() as conn:
        after = _counts(conn)
    engine.dispose()
    assert after == before


def test_dry_run_analysis_proposes_every_record_and_writes_nothing(db_required):
    """The read path against a synthetic delivery inside a transaction that is
    rolled back: one proposal per received record, totals and equation
    present, and the evidence tables untouched by the analysis."""
    import hashlib

    import sqlalchemy as sa
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    module = _load()
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    engine = sa.create_engine(url)
    connection = engine.connect()
    outer = connection.begin()
    try:
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        label = f"SYNTHETIC-DRYRUN-{uuid.uuid4().hex[:6]}"
        intake_id = uuid.uuid4()
        now = datetime.utcnow().replace(microsecond=0)
        session.add(m.RceSourceIntake(
            id=intake_id, delivery_label=label, original_filename="dryrun.psv",
            storage_path="(synthetic)", sha256=hashlib.sha256(label.encode()).hexdigest(),
            file_size_bytes=10, headers=["id", "name"], schema_fingerprint="f" * 64,
            record_count=3, received_by=label, status="PROCESSED"))
        session.flush()
        recs = []
        for i, (oid, name, status) in enumerate((("9.99.999.5.1", "ONE", "ok"),
                                                 ("9.99.999.5.2", "TWO", "ok"),
                                                 ("", "", "field_count_mismatch")), start=2):
            rec = m.RceSourceRecord(id=uuid.uuid4(), source_intake_id=intake_id, line_number=i,
                                    raw_line=f"{oid}|{name}", parsed={"id": oid, "name": name},
                                    record_sha256=hashlib.sha256(f"{oid}|{name}".encode()).hexdigest(),
                                    source_rce_id=oid or None, field_count=2, parse_status=status,
                                    promotion_status="promoted" if status == "ok" else "excluded")
            session.add(rec)
            recs.append(rec)
        entity = reg.TefcaRegEntity(id=uuid.uuid4(), name=f"{label} ONE", entity_level="participant",
                                    entity_type="provider", created_at=now - timedelta(days=3))
        session.add(entity)
        session.flush()
        session.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id,
                                       source_record_id=recs[0].id, record_status="CLEAN",
                                       rce_org_oid="9.99.999.5.1", name=f"{label} ONE",
                                       transformation_version="1.0.0",
                                       canonical_entity_id=entity.id, promoted_at=now))
        session.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id,
                                       source_record_id=recs[1].id, record_status="HELD",
                                       status_reason="NPI-003", rce_org_oid="9.99.999.5.2",
                                       name=f"{label} TWO", transformation_version="1.0.0"))
        session.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id,
                                       source_record_id=recs[2].id, record_status="REJECTED",
                                       transformation_version="1.0.0"))
        session.commit()

        before = _counts(connection)
        result = module.analyse(connection, str(intake_id))
        assert result["found"] is True and result["records"] == 3
        assert len(result["proposals"]) == 3
        by_line = {p["line_number"]: p for p in result["proposals"]}
        assert by_line[2]["disposition"] == "MATCHED_UNCHANGED"     # no job window, entity older
        assert by_line[3]["disposition"] == "HELD"
        assert by_line[4]["disposition"] == "REJECTED"
        assert result["totals"]["total"] == 3 and result["equation"]["holds"] is True
        assert result["job_id"] is None and result["existing_disposition_events"] == 0
        assert isinstance(result["limitations"], list) and result["limitations"]
        assert _counts(connection) == before, "analysis must not write"
        assert connection.execute(text(
            "select count(*) from rce_disposition_events where intake_id = cast(:i as uuid)"),
            {"i": str(intake_id)}).scalar() == 0
    finally:
        outer.rollback()
        connection.close()
        engine.dispose()
