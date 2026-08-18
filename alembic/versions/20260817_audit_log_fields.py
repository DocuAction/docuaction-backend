"""AT-001 / AT-009 — audit_logs: event_type, outcome, correlation_id.

Revision ID: 20260817_audit_fields
Revises:      20260725_tefca_registry
Create Date:  2026-08-17

WHAT THIS FIXES
---------------
`audit_logs` carried action, resource_type, resource_id, details, ip_address and
created_at. It did NOT carry the event's category, its outcome, or the id tying
together the events of one business transaction. Those three facts were either
absent entirely or buried inside the `details` JSON blob, so the two questions an
auditor asks first —

    "show me every failed authentication"
    "show me everything that happened during this import"

— could not be expressed in SQL. They required scanning the table and re-parsing
JSON per row, and the Audit Trail UI had no column to filter on (AT-007).

BACKFILL
--------
Existing rows are backfilled rather than left null:

  * `correlation_id` is lifted out of `details->>'correlation_id'`, which the
    auth routes have been writing since the enterprise auth work. Those rows
    already HAVE the value; it was simply not addressable.
  * `outcome` is derived from `details->>'result'` and from the action name, so
    historical `login_failed` rows read as failures rather than as nulls that a
    future "failures only" filter would silently omit.
  * `event_type` is derived from the action name using the same buckets as
    app/services/audit.py::classify_event_type.

Leaving history null would make the new filters lie by omission: an empty result
for "failed logins before today" is indistinguishable from "there were none".

SAFETY
------
Additive only — three nullable columns and three indexes. No existing column is
altered or dropped, and no row is deleted. The downgrade drops exactly what the
upgrade added.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260817_audit_fields"
down_revision = "20260725_tefca_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = {c["name"] for c in sa.inspect(conn).get_columns("audit_logs")}

    if "event_type" not in existing:
        op.add_column("audit_logs", sa.Column("event_type", sa.String(50), nullable=True))
        op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    if "outcome" not in existing:
        op.add_column("audit_logs", sa.Column("outcome", sa.String(20), nullable=True))
        op.create_index("ix_audit_logs_outcome", "audit_logs", ["outcome"])
    if "correlation_id" not in existing:
        op.add_column("audit_logs", sa.Column("correlation_id", sa.String(64), nullable=True))
        op.create_index("ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"])

    # ── Backfill ────────────────────────────────────────────────────────────
    # The correlation id is already present in details for auth events.
    op.execute(
        """
        UPDATE audit_logs
           SET correlation_id = details ->> 'correlation_id'
         WHERE correlation_id IS NULL
           AND details ? 'correlation_id'
        """
    )

    # Outcome: the recorded result wins; otherwise the action name decides.
    op.execute(
        """
        UPDATE audit_logs
           SET outcome = CASE
                 WHEN lower(coalesce(details ->> 'result', '')) IN ('fail', 'failure', 'error')
                      THEN 'failure'
                 WHEN lower(coalesce(details ->> 'result', '')) = 'rejected' THEN 'rejected'
                 WHEN lower(coalesce(details ->> 'result', '')) IN ('blocked', 'denied')
                      THEN 'blocked'
                 WHEN lower(action) LIKE '%%\\_failed'   ESCAPE '\\' THEN 'failure'
                 WHEN lower(action) LIKE '%%\\_failure'  ESCAPE '\\' THEN 'failure'
                 WHEN lower(action) LIKE '%%\\_blocked'  ESCAPE '\\' THEN 'blocked'
                 WHEN lower(action) LIKE '%%\\_throttled' ESCAPE '\\' THEN 'blocked'
                 WHEN lower(action) LIKE '%%\\_rejected' ESCAPE '\\' THEN 'rejected'
                 ELSE 'success'
               END
         WHERE outcome IS NULL
        """
    )

    # Event type: same buckets as classify_event_type().
    op.execute(
        """
        UPDATE audit_logs
           SET event_type = CASE
                 WHEN lower(action) IN (
                        'login_success', 'login_failed', 'login_failure',
                        'login_blocked', 'login_throttled', 'logout', 'signup',
                        'signup_rejected', 'signup_throttled', 'password_reset',
                        'email_verified')
                      THEN 'authentication'
                 WHEN lower(action) IN ('file_scan', 'permission_denied') THEN 'security'
                 WHEN lower(action) IN (
                        'entity_import', 'import_completed', 'fhir_import',
                        'csv_import')
                      THEN 'data_import'
                 WHEN lower(action) IN (
                        'review_executed', 'review_decision', 'entity_verified',
                        'bucket_override', 'verification_started',
                        'verification_completed')
                      THEN 'review'
                 WHEN lower(action) IN (
                        'entity_created', 'entity_updated', 'status_changed',
                        'status_change_refused', 'npi_flagged')
                      THEN 'data_change'
                 WHEN lower(action) IN (
                        'user_approved', 'user_rejected', 'user_disabled',
                        'user_role_changed', 'user_invited', 'password_set')
                      THEN 'administration'
                 WHEN lower(action) LIKE '%%report%%' THEN 'reporting'
                 WHEN lower(action) LIKE '%%export%%' THEN 'reporting'
                 WHEN lower(action) LIKE 'login%%'  THEN 'authentication'
                 WHEN lower(action) LIKE 'signup%%' THEN 'authentication'
                 WHEN lower(action) LIKE 'auth%%'   THEN 'authentication'
                 WHEN lower(action) LIKE 'user\\_%%' ESCAPE '\\' THEN 'administration'
                 -- Matches classify_event_type()'s residue bucket, which is the
                 -- label the Audit Trail filter offers. 'system' would be a
                 -- bucket the filter does not list.
                 ELSE 'other'
               END
         WHERE event_type IS NULL
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    existing = {c["name"] for c in sa.inspect(conn).get_columns("audit_logs")}
    for col, idx in (
        ("correlation_id", "ix_audit_logs_correlation_id"),
        ("outcome", "ix_audit_logs_outcome"),
        ("event_type", "ix_audit_logs_event_type"),
    ):
        if col in existing:
            op.drop_index(idx, table_name="audit_logs")
            op.drop_column("audit_logs", col)
