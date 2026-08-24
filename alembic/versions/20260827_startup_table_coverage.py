"""Bring TEFCA's startup-only schema under Alembic.

Revision ID: 20260827_startup_coverage
Revises:     20260826_area1_audit
Create Date: 2026-08-22

WHY THIS EXISTS
---------------
`alembic/env.py` pointed `target_metadata` at one of the project's two
declarative Bases, and imported one of the eight modules that populate them, so
Alembic could see 47 of the 135 modelled tables. Among the tables it could not
see were ones created by nothing except `app/main.py` startup's
`Base.metadata.create_all()` — so `alembic upgrade head` on an empty database
produced a database the application could not start against.

WHAT IT COVERS, AND WHY SO LITTLE
---------------------------------
The first draft of this revision created all 80 uncovered tables, because
`target_metadata` had just been widened to every model the process could import.
That over-corrected: 77 of the 80 belong to other DocuAction products — ERP,
case management, migration tooling, the enterprise document-to-action core,
shared reporting — and creating them here would have imported four unrelated
product schemas into a database holding federal contract evidence.

Option D of docs/database_domain_architecture.md makes each program module the
owner of its own schema. This revision therefore covers three tables:

    tefca_import_history   TEFCA-owned, declared in app/Tefca/models.py
    users                  Core. app/Tefca/routes.py resolves actor names for
                           the audit trail; ppef_scheduler.py resolves
                           job.requested_by by email
    audit_logs             Core. The TEFCA audit-trail endpoint reads it and
                           joins to users

Those two Core tables are the entire cross-domain surface of TEFCA, traced from
import statements rather than by matching names. `documents` looked like a third
until the import turned out to be `from docx import Document`, and `audit_log`
looked like a fourth until it turned out to be a class-name collision with
`audit_logs`. Neither is here.

No enum types are created: none of the three tables uses one. The 35 enum
definitions the first draft carried all belonged to the removed tables.

INDEX NAMES
-----------
41 indexes exist under a name a migration chose while the model declares the
same table and the same columns under a different name —
`idx_rce_curated_entity` against `ix_rce_curated_records_canonical_entity_id`.
Those are renamed, not dropped and rebuilt: `ALTER INDEX ... RENAME` is a
catalogue update that neither rebuilds the index nor blocks reads. Where both
names already exist — which happens on the live database, because some indexes
came from a migration and some from `create_all()` — the duplicate is dropped
instead. `downgrade()` renames them back, and Alembic runs downgrades
newest-first, so the original names are restored before any earlier revision
tries to drop them by name.

Every table these renames touch is TEFCA-owned.

ALSO HERE
---------
13 model-declared indexes on TEFCA tables the chain already builds, each missing
on at least one of the two deployment paths.

And one redundancy: `rce_issues.issue_code` ended up guaranteed unique three
times — a UNIQUE constraint from 20260822, a plain index from 20260822, and the
unique index the model declares. Three b-trees on one column, all maintained on
every write. This keeps the one the model declares and drops the other two, and
only once the model's index exists, so uniqueness is never unenforced.
`downgrade()` puts both back first.

IDEMPOTENCE
-----------
Every table, index and rename is guarded. On the live database this revision is
a no-op — which is what lets the chain be run rather than stamped.

NO DATA
-------
No INSERT, UPDATE or DELETE anywhere in this revision.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260827_startup_coverage"
down_revision = "20260826_area1_audit"
branch_labels = None
depends_on = None


# -- guards ------------------------------------------------------------------
# In offline (--sql) mode there is no bind to inspect, so the guards open and the
# full DDL is emitted.


def _offline() -> bool:
    return op.get_context().as_sql


def _has_table(name: str) -> bool:
    if _offline():
        return False
    return name in set(sa.inspect(op.get_bind()).get_table_names())


def _has_index(table: str, name: str) -> bool:
    if _offline() or not _has_table(table):
        return False
    inspector = sa.inspect(op.get_bind())
    names = {i["name"] for i in inspector.get_indexes(table)}
    names |= {u["name"] for u in inspector.get_unique_constraints(table)
              if u.get("name")}
    return name in names


def _drop_table_if_present(name: str) -> None:
    if _offline() or _has_table(name):
        op.drop_table(name)


def _drop_index_if_present(table: str, name: str) -> None:
    if _offline() or _has_index(table, name):
        op.drop_index(name, table_name=table)


def _rename_index(table: str, old: str, new: str) -> None:
    """Make `new` the one index on this table for these columns.

    Three states are reachable, because different databases got these indexes
    from different places — some from a migration, some from startup's
    `create_all()`:

      only `old` exists   rename it; the index itself is untouched
      both exist          `old` is a duplicate of `new` on the same table, the
                          same columns and the same uniqueness. Drop it. Left
                          alone it is a second copy of the same b-tree that
                          every write has to maintain, and it reappears in
                          `alembic check` forever.
      only `new` exists   nothing to do, which is the end state
    """
    if _offline():
        op.execute('ALTER INDEX "%s" RENAME TO "%s"' % (old, new))
        return
    has_old, has_new = _has_index(table, old), _has_index(table, new)
    if has_old and not has_new:
        op.execute('ALTER INDEX "%s" RENAME TO "%s"' % (old, new))
    elif has_old and has_new:
        op.drop_index(old, table_name=table)


def _has_constraint(table: str, name: str) -> bool:
    if _offline() or not _has_table(table):
        return False
    return bool(op.get_bind().execute(sa.text(
        "SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "WHERE t.relname = :t AND c.conname = :n"),
        {"t": table, "n": name}).scalar())


def _drop_constraint_if_redundant(table: str, constraint: str,
                                  covered_by: str) -> None:
    """Drop a uniqueness constraint whose guarantee a unique index already makes.

    Only ever called with `covered_by` naming a UNIQUE index on the same column,
    and only when that index is actually present — so the uniqueness guarantee
    never lapses, not even inside this transaction.
    """
    if _offline():
        op.execute('ALTER TABLE %s DROP CONSTRAINT IF EXISTS "%s"'
                   % (table, constraint))
        return
    if not _has_index(table, covered_by):
        return
    op.execute('ALTER TABLE %s DROP CONSTRAINT IF EXISTS "%s"'
               % (table, constraint))


# (name in the database, name the model declares, table)
#
# The first three are on `review_decision_events`, whose table exists on both
# paths but with different index names depending on which built it. They are
# listed with the others so the same reconcile-or-drop logic applies.
INDEX_RENAMES = (
    ("ix_review_event_review", "ix_review_decision_events_review_id",
     "review_decision_events"),
    ("ix_review_event_type", "ix_review_decision_events_event_type",
     "review_decision_events"),
    ("ix_review_event_actor", "ix_review_decision_events_actor_user_id",
     "review_decision_events"),
    ("idx_dim_evidence_correlation", "ix_tefca_dimension_evidence_correlation_id", "tefca_dimension_evidence"),
    ("idx_dim_evidence_observation_hash", "ix_tefca_dimension_evidence_observation_hash", "tefca_dimension_evidence"),
    ("idx_dim_evidence_observation_result", "ix_tefca_dimension_evidence_observation_result", "tefca_dimension_evidence"),
    ("idx_dim_evidence_vocab_version", "ix_tefca_dimension_evidence_vocabulary_version", "tefca_dimension_evidence"),
    ("idx_rce_correction_issue", "ix_rce_correction_details_issue_id", "rce_correction_details"),
    ("idx_rce_correction_source", "ix_rce_correction_details_source_record_id", "rce_correction_details"),
    ("idx_rce_curated_entity", "ix_rce_curated_records_canonical_entity_id", "rce_curated_records"),
    ("idx_rce_curated_hcid", "ix_rce_curated_records_hcid", "rce_curated_records"),
    ("idx_rce_curated_intake", "ix_rce_curated_records_source_intake_id", "rce_curated_records"),
    ("idx_rce_curated_npi", "ix_rce_curated_records_npi", "rce_curated_records"),
    ("idx_rce_curated_oid", "ix_rce_curated_records_rce_org_oid", "rce_curated_records"),
    ("idx_rce_curated_omo", "ix_rce_curated_records_org_managing_org", "rce_curated_records"),
    ("idx_rce_curated_partof", "ix_rce_curated_records_part_of", "rce_curated_records"),
    ("idx_rce_curated_record", "ix_rce_curated_records_source_record_id", "rce_curated_records"),
    ("idx_rce_curated_tefcaid", "ix_rce_curated_records_tefcaid", "rce_curated_records"),
    ("idx_rce_issue_intake", "ix_rce_issues_source_intake_id", "rce_issues"),
    ("idx_rce_issue_record", "ix_rce_issues_source_record_id", "rce_issues"),
    ("idx_rce_issue_rule", "ix_rce_issues_rule_id", "rce_issues"),
    ("idx_rce_issue_run", "ix_rce_issues_run_id", "rce_issues"),
    ("idx_rce_issue_severity", "ix_rce_issues_severity", "rce_issues"),
    ("idx_rce_issue_type", "ix_rce_issues_issue_type", "rce_issues"),
    ("idx_rce_record_entity", "ix_rce_source_records_canonical_entity_id", "rce_source_records"),
    ("idx_rce_record_hcid", "ix_rce_source_records_hcid", "rce_source_records"),
    ("idx_rce_record_intake", "ix_rce_source_records_source_intake_id", "rce_source_records"),
    ("idx_rce_record_npi", "ix_rce_source_records_npi", "rce_source_records"),
    ("idx_rce_record_sha", "ix_rce_source_records_record_sha256", "rce_source_records"),
    ("idx_rce_record_source_id", "ix_rce_source_records_source_rce_id", "rce_source_records"),
    ("idx_rce_record_tefcaid", "ix_rce_source_records_tefcaid", "rce_source_records"),
    ("idx_rce_rule_exec_run", "ix_rce_rule_execution_history_run_id", "rce_rule_execution_history"),
    ("idx_rce_run_intake", "ix_rce_ingestion_runs_source_intake_id", "rce_ingestion_runs"),
    ("idx_tefca_contact_source_record", "ix_tefca_entity_contacts_source_record_id", "tefca_entity_contacts"),
    ("ix_evidence_hop_evidence", "ix_evidence_relationship_path_evidence_id", "evidence_relationship_path"),
    ("ix_source_version_hash", "ix_source_version_snapshots_source_file_hash", "source_version_snapshots"),
    ("ix_tefca_dimension_evidence_dimension", "ix_tefca_dimension_evidence_evidence_dimension", "tefca_dimension_evidence"),
    ("ix_tefca_ppef_records_enrollment", "ix_tefca_ppef_records_enrollment_id", "tefca_ppef_records"),
    ("ix_tefca_ppef_records_related", "ix_tefca_ppef_records_related_enrollment_id", "tefca_ppef_records"),
    ("ix_tefca_ppef_records_snapshot", "ix_tefca_ppef_records_snapshot_id", "tefca_ppef_records"),
    ("ix_tefca_ppef_snapshots_version", "ix_tefca_ppef_snapshots_resource_version", "tefca_ppef_snapshots"),
)


def upgrade() -> None:
    for old, new, table in INDEX_RENAMES:
        _rename_index(table, old, new)
    if not _has_table('tefca_import_history'):
        op.create_table('tefca_import_history',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('filename', sa.String(length=500), nullable=True),
        sa.Column('record_count', sa.Integer(), nullable=True),
        sa.Column('imported_count', sa.Integer(), nullable=True),
        sa.Column('rejected_count', sa.Integer(), nullable=True),
        sa.Column('uploaded_by', sa.String(length=255), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=True),
        sa.Column('file_hash', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('errors', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('tefca_import_history', 'ix_tefca_import_history_file_hash'):
        op.create_index(op.f('ix_tefca_import_history_file_hash'), 'tefca_import_history', ['file_hash'], unique=False)
    if not _has_index('tefca_import_history', 'ix_tefca_import_history_status'):
        op.create_index(op.f('ix_tefca_import_history_status'), 'tefca_import_history', ['status'], unique=False)
    if not _has_index('tefca_import_history', 'ix_tefca_import_history_uploaded_at'):
        op.create_index(op.f('ix_tefca_import_history_uploaded_at'), 'tefca_import_history', ['uploaded_at'], unique=False)
    if not _has_index('tefca_import_history', 'ix_tefca_import_history_uploaded_by'):
        op.create_index(op.f('ix_tefca_import_history_uploaded_by'), 'tefca_import_history', ['uploaded_by'], unique=False)
    if not _has_table('users'):
        op.create_table('users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=True),
        sa.Column('company', sa.String(length=255), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=True),
        sa.Column('plan', sa.String(length=20), nullable=True),
        sa.Column('allowed_modules', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('is_verified', sa.Boolean(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=True),
        sa.Column('tokens_revoked_at', sa.DateTime(), nullable=True),
        sa.Column('last_active_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('users', 'ix_users_email'):
        op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    if not _has_index('users', 'ix_users_tenant_id'):
        op.create_index(op.f('ix_users_tenant_id'), 'users', ['tenant_id'], unique=False)
    if not _has_table('audit_logs'):
        op.create_table('audit_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=True),
        sa.Column('outcome', sa.String(length=20), nullable=True),
        sa.Column('resource_type', sa.String(length=50), nullable=True),
        sa.Column('resource_id', sa.String(length=255), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(length=50), nullable=True),
        sa.Column('correlation_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('audit_logs', 'ix_audit_logs_correlation_id'):
        op.create_index(op.f('ix_audit_logs_correlation_id'), 'audit_logs', ['correlation_id'], unique=False)
    if not _has_index('audit_logs', 'ix_audit_logs_event_type'):
        op.create_index(op.f('ix_audit_logs_event_type'), 'audit_logs', ['event_type'], unique=False)
    if not _has_index('audit_logs', 'ix_audit_logs_outcome'):
        op.create_index(op.f('ix_audit_logs_outcome'), 'audit_logs', ['outcome'], unique=False)
    if not _has_index('audit_logs', 'ix_audit_logs_tenant_id'):
        op.create_index(op.f('ix_audit_logs_tenant_id'), 'audit_logs', ['tenant_id'], unique=False)
    if not _has_index('rce_correction_details', 'ix_rce_correction_details_curated_record_id'):
        op.create_index(op.f('ix_rce_correction_details_curated_record_id'), 'rce_correction_details', ['curated_record_id'], unique=False)
    if not _has_index('rce_issues', 'ix_rce_issues_issue_code'):
        op.create_index(op.f('ix_rce_issues_issue_code'), 'rce_issues', ['issue_code'], unique=True)
    if not _has_index('rce_rule_execution_history', 'ix_rce_rule_execution_history_rule_id'):
        op.create_index(op.f('ix_rce_rule_execution_history_rule_id'), 'rce_rule_execution_history', ['rule_id'], unique=False)
    if not _has_index('rce_source_intakes', 'ix_rce_source_intakes_schema_fingerprint'):
        op.create_index(op.f('ix_rce_source_intakes_schema_fingerprint'), 'rce_source_intakes', ['schema_fingerprint'], unique=False)
    if not _has_index('rce_source_intakes', 'ix_rce_source_intakes_sha256'):
        op.create_index(op.f('ix_rce_source_intakes_sha256'), 'rce_source_intakes', ['sha256'], unique=False)
    if not _has_index('source_version_snapshots', 'ix_source_version_snapshots_created_at'):
        op.create_index(op.f('ix_source_version_snapshots_created_at'), 'source_version_snapshots', ['created_at'], unique=False)
    if not _has_index('source_version_snapshots', 'ix_source_version_snapshots_source'):
        op.create_index(op.f('ix_source_version_snapshots_source'), 'source_version_snapshots', ['source'], unique=False)
    if not _has_index('tefca_dimension_evidence', 'ix_tefca_dimension_evidence_created_at'):
        op.create_index(op.f('ix_tefca_dimension_evidence_created_at'), 'tefca_dimension_evidence', ['created_at'], unique=False)
    if not _has_index('tefca_dimension_evidence', 'ix_tefca_dimension_evidence_generation_timestamp'):
        op.create_index(op.f('ix_tefca_dimension_evidence_generation_timestamp'), 'tefca_dimension_evidence', ['generation_timestamp'], unique=False)
    if not _has_index('tefca_entity_contacts', 'ix_tefca_entity_contacts_entity_id'):
        op.create_index(op.f('ix_tefca_entity_contacts_entity_id'), 'tefca_entity_contacts', ['entity_id'], unique=False)
    if not _has_index('review_decision_events', 'ix_review_decision_events_review_id'):
        op.create_index(op.f('ix_review_decision_events_review_id'), 'review_decision_events', ['review_id'], unique=False)
    if not _has_index('review_decision_events', 'ix_review_decision_events_event_type'):
        op.create_index(op.f('ix_review_decision_events_event_type'), 'review_decision_events', ['event_type'], unique=False)
    if not _has_index('review_decision_events', 'ix_review_decision_events_actor_user_id'):
        op.create_index(op.f('ix_review_decision_events_actor_user_id'), 'review_decision_events', ['actor_user_id'], unique=False)
    _drop_constraint_if_redundant('rce_issues', 'rce_issues_issue_code_key',
                                  covered_by='ix_rce_issues_issue_code')
    if (_has_index('rce_issues', 'ix_rce_issues_issue_code')
            and _has_index('rce_issues', 'idx_rce_issue_code')):
        op.drop_index('idx_rce_issue_code', table_name='rce_issues')


def downgrade() -> None:
    if _has_table('rce_issues'):
        if not _has_index('rce_issues', 'idx_rce_issue_code'):
            op.create_index('idx_rce_issue_code', 'rce_issues', ['issue_code'])
        if not _has_constraint('rce_issues', 'rce_issues_issue_code_key'):
            op.execute('ALTER TABLE rce_issues ADD CONSTRAINT '
                       '"rce_issues_issue_code_key" UNIQUE (issue_code)')
    _drop_index_if_present('review_decision_events', 'ix_review_decision_events_actor_user_id')
    _drop_index_if_present('review_decision_events', 'ix_review_decision_events_event_type')
    _drop_index_if_present('review_decision_events', 'ix_review_decision_events_review_id')
    _drop_index_if_present('tefca_entity_contacts', 'ix_tefca_entity_contacts_entity_id')
    _drop_index_if_present('tefca_dimension_evidence', 'ix_tefca_dimension_evidence_generation_timestamp')
    _drop_index_if_present('tefca_dimension_evidence', 'ix_tefca_dimension_evidence_created_at')
    _drop_index_if_present('source_version_snapshots', 'ix_source_version_snapshots_source')
    _drop_index_if_present('source_version_snapshots', 'ix_source_version_snapshots_created_at')
    _drop_index_if_present('rce_source_intakes', 'ix_rce_source_intakes_sha256')
    _drop_index_if_present('rce_source_intakes', 'ix_rce_source_intakes_schema_fingerprint')
    _drop_index_if_present('rce_rule_execution_history', 'ix_rce_rule_execution_history_rule_id')
    _drop_index_if_present('rce_issues', 'ix_rce_issues_issue_code')
    _drop_index_if_present('rce_correction_details', 'ix_rce_correction_details_curated_record_id')
    _drop_table_if_present('audit_logs')
    _drop_table_if_present('users')
    _drop_table_if_present('tefca_import_history')
    for old, new, table in INDEX_RENAMES:
        _rename_index(table, new, old)
