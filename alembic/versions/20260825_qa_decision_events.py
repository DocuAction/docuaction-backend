"""QA gate — immutable analyst/QA decision events

Revision ID: 20260825_qa_events
Revises:      20260824_evidence_prov
Create Date:  2026-08-25

WHAT THIS ADDS
──────────────
  review_decision_events   NEW  append-only; every human act on a determination
  review_records.reportable_at  ONE additive nullable column
  review_effective_determination  a VIEW expressing precedence without hiding

NO BACKFILL, AND THAT IS THE POINT
The 43 existing determinations get NO events. They are system recommendations
that no human has resolved — `reviewer_resolution` is NULL on every one of them.
Creating an analyst or QA event for them would manufacture a human decision that
never happened, and `reportable_at` stays NULL so none of them can pass the gate
retrospectively.

SEGREGATION OF DUTIES IS ENFORCED IN THE DATABASE, NOT ONLY IN THE SERVICE
`trg_review_event_sod` resolves the analyst for the review and refuses a QA event
from the same person. It catches any future code path that bypasses the service —
which is the failure mode an application-only check cannot cover.

REVERSIBLE
`downgrade()` drops the view, the trigger, the function, the table and the
column. No data outside this feature is touched.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260825_qa_events"
down_revision = "20260824_evidence_prov"
branch_labels = None
depends_on = None

SOD_FUNCTION = """
CREATE OR REPLACE FUNCTION review_event_enforce_sod() RETURNS trigger AS $$
DECLARE
    analyst UUID;
BEGIN
    IF NEW.event_type <> 'QA_REVIEW' THEN
        RETURN NEW;
    END IF;

    -- The analyst whose determination this QA event reviews: the most recent
    -- determination event on this review before this one.
    SELECT e.actor_user_id INTO analyst
    FROM   review_decision_events e
    WHERE  e.review_id = NEW.review_id
      AND  e.event_type IN ('ANALYST_DETERMINATION','SUPERSEDING_DETERMINATION')
      AND  e.sequence_number < NEW.sequence_number
    ORDER  BY e.sequence_number DESC
    LIMIT  1;

    IF analyst IS NULL THEN
        RAISE EXCEPTION
          'QA event on review % has no preceding determination to review',
          NEW.review_id;
    END IF;

    IF analyst = NEW.actor_user_id THEN
        -- Permitted ONLY under an explicit, separately-granted exception.
        IF NEW.sod_exception_granted_by IS NULL
           OR NEW.sod_exception_reason IS NULL
           OR NEW.sod_exception_granted_by = NEW.actor_user_id THEN
            RAISE EXCEPTION
              'segregation of duties: % may not QA their own determination on %',
              NEW.actor_email, NEW.review_id;
        END IF;
    END IF;

    RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

EFFECTIVE_VIEW = """
CREATE OR REPLACE VIEW review_effective_determination AS
SELECT DISTINCT ON (e.review_id)
       e.review_id,
       e.id                AS decision_event_id,
       e.event_type,
       e.determination,
       e.determined_bucket,
       e.actor_user_id,
       e.actor_email,
       e.actor_role,
       e.occurred_at,
       e.sequence_number
FROM   review_decision_events e
WHERE  e.event_type IN ('ANALYST_DETERMINATION','SUPERSEDING_DETERMINATION')
  AND  NOT EXISTS (SELECT 1 FROM review_decision_events s
                   WHERE s.supersedes_decision_id = e.id)
ORDER  BY e.review_id, e.sequence_number DESC;
"""



# ── offline (--sql) tolerance ───────────────────────────────────────────────
# `alembic upgrade --sql` binds a MockConnection, which sa.inspect() cannot
# read. The guards below would raise NoInspectionAvailable and no reviewable
# script could be produced. Offline they are handed an inspector that reports an
# empty database, so upgrade() emits its full DDL — drift-unaware by
# construction, which is what an offline script is. downgrade() renders as a
# no-op offline for the same reason, and is not offered as a review artefact.


class _OfflineInspector:
    """Reports an empty schema so every create guard opens."""

    @staticmethod
    def get_table_names():
        return []

    @staticmethod
    def get_columns(table):
        return []

    @staticmethod
    def get_indexes(table):
        return []

    @staticmethod
    def get_unique_constraints(table):
        return []

    @staticmethod
    def get_foreign_keys(table):
        return []


def _inspect(bind):
    return _OfflineInspector() if op.get_context().as_sql else sa.inspect(bind)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = _inspect(bind)

    if "review_decision_events" not in set(inspector.get_table_names()):
        op.create_table(
            "review_decision_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("review_id", sa.String(20), nullable=False),
            sa.Column("sequence_number", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(30), nullable=False),
            sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("actor_email", sa.String(320), nullable=False),
            # The role held WHEN the decision was made, not the role held now.
            sa.Column("actor_role", sa.String(30), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("determination", sa.String(12)),
            sa.Column("determined_bucket", sa.String(2)),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("qa_action", sa.String(10)),
            sa.Column("qa_reason", sa.Text()),
            sa.Column("escalated_to_user_id", postgresql.UUID(as_uuid=True)),
            sa.Column("escalation_reason", sa.Text()),
            sa.Column("supersedes_decision_id", postgresql.UUID(as_uuid=True)),
            sa.Column("supersession_reason", sa.Text()),
            sa.Column("sod_exception_granted_by", postgresql.UUID(as_uuid=True)),
            sa.Column("sod_exception_reason", sa.Text()),
            sa.Column("ip_address", sa.String(45)),
            sa.Column("correlation_id", postgresql.UUID(as_uuid=True)),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["review_id"], ["review_records.review_id"]),
            sa.ForeignKeyConstraint(["supersedes_decision_id"],
                                    ["review_decision_events.id"]),
            sa.UniqueConstraint("review_id", "sequence_number",
                                name="uq_review_event_seq"),
            sa.CheckConstraint("event_type <> 'QA_REVIEW' OR qa_action IS NOT NULL",
                               name="ck_review_event_qa_action"),
            sa.CheckConstraint(
                "qa_action IS NULL OR qa_action IN ('APPROVE','RETURN','ESCALATE')",
                name="ck_review_event_qa_action_vocab"),
            sa.CheckConstraint(
                "qa_action <> 'ESCALATE' OR (escalated_to_user_id IS NOT NULL "
                "AND escalation_reason IS NOT NULL)",
                name="ck_review_event_escalation_complete"),
            sa.CheckConstraint(
                "supersedes_decision_id IS NULL OR supersession_reason IS NOT NULL",
                name="ck_review_event_supersession_reason"),
            sa.CheckConstraint("length(btrim(rationale)) >= 10",
                               name="ck_review_event_rationale"),
            sa.CheckConstraint(
                "event_type IN ('ANALYST_DETERMINATION','QA_REVIEW',"
                "'SUPERSEDING_DETERMINATION')", name="ck_review_event_type"),
            sa.CheckConstraint(
                "determination IS NULL OR determination IN ('CONFIRM','RECLASSIFY')",
                name="ck_review_event_determination"),
        )
        op.create_index("idx_review_event_review_seq", "review_decision_events",
                        ["review_id", "sequence_number"])
        op.create_index("idx_review_event_supersedes", "review_decision_events",
                        ["supersedes_decision_id"])
        op.create_index("idx_review_event_qa_action", "review_decision_events",
                        ["qa_action"])
        op.create_index("ix_review_event_review", "review_decision_events",
                        ["review_id"])
        op.create_index("ix_review_event_type", "review_decision_events",
                        ["event_type"])
        op.create_index("ix_review_event_actor", "review_decision_events",
                        ["actor_user_id"])

    existing = {c["name"] for c in inspector.get_columns("review_records")}
    if "reportable_at" not in existing:
        # Nullable, no default. NULL on all 43 existing rows, correctly: none has
        # passed QA, and the gate must not be back-dated.
        op.add_column("review_records",
                      sa.Column("reportable_at", sa.DateTime(), nullable=True))

    op.execute(SOD_FUNCTION)
    op.execute("DROP TRIGGER IF EXISTS trg_review_event_sod ON review_decision_events")
    op.execute(
        "CREATE TRIGGER trg_review_event_sod BEFORE INSERT ON review_decision_events "
        "FOR EACH ROW EXECUTE FUNCTION review_event_enforce_sod()")
    op.execute(EFFECTIVE_VIEW)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = _inspect(bind)

    op.execute("DROP VIEW IF EXISTS review_effective_determination")
    op.execute("DROP TRIGGER IF EXISTS trg_review_event_sod ON review_decision_events")
    op.execute("DROP FUNCTION IF EXISTS review_event_enforce_sod()")

    existing = {c["name"] for c in inspector.get_columns("review_records")}
    if "reportable_at" in existing:
        op.drop_column("review_records", "reportable_at")

    if "review_decision_events" in set(inspector.get_table_names()):
        op.drop_table("review_decision_events")
