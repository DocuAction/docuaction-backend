"""D-05 regression: the IQVIA importer must work as the RUNTIME role, not just as a superuser.

DEV, 2026-10-06 (QA case SUN-08): an upload never finished. The import worker logged
`permission denied for table source_snapshot ... UPDATE source_snapshot SET metadata=...` and the job
stayed RUNNING. Migration 20260921 grants the runtime role only SELECT, INSERT on source_snapshot, while
the importer records its reference-preflight and rejected-row count in `metadata` and corrects
`record_count` once the file has been read in full. Every other IQVIA test connects as a superuser, so
none of them could see it.

These tests switch the session to the runtime role (`SET ROLE`) before running the real importer:
  * with 20261006_snapshot_bookkeeping applied the import completes and the two bookkeeping columns
    are written;
  * at the previous head the same test fails with `permission denied for table source_snapshot`;
  * the grant is column-level: provenance (sha256, status, snapshot_label, ...) stays un-updatable, and
    there is no table-wide UPDATE and no DELETE.
Synthetic data only. Skips without a database and without the `docuaction_app` role.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import snapshot_models as sm
from rce_traceability_support import SYN, rolled_back_db  # noqa: F401
from test_iqvia_import import HCO_HEADER, write_csv

pytestmark = pytest.mark.regression

APP_ROLE = "docuaction_app"
BOOKKEEPING = {"record_count", "metadata"}


async def _require_app_role(db):
    found = (await db.execute(text("select 1 from pg_roles where rolname = :r"), {"r": APP_ROLE})).first()
    if not found:
        pytest.skip(f"role {APP_ROLE} does not exist on this database; the runtime-privilege check needs it")


async def _as_runtime_role(db):
    await _require_app_role(db)
    await db.execute(text(f'SET ROLE "{APP_ROLE}"'))


class TestImporterRunsAsTheRuntimeRole:
    async def test_import_completes_and_writes_its_bookkeeping(self, rolled_back_db, tmp_path):
        db = rolled_back_db
        f = write_csv(tmp_path / "rt.csv", HCO_HEADER, [
            [f"{SYN}-RT1", "", "", "Runtime One", "02101"],
            [f"{SYN}-RT2", "", "", "Runtime Two", "02101"],
            ["", "", "", "no key, rejected", "02101"],
        ])
        await _as_runtime_role(db)
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-runtime-role", created_by=SYN)
        await db.execute(text("RESET ROLE"))

        assert summary.rows_read == 3 and summary.rows_rejected == 1
        row = (await db.execute(text(
            "select record_count, metadata, status from source_snapshot where id = :i"),
            {"i": summary.snapshot_id})).one()
        assert row.record_count == 3, "record_count is corrected to the real total after the read"
        assert row.metadata.get("rows_rejected") == 1
        assert "reference_preflight" in row.metadata
        assert row.status == sm.SNAPSHOT_PENDING, "staging never changes the lifecycle status"


class TestTheGrantIsColumnLevelOnly:
    async def test_runtime_role_can_update_only_the_two_bookkeeping_columns(self, rolled_back_db):
        db = rolled_back_db
        await _require_app_role(db)
        cols = [r[0] for r in (await db.execute(text(
            "select attname from pg_attribute where attrelid = 'source_snapshot'::regclass "
            "and attnum > 0 and not attisdropped"))).fetchall()]
        can = {c for c in cols if (await db.execute(
            text("select has_column_privilege(:r, 'source_snapshot', :c, 'UPDATE')"),
            {"r": APP_ROLE, "c": c})).scalar()}
        assert can == BOOKKEEPING, f"runtime role can UPDATE {sorted(can)}; expected exactly {sorted(BOOKKEEPING)}"
        assert not (await db.execute(
            text("select has_table_privilege(:r, 'source_snapshot', 'UPDATE')"), {"r": APP_ROLE})).scalar()
        for priv in ("DELETE", "TRUNCATE"):
            assert not (await db.execute(
                text("select has_table_privilege(:r, 'source_snapshot', :p)"),
                {"r": APP_ROLE, "p": priv})).scalar(), priv

    @pytest.mark.parametrize("column,value", [("sha256", "'x'"), ("status", "'APPROVED'"),
                                              ("snapshot_label", "'renamed'")])
    async def test_provenance_columns_stay_immutable_to_the_runtime_role(self, rolled_back_db, tmp_path,
                                                                       column, value):
        db = rolled_back_db
        f = write_csv(tmp_path / "imm.csv", HCO_HEADER, [[f"{SYN}-IMM1", "", "", "Immutable", "02101"]])
        summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-immutable", created_by=SYN)
        await _as_runtime_role(db)
        await db.execute(text("SAVEPOINT before_denied"))
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text(f"update source_snapshot set {column} = {value} where id = :i"),
                             {"i": summary.snapshot_id})
        await db.execute(text("ROLLBACK TO SAVEPOINT before_denied"))
        await db.execute(text("RESET ROLE"))
