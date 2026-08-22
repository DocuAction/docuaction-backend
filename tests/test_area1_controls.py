"""
Area 1 evidence controls (B1 / Phase 4).

WHAT THESE PIN
──────────────
That the emitted grants would not break promotion, that the immutability probe
can tell a correctly-hardened database from an unhardened one, and that any
write touching delivered evidence is recorded.

WHAT THEY DELIBERATELY DO NOT DO
No grant is applied and no WORM retention is configured. Applying the grants
requires transferring table ownership off the application role — a superuser
action this deployment's credentials do not include — and the retention period
is Decision D8, unresolved. Both are reported, not forced.
"""

from __future__ import annotations

import pytest

from app.tefca_registry.rce.repository import (
    IMMUTABLE_EVIDENCE_COLUMNS,
    IMMUTABLE_TABLES,
    MUTABLE_WORKFLOW_COLUMNS,
    OWNER_ROLE,
    immutability_grants_sql,
)


# ── the grants ───────────────────────────────────────────────────────────────

def test_grants_transfer_ownership_before_revoking():
    """A REVOKE against the table OWNER is self-reversible.

    `docuaction` currently owns both Area 1 tables, and a PostgreSQL owner may
    re-GRANT to itself at any time. Without the ownership transfer the revoke
    guards against an accidental code path but not against intent.
    """
    statements = immutability_grants_sql()
    alters = [s for s in statements if s.startswith("ALTER TABLE")]
    revokes = [s for s in statements if s.startswith("REVOKE")]
    assert len(alters) == len(IMMUTABLE_TABLES)
    assert all(OWNER_ROLE in s for s in alters)
    # Ownership must move FIRST.
    assert statements.index(alters[-1]) < statements.index(revokes[0])


def test_grants_do_not_break_promotion():
    """The two workflow columns keep an UPDATE grant.

    A blanket REVOKE UPDATE on rce_source_records breaks `promote_delivery`,
    which writes promotion_status and canonical_entity_id on 23,562 rows AFTER
    the entities are already committed — failing mid-transaction and leaving
    Area 1 markers out of step with Area 2.
    """
    statements = immutability_grants_sql()
    column_grants = [s for s in statements if s.startswith("GRANT UPDATE (")]
    assert len(column_grants) == 1
    grant = column_grants[0]
    for column in MUTABLE_WORKFLOW_COLUMNS:
        assert column in grant, f"{column} must remain writable or promotion breaks"
    assert "rce_source_records" in grant


def test_grants_do_not_make_any_evidence_column_writable():
    grant = next(s for s in immutability_grants_sql() if s.startswith("GRANT UPDATE ("))
    inside = grant.split("(", 1)[1].split(")", 1)[0]
    granted = {c.strip() for c in inside.split(",")}
    assert granted == set(MUTABLE_WORKFLOW_COLUMNS)
    assert not (granted & set(IMMUTABLE_EVIDENCE_COLUMNS))


def test_grants_revoke_truncate_as_well_as_update_and_delete():
    """TRUNCATE bypasses row-level protections entirely."""
    for statement in immutability_grants_sql():
        if statement.startswith("REVOKE"):
            assert "TRUNCATE" in statement


def test_evidence_and_workflow_columns_are_disjoint():
    assert not (set(IMMUTABLE_EVIDENCE_COLUMNS) & set(MUTABLE_WORKFLOW_COLUMNS))


def test_owner_role_is_not_the_application_role():
    assert OWNER_ROLE != "docuaction"


# ── the immutability probe ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_reports_column_level_truth(db_required):
    """`has_table_privilege(..., 'UPDATE')` is TRUE if ANY column is writable.

    Without a column-level probe, the correctly-hardened configuration — UPDATE
    granted on the two workflow columns only — would be reported as unenforced
    and be indistinguishable from no enforcement at all.
    """
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.repository import verify_immutable

    async with async_session_maker() as session:
        result = await verify_immutable(session)

    if not result.get("checked"):
        pytest.skip("privilege probe unavailable on this deployment")

    records = result["tables"].get("rce_source_records", {})
    assert "updatable_columns" in records, "the probe must report per column"
    assert "evidence_columns_writable" in records
    assert "workflow_columns_writable" in records
    # `immutable` must be driven by EVIDENCE columns, not by the table-level flag.
    assert records["immutable"] == (
        not records["evidence_columns_writable"] and not records.get("can_delete"))


@pytest.mark.asyncio
async def test_probe_reports_unenforced_honestly(db_required):
    """An unhardened database must say so, not quietly pass."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.repository import verify_immutable

    async with async_session_maker() as session:
        result = await verify_immutable(session)
    if not result.get("checked"):
        pytest.skip("privilege probe unavailable on this deployment")
    if result["enforced"] is False:
        assert result.get("note"), "an unenforced result must explain itself"
        assert "immutability_grants_sql" in result["note"]


# ── the mutation audit trigger ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evidence_edit_is_audited_and_promotion_write_is_not(db_required):
    """The exemption is by COLUMN, not by role.

    A role-based exemption would stop recording precisely when the application
    is the thing doing something wrong. Both cases are exercised inside a
    transaction that is rolled back, so no evidence is modified.
    """
    import sqlalchemy as sa
    from app.core.database import async_session_maker

    async with async_session_maker() as session:
        exists = (await session.execute(sa.text(
            "select count(*) from information_schema.tables "
            "where table_name = 'area1_mutation_log'"))).scalar()
        if not exists:
            pytest.skip("area1_mutation_log not present on this deployment")

        row_id = (await session.execute(
            sa.text("select id from rce_source_records limit 1"))).scalar()
        if row_id is None:
            pytest.skip("no Area 1 rows to exercise the trigger against")

        try:
            # A promotion-marker write must NOT be audited.
            await session.execute(sa.text(
                "update rce_source_records set promotion_status = promotion_status "
                "where id = :i"), {"i": row_id})
            audited = (await session.execute(
                sa.text("select count(*) from area1_mutation_log"))).scalar()
            assert audited == 0, "the column filter must exempt the promotion marker"

            # An evidence edit MUST be audited, with both images.
            await session.execute(sa.text(
                "update rce_source_records set raw_line = raw_line || 'X' "
                "where id = :i"), {"i": row_id})
            entry = (await session.execute(sa.text(
                "select operation, table_name, before_image is not null as b, "
                "after_image is not null as a from area1_mutation_log"
            ))).mappings().first()
            assert entry is not None, "an edit to raw_line must be recorded"
            assert entry["operation"] == "UPDATE"
            assert entry["table_name"] == "rce_source_records"
            assert entry["b"] and entry["a"], "both images must be captured"
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_area1_rows_cannot_be_deleted_while_referenced(db_required):
    """Referential integrity already blocks Area 1 deletion.

    Every source record is referenced by an issue or a curated record, so a
    DELETE fails on the foreign key before the trigger is even reached. The
    trigger remains installed as defence in depth for any row that is not.
    """
    import sqlalchemy as sa
    from app.core.database import async_session_maker

    async with async_session_maker() as session:
        unreferenced = (await session.execute(sa.text("""
            select r.id from rce_source_records r
            left join rce_issues i on i.source_record_id = r.id
            left join rce_curated_records c on c.source_record_id = r.id
            where i.id is null and c.id is null limit 1"""))).scalar()
        row_id = (await session.execute(
            sa.text("select id from rce_source_records limit 1"))).scalar()
        if row_id is None:
            pytest.skip("no Area 1 rows present")
        if unreferenced is not None:
            pytest.skip("an unreferenced Area 1 row exists; FK protection is partial")
        try:
            with pytest.raises(Exception) as exc:
                await session.execute(
                    sa.text("delete from rce_source_records where id = :i"),
                    {"i": row_id})
            assert "foreign key" in str(exc.value).lower()
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_area1_hashes_still_verify(db_required):
    """23,566 / 23,566. Nothing in Phases 1-4 may touch delivered evidence."""
    import sqlalchemy as sa
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.repository import verify_record_hashes

    async with async_session_maker() as session:
        intake_id = (await session.execute(
            sa.text("select id from rce_source_intakes limit 1"))).scalar()
        if intake_id is None:
            pytest.skip("no delivery ingested on this deployment")
        result = await verify_record_hashes(session, intake_id)
    assert result["mismatches"] == 0
    assert result["records_checked"] > 0
    assert result["intact"] is True
