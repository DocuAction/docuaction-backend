"""IQVIA Release-1 observation tables (HCO, HCP, affiliation).

Revision ID: 20261002_iqvia_observations
Revises: 20260930_alembic_version_read
Create Date: 2026-10-02

WHY THIS EXISTS
---------------
`docs/architecture/iqvia_release1_schema_proposal.md` (2026-09-20) proposed
these three tables but could not create them: the licensed IQVIA OneKey file
specification, samples and a data-use approval were not yet in hand. They
have since been received and locally profiled (read-only; no licensed row
values leave the analysis -- see
qa-evidence/2026-10-02-sam-trace-and-reporting-plan/MORNING-CHECKPOINT.md).
This migration creates exactly the three tables the proposal specified, with
the column names the schema proposal called placeholders now filled in from
the confirmed real layout:

    iqvia_hco_observation          DEMOGRAPHIC extract (organisation-level;
                                   `HCO_HCE_ID` is IQVIA's own internal key,
                                   `ORG_NPI`/`ORG_CCN_ID` lifted for matching)
    iqvia_hcp_observation          HCP_ADDR extract (individual-practitioner,
                                   one row per address; `HCP_HCE_ID` internal
                                   key, `NPI` lifted for matching)
    iqvia_affiliation_observation  HCP<->HCO link. NO DATA DELIVERED FOR THIS
                                   EXTRACT (confirmed: only the layout file was
                                   received; HCP_ADDR's own inline
                                   HOSP_AFFIL_1..5 slots are 0% populated in
                                   the delivered file). Created now so the
                                   importer and matcher have somewhere to
                                   write to the day a populated affiliation
                                   file arrives -- not populated by this
                                   migration or by the importer added
                                   alongside it.

WHAT THIS DOES NOT CHANGE
--------------------------
Reuses `source_snapshot` / `entity_source_match` / `arc_assessment_run`
(20260921_september_snapshot) unchanged -- no new snapshot-approval or
matching machinery, exactly the "five-layer model" the schema proposal
already described. No existing table is altered.

PAYLOAD IS VERBATIM, NEVER THE MATCH KEY
------------------------------------------
Every delivered field lands in `payload jsonb`, exactly as parsed (leading
zeros preserved by the importer reading each field as text, never inferring
a numeric type -- confirmed necessary: 14% of populated CCNs and ~10% of ZIPs
in the real file carry a leading zero). Only the columns actually needed for
matching or dedup (`npi`, `ccn`, the IQVIA internal id) are lifted into their
own indexed columns; nothing is parsed, derived or renamed into them beyond
that lift. `source_matching.py`'s `assert_not_tefca_fact` already refuses to
let any observation source (including these) write a TEFCA-authoritative
field -- unchanged by this migration, since these tables carry only
`payload`/lifted-identifier columns, never a TEFCA fact column at all.

APPEND-ONLY BY GRANT, SAME AS THE SEPTEMBER MIGRATION
---------------------------------------------------------
Runtime role: SELECT + INSERT only. No UPDATE, no DELETE. A corrected row is
a new row in a new, later-received `source_snapshot`; nothing here is ever
edited in place.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20261002_iqvia_observations"
down_revision = "20260930_alembic_version_read"
branch_labels = None
depends_on = None

OWNER = "docuaction_owner"
TABLES = ("iqvia_hco_observation", "iqvia_hcp_observation", "iqvia_affiliation_observation")


class IqviaObservationPreconditionError(RuntimeError):
    pass


def _offline() -> bool:
    return context.is_offline_mode()


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    return False if _offline() else name in _inspector().get_table_names()


def _app_role() -> str:
    role = os.getenv("DB_APP_ROLE", "").strip()
    if _offline():
        return role or "docuaction_app"
    if not role:
        raise IqviaObservationPreconditionError(
            "DB_APP_ROLE is not set. This migration grants runtime privileges to a "
            "named application role on the three tables it creates, the same "
            "discipline 20260921_september_snapshot uses -- it refuses to guess "
            "which role that is. Set DB_APP_ROLE=docuaction_app.")
    return role


def _uuid():
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    role = _app_role()

    # ── iqvia_hco_observation ─────────────────────────────────────────────────
    if not _table_exists("iqvia_hco_observation"):
        op.create_table(
            "iqvia_hco_observation",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("source_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False),
            # IQVIA's own internal organisation key (DEMOGRAPHIC.HCO_HCE_ID),
            # verbatim -- NOT an NPI/CCN; those are separate, often-absent
            # fields on the same row (only 25.4%/7.4% populated respectively
            # in the delivered file), lifted below for matching only.
            sa.Column("source_record_key", sa.Text(), nullable=False),
            sa.Column("record_sha256", sa.String(64), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("npi", sa.String(10)),
            sa.Column("ccn", sa.String(20)),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.UniqueConstraint("source_snapshot_id", "source_record_key",
                                name="uq_iqvia_hco_snapshot_key"),
        )
        op.create_index("idx_iqvia_hco_npi", "iqvia_hco_observation", ["npi"])
        op.create_index("idx_iqvia_hco_ccn", "iqvia_hco_observation", ["ccn"])

    # ── iqvia_hcp_observation ─────────────────────────────────────────────────
    if not _table_exists("iqvia_hcp_observation"):
        op.create_table(
            "iqvia_hcp_observation",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("source_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False),
            # HCP_ADDR is one row per (practitioner, address) -- the key is
            # the pair, not the practitioner alone (a real HCP_HCE_ID repeats
            # across its 1..39 address rows in the delivered file).
            sa.Column("source_record_key", sa.Text(), nullable=False),
            sa.Column("record_sha256", sa.String(64), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("npi", sa.String(10)),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.UniqueConstraint("source_snapshot_id", "source_record_key",
                                name="uq_iqvia_hcp_snapshot_key"),
        )
        op.create_index("idx_iqvia_hcp_npi", "iqvia_hcp_observation", ["npi"])

    # ── iqvia_affiliation_observation ─────────────────────────────────────────
    # No data delivered for this extract (see module docstring) -- created so
    # the importer/matcher have a destination the day one arrives, never
    # written to by this migration.
    if not _table_exists("iqvia_affiliation_observation"):
        op.create_table(
            "iqvia_affiliation_observation",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("source_snapshot_id", _uuid(),
                      sa.ForeignKey("source_snapshot.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("hcp_record_key", sa.Text(), nullable=False),
            sa.Column("hco_record_key", sa.Text(), nullable=False),
            sa.Column("affiliation_type", sa.Text()),
            sa.Column("record_sha256", sa.String(64), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("correlation_id", sa.String(64), nullable=False),
            sa.UniqueConstraint("source_snapshot_id", "hcp_record_key", "hco_record_key",
                                "affiliation_type", name="uq_iqvia_affiliation_snapshot_key"),
        )
        op.create_index("idx_iqvia_affiliation_hcp", "iqvia_affiliation_observation",
                        ["hcp_record_key"])
        op.create_index("idx_iqvia_affiliation_hco", "iqvia_affiliation_observation",
                        ["hco_record_key"])

    for table in TABLES:
        op.execute(f'GRANT SELECT, INSERT ON "{table}" TO "{role}"')


def downgrade() -> None:
    """Refuses if any observation row exists -- evidence is not un-recorded,
    same discipline as 20260921_september_snapshot's downgrade."""
    if not _offline():
        bind = op.get_bind()
        for table in TABLES:
            if _table_exists(table):
                n = bind.execute(sa.text(f'select count(*) from "{table}"')).scalar()
                if n:
                    raise IqviaObservationPreconditionError(
                        f"{table} holds {n} row(s); downgrade refused.")
    for table in TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}"')
