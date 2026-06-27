"""TEFCA dashboard tables: tefca_connector_logs, tefca_reviews, tefca_findings.

Revision ID: 20260627_tefca_dashboard
Revises:      20260627_tefca_initial
Create Date:  2026-06-27

Adds the three lightweight tables backing the executive dashboard. Creates ONLY
these tables (from the TEFCA metadata, scoped) — touches nothing else.
"""
from alembic import op

revision = "20260627_tefca_dashboard"
down_revision = "20260627_tefca_initial"
branch_labels = None
depends_on = None

DASHBOARD_TABLES = ["tefca_connector_logs", "tefca_reviews", "tefca_findings"]


def _tables():
    import app.Tefca.models  # noqa: F401  (registration side effect)
    from app.core.database import Base
    md = Base.metadata
    return md, [md.tables[name] for name in DASHBOARD_TABLES]


def upgrade() -> None:
    bind = op.get_bind()
    md, tables = _tables()
    md.create_all(bind=bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    md, tables = _tables()
    md.drop_all(bind=bind, tables=tables, checkfirst=True)
