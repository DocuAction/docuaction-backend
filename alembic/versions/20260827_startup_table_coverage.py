"""Bring the startup-only tables under Alembic.

Revision ID: 20260827_startup_coverage
Revises:     20260826_area1_audit
Create Date: 2026-08-22

WHY THIS EXISTS
---------------
`alembic/env.py` pointed `target_metadata` at one of the project's two
declarative Bases, and imported one of the eight modules that register models on
them. Alembic could therefore see 47 of the 135 modelled tables. Of the tables
it could not see, 80 are created by nothing except `app/main.py`
startup's `Base.metadata.create_all()`.

That meant `alembic upgrade head` on an empty database produced a database the
application cannot run against: no `users`, no `documents`, no `audit_logs`.
Schema construction depended on an unversioned side effect of booting the app.

This revision closes the gap. It is the literal DDL for those 80 tables
and their 59 indexes, produced by `--autogenerate` against a schema
built by the migration chain alone — so by construction it contains exactly what
the chain was failing to build, and nothing else.

It also creates 10 indexes that the models declare on tables the chain
does create, and that the chain never created.

INDEX NAMES
-----------
38 indexes exist under a name a migration chose while the model declares
the same table and the same columns under a different name —
`idx_rce_curated_entity` against `ix_rce_curated_records_canonical_entity_id`,
and so on. Those are renamed, not dropped and rebuilt: `ALTER INDEX ... RENAME`
is a catalogue update that neither rebuilds the index nor blocks reads.

`downgrade()` renames them back. Alembic runs downgrades newest-first, so the
original names are restored before any earlier revision's `downgrade()` tries to
drop them by name.

WHAT WAS DELIBERATELY LEFT OUT
------------------------------
Autogenerate also proposed dropping `area1_mutation_log` and its three indexes,
because that table has no ORM model — it is written by database triggers and read
by auditors. Dropping the Area 1 mutation log to satisfy a metadata comparison
would be exactly backwards. Those proposals are discarded here, and the table is
declared migration-owned in env.py's `include_object` so it stops being reported
as drift.

Every `drop_index` autogenerate proposed against a chain-built table was likewise
discarded. Those indexes are hand-written composites and partials the models
cannot express — `idx_dim_evidence_entity_dimension`, `idx_rce_issue_open` — and
they exist because a query needed them.

IDEMPOTENCE
-----------
Every table, index and rename is guarded. On the live database — where startup
already created all 80 tables — this revision is close to a no-op, which
is what lets the chain be run rather than stamped.

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


# The enum types these tables use, hoisted out of the column definitions.
#
# Autogenerate writes the type inline in every table that uses it, and each
# create_table then emits its own CREATE TYPE — so the second table sharing a
# type fails with `type "..." already exists`. They are created once, guarded,
# before any table, and every column below says `create_type=False`.
#
# PostgreSQL also leaves the type behind when the table that introduced it is
# dropped, which is the defect that makes 20260725_tefca_registry's downgrade
# unusable. downgrade() drops them so this revision survives a round trip.
ENUM_TYPES = (
    ("applicationstatus", ('APPLIED', 'SCREENING', 'INTERVIEW', 'SUBMITTED_TO_CLIENT', 'OFFERED', 'HIRED', 'REJECTED')),
    ("benchstatus", ('AVAILABLE', 'SUBMITTED', 'INTERVIEWING', 'PLACED', 'NOT_AVAILABLE')),
    ("billingcode", ('CPT_99490', 'CPT_99439', 'CPT_99491', 'CPT_99437', 'CPT_99487', 'CPT_99489', 'CPT_99495', 'CPT_99496', 'CPT_99424', 'CPT_99425', 'CPT_99426', 'CPT_99427')),
    ("casestatus", ('ACTIVE', 'INACTIVE', 'ENROLLED', 'PENDING_CONSENT', 'DISCHARGED', 'CLOSED')),
    ("cmmoduletype", ('CLINICAL_CM', 'CCM', 'TCM', 'PCM', 'BEHAVIORAL_CM', 'GOVERNMENT_CM', 'DISCHARGE_CM')),
    ("contractstatus", ('ACTIVE', 'COMPLETED', 'TERMINATED', 'PENDING')),
    ("customertype", ('GOVERNMENT', 'COMMERCIAL')),
    ("dealregstatus", ('ACTIVE', 'EXPIRED', 'USED')),
    ("dealstatus", ('INTAKE', 'QUOTED', 'SUBMITTED', 'WON', 'LOST', 'ORDERED', 'SHIPPED', 'DELIVERED', 'CANCELLED')),
    ("employeestatus", ('ACTIVE', 'BENCH', 'TERMINATED', 'ONBOARDING')),
    ("expensecategory", ('SALARY', 'BENEFITS', 'IMMIGRATION', 'RENT', 'UTILITIES', 'SOFTWARE', 'TRAVEL', 'EQUIPMENT', 'INSURANCE', 'OTHER')),
    ("financialstage", ('QUOTED', 'AWARDED', 'ORDERED', 'INVOICED', 'PAID')),
    ("followupstatus", ('PENDING', 'COMPLETED')),
    ("inputmode", ('VOICE', 'TEXT', 'STRUCTURED', 'EHR_IMPORT')),
    ("invoicestatus", ('DRAFT', 'SENT', 'PAID', 'OVERDUE', 'CANCELLED')),
    ("jobstatus", ('OPEN', 'CLOSED', 'ON_HOLD')),
    ("lifecyclestatus", ('ACTIVE', 'END_OF_SALE', 'END_OF_LIFE')),
    ("notestatus", ('DRAFT', 'AI_GENERATED', 'PENDING_REVIEW', 'APPROVED', 'SIGNED', 'BILLED')),
    ("notetype", ('CCM_PROGRESS', 'TCM_FOLLOWUP', 'PCM_PROGRESS', 'CARE_PLAN_UPDATE', 'DISCHARGE_SUMMARY', 'EDUCATION_NOTE', 'REFERRAL_NOTE', 'MEETING_MINUTES', 'SDOH_ASSESSMENT', 'GOVERNMENT_CASE')),
    ("opportunitysource", ('SAM_GOV', 'STATE', 'LOCAL', 'GSA_EBUY', 'GRANTS_GOV', 'MANUAL')),
    ("opportunitystatus", ('NEW', 'REVIEWING', 'MATCHED', 'PURSUING', 'BID_SUBMITTED', 'WON', 'LOST', 'NO_BID', 'EXPIRED')),
    ("outreachstatus", ('DRAFT', 'SENT', 'REPLIED')),
    ("projectstage", ('INTAKE', 'REVIEW', 'PROPOSAL', 'SUBMITTED', 'AWARDED', 'ACTIVE', 'COMPLETED', 'LOST', 'EXPIRED')),
    ("projecttype", ('DEVELOPMENT', 'CONSULTING')),
    ("proposalcategory", ('TECHNICAL', 'MANAGEMENT', 'PAST_PERFORMANCE', 'PRICING', 'COMPLIANCE', 'COVER_LETTER', 'EXECUTIVE_SUMMARY', 'FULL_PROPOSAL', 'TEMPLATE')),
    ("quotestatus", ('DRAFT', 'FINAL', 'SUBMITTED', 'SUPERSEDED')),
    ("reviewstatus", ('PENDING', 'CONFIRMED', 'CORRECTED')),
    ("rfqstatus", ('NEW', 'IN_PROGRESS', 'QUOTED', 'SUBMITTED', 'WON', 'LOST', 'CANCELLED')),
    ("setasidetype", ('NONE', 'SB', 'EIGHT_A', 'WOSB', 'HUBZONE', 'SDVOSB')),
    ("submissionstatus", ('SUBMITTED', 'CLIENT_REVIEW', 'INTERVIEW_SCHEDULED', 'FEEDBACK_PENDING', 'SELECTED', 'REJECTED')),
    ("supplierquotestatus", ('PENDING', 'RECEIVED', 'DELAYED', 'NOT_NEEDED')),
    ("suppliertier", ('PREFERRED', 'APPROVED', 'BACKUP')),
    ("taskstatus", ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'OVERDUE')),
    ("ticketpriority", ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    ("ticketstatus", ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED')),
)


def _create_enum_types() -> None:
    bind = op.get_bind()
    for name, values in ENUM_TYPES:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=not _offline())

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
    _create_enum_types()

    # Renames next: a renamed index then satisfies the guard on the matching
    # create below, so the two phases cannot produce a duplicate.
    for old, new, table in INDEX_RENAMES:
        _rename_index(table, old, new)

    if not _has_table('agency_metrics'):
        op.create_table('agency_metrics',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('agency_name', sa.String(length=500), nullable=False),
        sa.Column('total_rfqs', sa.Integer(), nullable=False),
        sa.Column('total_won', sa.Integer(), nullable=False),
        sa.Column('total_lost', sa.Integer(), nullable=False),
        sa.Column('total_quoted_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('total_won_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('avg_margin_pct', sa.Float(), nullable=False),
        sa.Column('win_rate_pct', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agency_name')
        )
    if not _has_table('audit_log'):
        op.create_table('audit_log',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('table_name', sa.String(length=100), nullable=False),
        sa.Column('record_id', sa.String(length=100), nullable=False),
        sa.Column('field_name', sa.String(length=100), nullable=True),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('user_id', sa.String(length=255), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('candidates'):
        op.create_table('candidates',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('first_name', sa.String(length=255), nullable=False),
        sa.Column('last_name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('resume_text', sa.Text(), nullable=True),
        sa.Column('resume_filename', sa.String(length=500), nullable=True),
        sa.Column('linkedin_url', sa.String(length=500), nullable=True),
        sa.Column('skills', sa.Text(), nullable=True),
        sa.Column('years_experience', sa.Integer(), nullable=True),
        sa.Column('clearance_level', sa.String(length=50), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('cm_government_cases'):
        op.create_table('cm_government_cases',
        sa.Column('case_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=255), nullable=False),
        sa.Column('case_type', sa.String(length=100), nullable=True),
        sa.Column('agency', sa.String(length=200), nullable=True),
        sa.Column('case_reference', sa.String(length=200), nullable=True),
        sa.Column('assigned_analyst', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=100), nullable=True),
        sa.Column('priority', sa.String(length=50), nullable=True),
        sa.Column('case_summary', sa.Text(), nullable=True),
        sa.Column('findings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('evidence_documents', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('recommendations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('regulatory_citations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('received_date', sa.DateTime(), nullable=True),
        sa.Column('response_deadline', sa.DateTime(), nullable=True),
        sa.Column('completed_date', sa.DateTime(), nullable=True),
        sa.Column('investigation_type', sa.String(length=100), nullable=True),
        sa.Column('subjects', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('chain_of_custody', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('case_body', sa.Text(), nullable=True),
        sa.Column('ai_generated', sa.Boolean(), nullable=True),
        sa.Column('ai_model_used', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('case_id')
        )
    if not _has_index('cm_government_cases', 'ix_cm_government_cases_case_reference'):
        op.create_index(op.f('ix_cm_government_cases_case_reference'), 'cm_government_cases', ['case_reference'], unique=False)
    if not _has_index('cm_government_cases', 'ix_cm_government_cases_tenant_id'):
        op.create_index(op.f('ix_cm_government_cases_tenant_id'), 'cm_government_cases', ['tenant_id'], unique=False)
    if not _has_table('cm_patients'):
        op.create_table('cm_patients',
        sa.Column('patient_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=255), nullable=False),
        sa.Column('mrn', sa.String(length=100), nullable=True),
        sa.Column('first_name', sa.String(length=255), nullable=True),
        sa.Column('last_name', sa.String(length=255), nullable=True),
        sa.Column('date_of_birth', sa.String(length=20), nullable=True),
        sa.Column('gender', sa.String(length=20), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('address', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('insurance_primary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('insurance_secondary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('pcp_name', sa.String(length=255), nullable=True),
        sa.Column('pcp_npi', sa.String(length=10), nullable=True),
        sa.Column('diagnoses_icd10', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('hcc_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('risk_score', sa.Float(), nullable=True),
        sa.Column('risk_tier', sa.String(length=20), nullable=True),
        sa.Column('sdoh_flags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('cm_module_type', postgresql.ENUM('CLINICAL_CM', 'CCM', 'TCM', 'PCM', 'BEHAVIORAL_CM', 'GOVERNMENT_CM', 'DISCHARGE_CM', name='cmmoduletype', create_type=False), nullable=True),
        sa.Column('case_status', postgresql.ENUM('ACTIVE', 'INACTIVE', 'ENROLLED', 'PENDING_CONSENT', 'DISCHARGED', 'CLOSED', name='casestatus', create_type=False), nullable=True),
        sa.Column('consent_date', sa.DateTime(), nullable=True),
        sa.Column('enrollment_date', sa.DateTime(), nullable=True),
        sa.Column('assigned_case_manager_id', sa.String(length=255), nullable=True),
        sa.Column('care_plan_id', sa.UUID(), nullable=True),
        sa.Column('monthly_contact_required', sa.Boolean(), nullable=True),
        sa.Column('last_contact_date', sa.DateTime(), nullable=True),
        sa.Column('next_contact_due', sa.DateTime(), nullable=True),
        sa.Column('total_ccm_minutes_ytd', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('patient_id')
        )
    if not _has_index('cm_patients', 'ix_cm_patients_tenant_id'):
        op.create_index(op.f('ix_cm_patients_tenant_id'), 'cm_patients', ['tenant_id'], unique=False)
    if not _has_table('company_profiles'):
        op.create_table('company_profiles',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_name', sa.String(length=500), nullable=False),
        sa.Column('dba_name', sa.String(length=500), nullable=True),
        sa.Column('cage_code', sa.String(length=20), nullable=True),
        sa.Column('uei_number', sa.String(length=50), nullable=True),
        sa.Column('duns_number', sa.String(length=20), nullable=True),
        sa.Column('sam_registration_date', sa.Date(), nullable=True),
        sa.Column('sam_expiration_date', sa.Date(), nullable=True),
        sa.Column('naics_codes', sa.JSON(), nullable=True),
        sa.Column('gsa_sins', sa.JSON(), nullable=True),
        sa.Column('psc_codes', sa.JSON(), nullable=True),
        sa.Column('certifications', sa.JSON(), nullable=True),
        sa.Column('capabilities_narrative', sa.Text(), nullable=True),
        sa.Column('core_competencies', sa.JSON(), nullable=True),
        sa.Column('past_performance_keywords', sa.JSON(), nullable=True),
        sa.Column('contract_vehicles', sa.JSON(), nullable=True),
        sa.Column('business_size', sa.String(length=50), nullable=True),
        sa.Column('socioeconomic_categories', sa.JSON(), nullable=True),
        sa.Column('primary_state', sa.String(length=2), nullable=True),
        sa.Column('service_states', sa.JSON(), nullable=True),
        sa.Column('min_contract_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('max_contract_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('target_agencies', sa.JSON(), nullable=True),
        sa.Column('excluded_keywords', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('customers'):
        op.create_table('customers',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('customer_type', postgresql.ENUM('GOVERNMENT', 'COMMERCIAL', name='customertype', create_type=False), nullable=False),
        sa.Column('division', sa.String(length=500), nullable=True),
        sa.Column('department', sa.String(length=500), nullable=True),
        sa.Column('agency_code', sa.String(length=50), nullable=True),
        sa.Column('website', sa.String(length=500), nullable=True),
        sa.Column('contact_name', sa.String(length=255), nullable=True),
        sa.Column('contact_title', sa.String(length=255), nullable=True),
        sa.Column('contact_email', sa.String(length=255), nullable=True),
        sa.Column('contact_phone', sa.String(length=50), nullable=True),
        sa.Column('contact2_name', sa.String(length=255), nullable=True),
        sa.Column('contact2_title', sa.String(length=255), nullable=True),
        sa.Column('contact2_email', sa.String(length=255), nullable=True),
        sa.Column('contact2_phone', sa.String(length=50), nullable=True),
        sa.Column('billing_address', sa.Text(), nullable=True),
        sa.Column('billing_city', sa.String(length=255), nullable=True),
        sa.Column('billing_state', sa.String(length=50), nullable=True),
        sa.Column('billing_zip', sa.String(length=20), nullable=True),
        sa.Column('shipping_address', sa.String(length=500), nullable=True),
        sa.Column('shipping_city', sa.String(length=255), nullable=True),
        sa.Column('shipping_state', sa.String(length=50), nullable=True),
        sa.Column('shipping_zip', sa.String(length=20), nullable=True),
        sa.Column('ship_to_zip', sa.String(length=20), nullable=True),
        sa.Column('mailing_address', sa.String(length=500), nullable=True),
        sa.Column('mailing_city', sa.String(length=255), nullable=True),
        sa.Column('mailing_state', sa.String(length=50), nullable=True),
        sa.Column('mailing_zip', sa.String(length=20), nullable=True),
        sa.Column('cage_code', sa.String(length=20), nullable=True),
        sa.Column('uei_number', sa.String(length=50), nullable=True),
        sa.Column('duns_number', sa.String(length=20), nullable=True),
        sa.Column('tax_exempt_id', sa.String(length=100), nullable=True),
        sa.Column('credit_limit', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('payment_terms', sa.String(length=50), nullable=True),
        sa.Column('contract_vehicle', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('dev_projects'):
        op.create_table('dev_projects',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('project_type', postgresql.ENUM('DEVELOPMENT', 'CONSULTING', name='projecttype', create_type=False), nullable=False),
        sa.Column('agency', sa.String(length=500), nullable=True),
        sa.Column('solicitation_number', sa.String(length=200), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('estimated_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('stage', postgresql.ENUM('INTAKE', 'REVIEW', 'PROPOSAL', 'SUBMITTED', 'AWARDED', 'ACTIVE', 'COMPLETED', 'LOST', 'EXPIRED', name='projectstage', create_type=False), nullable=False),
        sa.Column('assigned_to', sa.String(length=255), nullable=True),
        sa.Column('contract_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('amount_invoiced', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('amount_received', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('document_path', sa.String(length=1000), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('employees'):
        op.create_table('employees',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('department', sa.String(length=255), nullable=True),
        sa.Column('salary', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('billing_rate', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('benefits_cost_monthly', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('immigration_cost', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('immigration_type', sa.String(length=50), nullable=True),
        sa.Column('status', postgresql.ENUM('ACTIVE', 'BENCH', 'TERMINATED', 'ONBOARDING', name='employeestatus', create_type=False), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('utilization_pct', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('job_postings'):
        op.create_table('job_postings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('employment_type', sa.String(length=50), nullable=True),
        sa.Column('salary_min', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('salary_max', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('clearance_required', sa.String(length=50), nullable=True),
        sa.Column('skills_required', sa.Text(), nullable=True),
        sa.Column('contract_name', sa.String(length=500), nullable=True),
        sa.Column('status', postgresql.ENUM('OPEN', 'CLOSED', 'ON_HOLD', name='jobstatus', create_type=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('migration_projects'):
        op.create_table('migration_projects',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.String(length=20), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=True),
        sa.Column('source_system', sa.String(length=200), nullable=True),
        sa.Column('target_system', sa.String(length=200), nullable=True),
        sa.Column('total_schemas', sa.Integer(), nullable=True),
        sa.Column('total_fields', sa.Integer(), nullable=True),
        sa.Column('total_mappings', sa.Integer(), nullable=True),
        sa.Column('approved_mappings', sa.Integer(), nullable=True),
        sa.Column('overall_risk_score', sa.Float(), nullable=True),
        sa.Column('foia_readiness_score', sa.Float(), nullable=True),
        sa.Column('correlation_id', sa.String(length=30), nullable=True),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id')
        )
    if not _has_table('output_templates'):
        op.create_table('output_templates',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('template_content', sa.Text(), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('output_templates', 'ix_output_templates_tenant_id'):
        op.create_index(op.f('ix_output_templates_tenant_id'), 'output_templates', ['tenant_id'], unique=False)
    if not _has_table('price_history'):
        op.create_table('price_history',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('product_part_number', sa.String(length=255), nullable=False),
        sa.Column('supplier_name', sa.String(length=500), nullable=True),
        sa.Column('supplier_id', sa.UUID(), nullable=True),
        sa.Column('unit_cost', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('sell_price', sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column('margin_pct', sa.Float(), nullable=True),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('quote_id', sa.UUID(), nullable=True),
        sa.Column('date_quoted', sa.Date(), server_default=sa.text('CURRENT_DATE'), nullable=False),
        sa.Column('agency', sa.String(length=500), nullable=True),
        sa.Column('won', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('price_history', 'ix_price_history_product_part_number'):
        op.create_index(op.f('ix_price_history_product_part_number'), 'price_history', ['product_part_number'], unique=False)
    if not _has_table('product_catalog'):
        op.create_table('product_catalog',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('part_number', sa.String(length=255), nullable=False),
        sa.Column('manufacturer', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=255), nullable=True),
        sa.Column('msrp', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('last_known_cost', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('taa_compliant', sa.Boolean(), nullable=False),
        sa.Column('lifecycle', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('product_catalog', 'ix_product_catalog_manufacturer'):
        op.create_index(op.f('ix_product_catalog_manufacturer'), 'product_catalog', ['manufacturer'], unique=False)
    if not _has_index('product_catalog', 'ix_product_catalog_part_number'):
        op.create_index(op.f('ix_product_catalog_part_number'), 'product_catalog', ['part_number'], unique=False)
    if not _has_table('products'):
        op.create_table('products',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('manufacturer', sa.String(length=255), nullable=False),
        sa.Column('part_number', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(length=255), nullable=True),
        sa.Column('msrp', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('lifecycle_status', postgresql.ENUM('ACTIVE', 'END_OF_SALE', 'END_OF_LIFE', name='lifecyclestatus', create_type=False), nullable=False),
        sa.Column('replacement_product_id', sa.UUID(), nullable=True),
        sa.Column('alt_part_numbers', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['replacement_product_id'], ['products.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('products', 'ix_products_part_number'):
        op.create_index(op.f('ix_products_part_number'), 'products', ['part_number'], unique=False)
    if not _has_table('proposal_library'):
        op.create_table('proposal_library',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('category', postgresql.ENUM('TECHNICAL', 'MANAGEMENT', 'PAST_PERFORMANCE', 'PRICING', 'COMPLIANCE', 'COVER_LETTER', 'EXECUTIVE_SUMMARY', 'FULL_PROPOSAL', 'TEMPLATE', name='proposalcategory', create_type=False), nullable=False),
        sa.Column('agency', sa.String(length=500), nullable=True),
        sa.Column('solicitation_number', sa.String(length=200), nullable=True),
        sa.Column('contract_type', sa.String(length=50), nullable=True),
        sa.Column('naics_code', sa.String(length=20), nullable=True),
        sa.Column('keywords', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=True),
        sa.Column('file_name', sa.String(length=500), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('state_audit_log'):
        op.create_table('state_audit_log',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('entity_id', sa.String(), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('old_state', sa.String(length=50), nullable=True),
        sa.Column('new_state', sa.String(length=50), nullable=False),
        sa.Column('change_reason', sa.Text(), nullable=True),
        sa.Column('changed_by', sa.String(), nullable=False),
        sa.Column('ip_address', sa.String(length=50), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('state_audit_log', 'ix_audit_entity'):
        op.create_index('ix_audit_entity', 'state_audit_log', ['entity_id', 'entity_type'], unique=False)
    if not _has_index('state_audit_log', 'ix_audit_tenant'):
        op.create_index('ix_audit_tenant', 'state_audit_log', ['tenant_id'], unique=False)
    if not _has_index('state_audit_log', 'ix_audit_timestamp'):
        op.create_index('ix_audit_timestamp', 'state_audit_log', ['timestamp'], unique=False)
    if not _has_table('suppliers'):
        op.create_table('suppliers',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('contact_name', sa.String(length=255), nullable=True),
        sa.Column('contact_email', sa.String(length=255), nullable=True),
        sa.Column('contact_phone', sa.String(length=50), nullable=True),
        sa.Column('payment_terms', sa.String(length=50), nullable=True),
        sa.Column('reliability_score', sa.Integer(), nullable=True),
        sa.Column('preferred_tier', postgresql.ENUM('PREFERRED', 'APPROVED', 'BACKUP', name='suppliertier', create_type=False), nullable=False),
        sa.Column('country', sa.String(length=100), nullable=True),
        sa.Column('categories', sa.JSON(), nullable=True),
        sa.Column('supplier_type', sa.String(length=50), nullable=True),
        sa.Column('manufacturer_focus', sa.Text(), nullable=True),
        sa.Column('website', sa.String(length=500), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('support_tickets'):
        op.create_table('support_tickets',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('ticket_number', sa.String(length=50), nullable=False),
        sa.Column('subject', sa.String(length=1000), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=False),
        sa.Column('priority', postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='ticketpriority', create_type=False), nullable=False),
        sa.Column('status', postgresql.ENUM('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED', name='ticketstatus', create_type=False), nullable=False),
        sa.Column('submitted_by', sa.String(length=255), nullable=True),
        sa.Column('submitted_email', sa.String(length=255), nullable=True),
        sa.Column('response', sa.Text(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('tax_jurisdictions'):
        op.create_table('tax_jurisdictions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('state', sa.String(length=2), nullable=False),
        sa.Column('zip_code', sa.String(length=10), nullable=True),
        sa.Column('tax_rate', sa.Float(), nullable=False),
        sa.Column('jurisdiction_name', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('technical_library'):
        op.create_table('technical_library',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('brand', sa.String(length=255), nullable=False),
        sa.Column('category', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_type', sa.String(length=50), nullable=False),
        sa.Column('warranty_terms', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
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
    if not _has_table('tenants'):
        op.create_table('tenants',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=True),
        sa.Column('plan', sa.String(length=50), nullable=True),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('governance_policy', sa.String(length=50), nullable=True),
        sa.Column('strict_mode', sa.Boolean(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
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
    if not _has_table('validation_queue'):
        op.create_table('validation_queue',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=True),
        sa.Column('document_id', sa.String(), nullable=True),
        sa.Column('document_name', sa.String(), nullable=True),
        sa.Column('output_id', sa.String(), nullable=True),
        sa.Column('action_type', sa.String(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('risk_level', sa.String(), nullable=True),
        sa.Column('domain', sa.String(), nullable=True),
        sa.Column('correlation_id', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('reviewer_id', sa.String(), nullable=True),
        sa.Column('reviewer_notes', sa.Text(), nullable=True),
        sa.Column('content_preview', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('ai_memory'):
        op.create_table('ai_memory',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('candidate_id', sa.UUID(), nullable=True),
        sa.Column('candidate_name', sa.String(length=500), nullable=True),
        sa.Column('run_type', sa.String(length=50), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('skills', sa.Text(), nullable=True),
        sa.Column('clearance', sa.String(length=50), nullable=True),
        sa.Column('years_experience', sa.Integer(), nullable=True),
        sa.Column('match_data', sa.Text(), nullable=True),
        sa.Column('submission_package', sa.Text(), nullable=True),
        sa.Column('full_result', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('applications'):
        op.create_table('applications',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('candidate_id', sa.UUID(), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=False),
        sa.Column('status', postgresql.ENUM('APPLIED', 'SCREENING', 'INTERVIEW', 'SUBMITTED_TO_CLIENT', 'OFFERED', 'HIRED', 'REJECTED', name='applicationstatus', create_type=False), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['job_postings.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('ats_activities'):
        op.create_table('ats_activities',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('activity_type', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('candidate_id', sa.UUID(), nullable=True),
        sa.Column('job_id', sa.UUID(), nullable=True),
        sa.Column('user_name', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['job_postings.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('audio_files'):
        op.create_table('audio_files',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('filename', sa.String(length=500), nullable=False),
        sa.Column('file_path', sa.String(length=1000), nullable=False),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('file_type', sa.String(length=10), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('language_detected', sa.String(length=10), nullable=True),
        sa.Column('transcription_cost', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('audio_files', 'ix_audio_files_tenant_id'):
        op.create_index(op.f('ix_audio_files_tenant_id'), 'audio_files', ['tenant_id'], unique=False)
    if not _has_index('audio_files', 'ix_audio_files_user_id'):
        op.create_index(op.f('ix_audio_files_user_id'), 'audio_files', ['user_id'], unique=False)
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
    if not _has_table('bench_candidates'):
        op.create_table('bench_candidates',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('candidate_id', sa.UUID(), nullable=False),
        sa.Column('status', postgresql.ENUM('AVAILABLE', 'SUBMITTED', 'INTERVIEWING', 'PLACED', 'NOT_AVAILABLE', name='benchstatus', create_type=False), nullable=False),
        sa.Column('available_date', sa.Date(), nullable=True),
        sa.Column('desired_rate', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('visa_status', sa.String(length=50), nullable=True),
        sa.Column('relocation', sa.Boolean(), nullable=False),
        sa.Column('vendor_submissions', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('cm_billing_summaries'):
        op.create_table('cm_billing_summaries',
        sa.Column('summary_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=255), nullable=False),
        sa.Column('patient_id', sa.UUID(), nullable=True),
        sa.Column('billing_month', sa.String(length=7), nullable=True),
        sa.Column('case_manager_id', sa.String(length=255), nullable=True),
        sa.Column('billing_provider_npi', sa.String(length=10), nullable=True),
        sa.Column('total_minutes', sa.Integer(), nullable=True),
        sa.Column('billable_minutes', sa.Integer(), nullable=True),
        sa.Column('primary_cpt_code', sa.String(length=10), nullable=True),
        sa.Column('addon_cpt_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('estimated_reimbursement', sa.Float(), nullable=True),
        sa.Column('notes_count', sa.Integer(), nullable=True),
        sa.Column('consent_on_file', sa.Boolean(), nullable=True),
        sa.Column('care_plan_active', sa.Boolean(), nullable=True),
        sa.Column('documentation_complete', sa.Boolean(), nullable=True),
        sa.Column('ready_to_bill', sa.Boolean(), nullable=True),
        sa.Column('billed_date', sa.DateTime(), nullable=True),
        sa.Column('claim_number', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['patient_id'], ['cm_patients.patient_id'], ),
        sa.PrimaryKeyConstraint('summary_id')
        )
    if not _has_index('cm_billing_summaries', 'ix_cm_billing_summaries_tenant_id'):
        op.create_index(op.f('ix_cm_billing_summaries_tenant_id'), 'cm_billing_summaries', ['tenant_id'], unique=False)
    if not _has_table('cm_care_plans'):
        op.create_table('cm_care_plans',
        sa.Column('plan_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=255), nullable=False),
        sa.Column('patient_id', sa.UUID(), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('plan_version', sa.Integer(), nullable=True),
        sa.Column('effective_date', sa.DateTime(), nullable=True),
        sa.Column('review_date', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('primary_diagnosis', sa.String(length=500), nullable=True),
        sa.Column('diagnoses', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('medications', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('allergies', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('functional_status', sa.Text(), nullable=True),
        sa.Column('cognitive_status', sa.Text(), nullable=True),
        sa.Column('caregiver_info', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('advance_directive', sa.String(length=100), nullable=True),
        sa.Column('goals', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('interventions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('barriers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('strengths', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('care_team', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('specialist_referrals', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('sdoh_assessment', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('community_resources', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('education_topics', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('education_materials_generated', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('ai_generated', sa.Boolean(), nullable=True),
        sa.Column('ai_model_used', sa.String(length=100), nullable=True),
        sa.Column('source_documents', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('plan_body', sa.Text(), nullable=True),
        sa.Column('patient_signature', sa.Boolean(), nullable=True),
        sa.Column('patient_signature_date', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['patient_id'], ['cm_patients.patient_id'], ),
        sa.PrimaryKeyConstraint('plan_id')
        )
    if not _has_index('cm_care_plans', 'ix_cm_care_plans_tenant_id'):
        op.create_index(op.f('ix_cm_care_plans_tenant_id'), 'cm_care_plans', ['tenant_id'], unique=False)
    if not _has_table('cm_discharge_records'):
        op.create_table('cm_discharge_records',
        sa.Column('discharge_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=255), nullable=False),
        sa.Column('patient_id', sa.UUID(), nullable=True),
        sa.Column('admission_date', sa.DateTime(), nullable=True),
        sa.Column('discharge_date', sa.DateTime(), nullable=True),
        sa.Column('attending_physician', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('primary_diagnosis', sa.String(length=500), nullable=True),
        sa.Column('secondary_diagnoses', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('procedures_performed', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('hospital_course', sa.Text(), nullable=True),
        sa.Column('complications', sa.Text(), nullable=True),
        sa.Column('condition_at_discharge', sa.String(length=100), nullable=True),
        sa.Column('discharge_disposition', sa.String(length=200), nullable=True),
        sa.Column('discharge_facility', sa.String(length=500), nullable=True),
        sa.Column('follow_up_provider', sa.String(length=500), nullable=True),
        sa.Column('follow_up_date', sa.String(length=50), nullable=True),
        sa.Column('follow_up_instructions', sa.Text(), nullable=True),
        sa.Column('medications_at_discharge', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('medication_changes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('medication_reconciliation_completed', sa.Boolean(), nullable=True),
        sa.Column('education_provided', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('patient_verbalized_understanding', sa.Boolean(), nullable=True),
        sa.Column('caregiver_educated', sa.Boolean(), nullable=True),
        sa.Column('warning_signs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('when_to_call_doctor', sa.Text(), nullable=True),
        sa.Column('er_criteria', sa.Text(), nullable=True),
        sa.Column('patient_instructions', sa.Text(), nullable=True),
        sa.Column('instructions_language', sa.String(length=50), nullable=True),
        sa.Column('ai_generated', sa.Boolean(), nullable=True),
        sa.Column('ai_model_used', sa.String(length=100), nullable=True),
        sa.Column('source_notes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('discharge_summary_body', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('signed_by', sa.String(length=255), nullable=True),
        sa.Column('signed_at', sa.DateTime(), nullable=True),
        sa.Column('completed_within_24h', sa.Boolean(), nullable=True),
        sa.Column('jc_rc020125_met', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['patient_id'], ['cm_patients.patient_id'], ),
        sa.PrimaryKeyConstraint('discharge_id')
        )
    if not _has_index('cm_discharge_records', 'ix_cm_discharge_records_tenant_id'):
        op.create_index(op.f('ix_cm_discharge_records_tenant_id'), 'cm_discharge_records', ['tenant_id'], unique=False)
    if not _has_table('cm_notes'):
        op.create_table('cm_notes',
        sa.Column('note_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=255), nullable=False),
        sa.Column('patient_id', sa.UUID(), nullable=True),
        sa.Column('case_manager_id', sa.String(length=255), nullable=False),
        sa.Column('note_type', postgresql.ENUM('CCM_PROGRESS', 'TCM_FOLLOWUP', 'PCM_PROGRESS', 'CARE_PLAN_UPDATE', 'DISCHARGE_SUMMARY', 'EDUCATION_NOTE', 'REFERRAL_NOTE', 'MEETING_MINUTES', 'SDOH_ASSESSMENT', 'GOVERNMENT_CASE', name='notetype', create_type=False), nullable=False),
        sa.Column('note_status', postgresql.ENUM('DRAFT', 'AI_GENERATED', 'PENDING_REVIEW', 'APPROVED', 'SIGNED', 'BILLED', name='notestatus', create_type=False), nullable=True),
        sa.Column('input_mode', postgresql.ENUM('VOICE', 'TEXT', 'STRUCTURED', 'EHR_IMPORT', name='inputmode', create_type=False), nullable=True),
        sa.Column('service_date', sa.DateTime(), nullable=False),
        sa.Column('time_start', sa.String(length=10), nullable=True),
        sa.Column('time_end', sa.String(length=10), nullable=True),
        sa.Column('total_minutes', sa.Integer(), nullable=True),
        sa.Column('billable_minutes', sa.Integer(), nullable=True),
        sa.Column('billing_code', postgresql.ENUM('CPT_99490', 'CPT_99439', 'CPT_99491', 'CPT_99437', 'CPT_99487', 'CPT_99489', 'CPT_99495', 'CPT_99496', 'CPT_99424', 'CPT_99425', 'CPT_99426', 'CPT_99427', name='billingcode', create_type=False), nullable=True),
        sa.Column('billing_rationale', sa.Text(), nullable=True),
        sa.Column('cumulative_minutes_this_month', sa.Integer(), nullable=True),
        sa.Column('voice_transcript', sa.Text(), nullable=True),
        sa.Column('clinical_summary', sa.Text(), nullable=True),
        sa.Column('note_body', sa.Text(), nullable=True),
        sa.Column('care_plan_updates', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('action_items', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('risk_flags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('patient_consent_verified', sa.Boolean(), nullable=True),
        sa.Column('care_plan_reviewed', sa.Boolean(), nullable=True),
        sa.Column('coordination_activities', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('medications_reconciled', sa.Boolean(), nullable=True),
        sa.Column('followup_scheduled', sa.Boolean(), nullable=True),
        sa.Column('physician_supervision_noted', sa.Boolean(), nullable=True),
        sa.Column('contains_sud_content', sa.Boolean(), nullable=True),
        sa.Column('sud_content_redacted', sa.Boolean(), nullable=True),
        sa.Column('ai_model_used', sa.String(length=100), nullable=True),
        sa.Column('ai_confidence', sa.Float(), nullable=True),
        sa.Column('ai_generation_time', sa.Float(), nullable=True),
        sa.Column('source_citations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('reviewed_by', sa.String(length=255), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('signed_by', sa.String(length=255), nullable=True),
        sa.Column('signed_at', sa.DateTime(), nullable=True),
        sa.Column('override_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['patient_id'], ['cm_patients.patient_id'], ),
        sa.PrimaryKeyConstraint('note_id')
        )
    if not _has_index('cm_notes', 'ix_cm_notes_tenant_id'):
        op.create_index(op.f('ix_cm_notes_tenant_id'), 'cm_notes', ['tenant_id'], unique=False)
    if not _has_table('contexts'):
        op.create_table('contexts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('source_name', sa.String(length=500), nullable=False),
        sa.Column('source_id', sa.String(), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('contexts', 'ix_contexts_tenant'):
        op.create_index('ix_contexts_tenant', 'contexts', ['tenant_id'], unique=False)
    if not _has_index('contexts', 'ix_contexts_type'):
        op.create_index('ix_contexts_type', 'contexts', ['type'], unique=False)
    if not _has_table('documents'):
        op.create_table('documents',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('filename', sa.String(length=500), nullable=False),
        sa.Column('file_path', sa.String(length=1000), nullable=False),
        sa.Column('file_type', sa.String(length=10), nullable=True),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('checksum_sha256', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('language', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('documents', 'idx_doc_tenant_user'):
        op.create_index('idx_doc_tenant_user', 'documents', ['tenant_id', 'user_id'], unique=False)
    if not _has_index('documents', 'ix_documents_checksum_sha256'):
        op.create_index(op.f('ix_documents_checksum_sha256'), 'documents', ['checksum_sha256'], unique=False)
    if not _has_index('documents', 'ix_documents_tenant_id'):
        op.create_index(op.f('ix_documents_tenant_id'), 'documents', ['tenant_id'], unique=False)
    if not _has_index('documents', 'ix_documents_user_id'):
        op.create_index(op.f('ix_documents_user_id'), 'documents', ['user_id'], unique=False)
    if not _has_table('migration_manifest_versions'):
        op.create_table('migration_manifest_versions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('manifest_id', sa.String(length=20), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('published_by', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('version_hash', sa.String(length=64), nullable=False),
        sa.Column('manifest_content', sa.JSON(), nullable=False),
        sa.Column('total_mappings', sa.Integer(), nullable=True),
        sa.Column('approved_mappings', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['migration_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('manifest_id')
        )
    if not _has_table('migration_schemas'):
        op.create_table('migration_schemas',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('schema_id', sa.String(length=20), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('schema_type', sa.String(length=20), nullable=True),
        sa.Column('system_type', sa.String(length=50), nullable=True),
        sa.Column('input_type', sa.String(length=30), nullable=True),
        sa.Column('file_path', sa.String(length=500), nullable=True),
        sa.Column('file_hash', sa.String(length=64), nullable=True),
        sa.Column('raw_content_length', sa.Integer(), nullable=True),
        sa.Column('table_count', sa.Integer(), nullable=True),
        sa.Column('field_count', sa.Integer(), nullable=True),
        sa.Column('relationship_count', sa.Integer(), nullable=True),
        sa.Column('pii_field_count', sa.Integer(), nullable=True),
        sa.Column('analysis_result', sa.JSON(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('model_used', sa.String(length=50), nullable=True),
        sa.Column('processing_time_ms', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['migration_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('schema_id')
        )
    if not _has_table('migration_validation_runs'):
        op.create_table('migration_validation_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('validation_id', sa.String(length=20), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('validation_type', sa.String(length=30), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('total_checks', sa.Integer(), nullable=True),
        sa.Column('passed_checks', sa.Integer(), nullable=True),
        sa.Column('failed_checks', sa.Integer(), nullable=True),
        sa.Column('warnings', sa.Integer(), nullable=True),
        sa.Column('results', sa.JSON(), nullable=True),
        sa.Column('processing_time_ms', sa.Float(), nullable=True),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['migration_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('validation_id')
        )
    if not _has_table('outreach_logs'):
        op.create_table('outreach_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('candidate_id', sa.UUID(), nullable=False),
        sa.Column('target_company', sa.String(length=500), nullable=False),
        sa.Column('recipient_email', sa.String(length=255), nullable=True),
        sa.Column('subject', sa.String(length=1000), nullable=True),
        sa.Column('email_content', sa.Text(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', postgresql.ENUM('DRAFT', 'SENT', 'REPLIED', name='outreachstatus', create_type=False), nullable=False),
        sa.Column('sent_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('placement_outcomes'):
        op.create_table('placement_outcomes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('candidate_id', sa.UUID(), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=True),
        sa.Column('outcome', sa.String(length=50), nullable=False),
        sa.Column('match_score', sa.Integer(), nullable=True),
        sa.Column('actual_bill_rate', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('actual_pay_rate', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('placed_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['job_postings.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('policy_validations'):
        op.create_table('policy_validations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('entity_id', sa.String(), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('policy_name', sa.String(length=100), nullable=True),
        sa.Column('domain', sa.String(length=50), nullable=True),
        sa.Column('strict_mode', sa.Boolean(), nullable=True),
        sa.Column('validation_result', sa.JSON(), nullable=False),
        sa.Column('gate_result', sa.String(length=50), nullable=True),
        sa.Column('accuracy_score', sa.Float(), nullable=True),
        sa.Column('reliability', sa.String(length=20), nullable=True),
        sa.Column('violations_count', sa.Integer(), nullable=True),
        sa.Column('certificate_id', sa.String(length=50), nullable=True),
        sa.Column('certificate_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('policy_validations', 'ix_pv_entity'):
        op.create_index('ix_pv_entity', 'policy_validations', ['entity_id', 'entity_type'], unique=False)
    if not _has_index('policy_validations', 'ix_pv_tenant'):
        op.create_index('ix_pv_tenant', 'policy_validations', ['tenant_id'], unique=False)
    if not _has_table('rfqs'):
        op.create_table('rfqs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('solicitation_number', sa.String(length=200), nullable=True),
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('agency', sa.String(length=500), nullable=True),
        sa.Column('naics_code', sa.String(length=20), nullable=True),
        sa.Column('set_aside_type', postgresql.ENUM('NONE', 'SB', 'EIGHT_A', 'WOSB', 'HUBZONE', 'SDVOSB', name='setasidetype', create_type=False), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('estimated_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('status', postgresql.ENUM('NEW', 'IN_PROGRESS', 'QUOTED', 'SUBMITTED', 'WON', 'LOST', 'CANCELLED', name='rfqstatus', create_type=False), nullable=False),
        sa.Column('priority_score', sa.Integer(), nullable=True),
        sa.Column('assigned_to', sa.String(length=255), nullable=True),
        sa.Column('customer_type', postgresql.ENUM('GOVERNMENT', 'COMMERCIAL', name='customertype', create_type=False), nullable=False),
        sa.Column('is_taxable', sa.Boolean(), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=True),
        sa.Column('raw_document_path', sa.String(length=1000), nullable=True),
        sa.Column('contract_officer_name', sa.String(length=255), nullable=True),
        sa.Column('contract_officer_email', sa.String(length=255), nullable=True),
        sa.Column('contract_officer_phone', sa.String(length=50), nullable=True),
        sa.Column('department', sa.String(length=500), nullable=True),
        sa.Column('ship_to_address', sa.Text(), nullable=True),
        sa.Column('ship_to_city', sa.String(length=255), nullable=True),
        sa.Column('ship_to_state', sa.String(length=2), nullable=True),
        sa.Column('ship_to_zip', sa.String(length=20), nullable=True),
        sa.Column('shipping_method', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('saved_searches'):
        op.create_table('saved_searches',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('search_type', sa.String(length=50), nullable=False),
        sa.Column('keywords', sa.Text(), nullable=True),
        sa.Column('naics_codes', sa.JSON(), nullable=True),
        sa.Column('set_aside_types', sa.JSON(), nullable=True),
        sa.Column('agencies', sa.JSON(), nullable=True),
        sa.Column('states', sa.JSON(), nullable=True),
        sa.Column('min_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('max_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('last_run', sa.DateTime(timezone=True), nullable=True),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('submissions'):
        op.create_table('submissions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('candidate_id', sa.UUID(), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=True),
        sa.Column('client_name', sa.String(length=500), nullable=False),
        sa.Column('vendor_name', sa.String(length=500), nullable=True),
        sa.Column('submission_type', sa.String(length=50), nullable=False),
        sa.Column('bill_rate', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('pay_rate', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('status', postgresql.ENUM('SUBMITTED', 'CLIENT_REVIEW', 'INTERVIEW_SCHEDULED', 'FEEDBACK_PENDING', 'SELECTED', 'REJECTED', name='submissionstatus', create_type=False), nullable=False),
        sa.Column('submitted_by', sa.String(length=255), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('interview_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['job_postings.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('supplier_contacts'):
        op.create_table('supplier_contacts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('contact_name', sa.String(length=255), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('supplier_metrics'):
        op.create_table('supplier_metrics',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('total_quotes_requested', sa.Integer(), nullable=False),
        sa.Column('total_quotes_received', sa.Integer(), nullable=False),
        sa.Column('avg_response_days', sa.Float(), nullable=False),
        sa.Column('total_deals_won', sa.Integer(), nullable=False),
        sa.Column('total_deals_lost', sa.Integer(), nullable=False),
        sa.Column('win_rate_pct', sa.Float(), nullable=False),
        sa.Column('avg_margin_pct', sa.Float(), nullable=False),
        sa.Column('authorized_brands', sa.Text(), nullable=True),
        sa.Column('reliability_score', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('supplier_price_snapshots'):
        op.create_table('supplier_price_snapshots',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('part_number', sa.String(length=255), nullable=False),
        sa.Column('unit_price', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('tenant_users'):
        op.create_table('tenant_users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('tenant_users', 'ix_tenant_users_tenant'):
        op.create_index('ix_tenant_users_tenant', 'tenant_users', ['tenant_id'], unique=False)
    if not _has_index('tenant_users', 'ix_tenant_users_user'):
        op.create_index('ix_tenant_users_user', 'tenant_users', ['user_id'], unique=False)
    if not _has_table('agency_contacts'):
        op.create_table('agency_contacts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('agency_name', sa.String(length=500), nullable=False),
        sa.Column('contact_name', sa.String(length=255), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('department', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('bom_items'):
        op.create_table('bom_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('manufacturer', sa.String(length=255), nullable=True),
        sa.Column('part_number', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('unit_of_measure', sa.String(length=50), nullable=False),
        sa.Column('clin', sa.String(length=50), nullable=True),
        sa.Column('ai_confidence', sa.Integer(), nullable=True),
        sa.Column('review_status', postgresql.ENUM('PENDING', 'CONFIRMED', 'CORRECTED', name='reviewstatus', create_type=False), nullable=False),
        sa.Column('canonical_product_id', sa.UUID(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['canonical_product_id'], ['products.id'], ),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('communication_logs'):
        op.create_table('communication_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('direction', sa.String(length=20), nullable=False),
        sa.Column('comm_type', sa.String(length=20), nullable=False),
        sa.Column('recipient_name', sa.String(length=500), nullable=True),
        sa.Column('recipient_email', sa.String(length=255), nullable=True),
        sa.Column('subject', sa.String(length=1000), nullable=True),
        sa.Column('body_preview', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('sent_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('contracts'):
        op.create_table('contracts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('contract_number', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('project_id', sa.UUID(), nullable=True),
        sa.Column('client_name', sa.String(length=500), nullable=False),
        sa.Column('agency', sa.String(length=500), nullable=True),
        sa.Column('contract_value', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('contract_type', sa.String(length=50), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('status', postgresql.ENUM('ACTIVE', 'COMPLETED', 'TERMINATED', 'PENDING', name='contractstatus', create_type=False), nullable=False),
        sa.Column('total_invoiced', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('total_received', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('total_expenses', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['dev_projects.id'], ),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('contract_number')
        )
    if not _has_table('deal_registrations'):
        op.create_table('deal_registrations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('oem', sa.String(length=100), nullable=False),
        sa.Column('registration_id', sa.String(length=200), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('sku_list', sa.JSON(), nullable=True),
        sa.Column('discount_pct', sa.Float(), nullable=True),
        sa.Column('special_unit_price', sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column('expiration_date', sa.Date(), nullable=True),
        sa.Column('status', postgresql.ENUM('ACTIVE', 'EXPIRED', 'USED', name='dealregstatus', create_type=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('follow_up_queue'):
        op.create_table('follow_up_queue',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('submission_id', sa.UUID(), nullable=False),
        sa.Column('candidate_name', sa.String(length=500), nullable=True),
        sa.Column('target_company', sa.String(length=500), nullable=True),
        sa.Column('next_follow_up_date', sa.Date(), nullable=False),
        sa.Column('status', postgresql.ENUM('PENDING', 'COMPLETED', name='followupstatus', create_type=False), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('invoices'):
        op.create_table('invoices',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('invoice_number', sa.String(length=50), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('status', postgresql.ENUM('DRAFT', 'SENT', 'PAID', 'OVERDUE', 'CANCELLED', name='invoicestatus', create_type=False), nullable=False),
        sa.Column('client_name', sa.String(length=500), nullable=False),
        sa.Column('client_address', sa.Text(), nullable=True),
        sa.Column('client_email', sa.String(length=255), nullable=True),
        sa.Column('client_phone', sa.String(length=50), nullable=True),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('project_id', sa.UUID(), nullable=True),
        sa.Column('contract_reference', sa.String(length=200), nullable=True),
        sa.Column('consultant_name', sa.String(length=255), nullable=True),
        sa.Column('subtotal', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('other_charges', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('total', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('payment_terms', sa.String(length=100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['dev_projects.id'], ),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_number')
        )
    if not _has_table('migration_fields'):
        op.create_table('migration_fields',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('field_id', sa.String(length=20), nullable=False),
        sa.Column('schema_id', sa.UUID(), nullable=False),
        sa.Column('table_name', sa.String(length=200), nullable=False),
        sa.Column('field_name', sa.String(length=200), nullable=False),
        sa.Column('data_type', sa.String(length=100), nullable=True),
        sa.Column('max_length', sa.Integer(), nullable=True),
        sa.Column('is_nullable', sa.Boolean(), nullable=True),
        sa.Column('is_primary_key', sa.Boolean(), nullable=True),
        sa.Column('is_foreign_key', sa.Boolean(), nullable=True),
        sa.Column('fk_references', sa.String(length=500), nullable=True),
        sa.Column('profiling_result', sa.JSON(), nullable=True),
        sa.Column('null_percentage', sa.Float(), nullable=True),
        sa.Column('unique_percentage', sa.Float(), nullable=True),
        sa.Column('pattern_detected', sa.String(length=100), nullable=True),
        sa.Column('sample_values', sa.JSON(), nullable=True),
        sa.Column('is_pii', sa.Boolean(), nullable=True),
        sa.Column('pii_type', sa.String(length=50), nullable=True),
        sa.Column('foia_exemption', sa.String(length=20), nullable=True),
        sa.Column('business_description', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['schema_id'], ['migration_schemas.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('field_id')
        )
    if not _has_table('migration_logic_artifacts'):
        op.create_table('migration_logic_artifacts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('artifact_id', sa.String(length=20), nullable=False),
        sa.Column('schema_id', sa.UUID(), nullable=False),
        sa.Column('artifact_type', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('raw_content', sa.Text(), nullable=True),
        sa.Column('extracted_rules', sa.JSON(), nullable=True),
        sa.Column('dependencies', sa.JSON(), nullable=True),
        sa.Column('transformations', sa.JSON(), nullable=True),
        sa.Column('is_dead_code', sa.Boolean(), nullable=True),
        sa.Column('severity', sa.String(length=20), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('model_used', sa.String(length=50), nullable=True),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['schema_id'], ['migration_schemas.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('artifact_id')
        )
    if not _has_table('migration_mappings'):
        op.create_table('migration_mappings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('mapping_id', sa.String(length=20), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('source_schema_id', sa.UUID(), nullable=False),
        sa.Column('source_table', sa.String(length=200), nullable=False),
        sa.Column('source_field', sa.String(length=200), nullable=False),
        sa.Column('source_type', sa.String(length=100), nullable=True),
        sa.Column('target_schema_id', sa.UUID(), nullable=True),
        sa.Column('target_table', sa.String(length=200), nullable=True),
        sa.Column('target_field', sa.String(length=200), nullable=True),
        sa.Column('target_type', sa.String(length=100), nullable=True),
        sa.Column('transformation_rule', sa.Text(), nullable=True),
        sa.Column('transformation_type', sa.String(length=50), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('alternatives', sa.JSON(), nullable=True),
        sa.Column('risk_factors', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=True),
        sa.Column('decision_id', sa.String(length=30), nullable=True),
        sa.Column('assigned_to', sa.UUID(), nullable=True),
        sa.Column('approved_by', sa.UUID(), nullable=True),
        sa.Column('approval_justification', sa.Text(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('impact_score', sa.Float(), nullable=True),
        sa.Column('affected_reports', sa.JSON(), nullable=True),
        sa.Column('affected_integrations', sa.JSON(), nullable=True),
        sa.Column('rollback_risk', sa.String(length=20), nullable=True),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('supersedes', sa.String(length=20), nullable=True),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['migration_projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_schema_id'], ['migration_schemas.id'], ),
        sa.ForeignKeyConstraint(['target_schema_id'], ['migration_schemas.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('mapping_id')
        )
    if not _has_table('opportunities'):
        op.create_table('opportunities',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('notice_id', sa.String(length=200), nullable=True),
        sa.Column('solicitation_number', sa.String(length=200), nullable=True),
        sa.Column('title', sa.String(length=2000), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('source', postgresql.ENUM('SAM_GOV', 'STATE', 'LOCAL', 'GSA_EBUY', 'GRANTS_GOV', 'MANUAL', name='opportunitysource', create_type=False), nullable=False),
        sa.Column('opportunity_type', sa.String(length=100), nullable=True),
        sa.Column('department', sa.String(length=500), nullable=True),
        sa.Column('sub_tier', sa.String(length=500), nullable=True),
        sa.Column('office', sa.String(length=500), nullable=True),
        sa.Column('naics_code', sa.String(length=20), nullable=True),
        sa.Column('classification_code', sa.String(length=20), nullable=True),
        sa.Column('set_aside', sa.String(length=200), nullable=True),
        sa.Column('set_aside_description', sa.String(length=500), nullable=True),
        sa.Column('posted_date', sa.Date(), nullable=True),
        sa.Column('response_deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archive_date', sa.Date(), nullable=True),
        sa.Column('estimated_value', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('award_number', sa.String(length=200), nullable=True),
        sa.Column('award_amount', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('awardee_name', sa.String(length=500), nullable=True),
        sa.Column('awardee_uei', sa.String(length=50), nullable=True),
        sa.Column('contact_name', sa.String(length=255), nullable=True),
        sa.Column('contact_email', sa.String(length=255), nullable=True),
        sa.Column('contact_phone', sa.String(length=50), nullable=True),
        sa.Column('place_of_performance_state', sa.String(length=50), nullable=True),
        sa.Column('place_of_performance_city', sa.String(length=255), nullable=True),
        sa.Column('place_of_performance_zip', sa.String(length=20), nullable=True),
        sa.Column('sam_url', sa.String(length=2000), nullable=True),
        sa.Column('resource_links', sa.JSON(), nullable=True),
        sa.Column('status', postgresql.ENUM('NEW', 'REVIEWING', 'MATCHED', 'PURSUING', 'BID_SUBMITTED', 'WON', 'LOST', 'NO_BID', 'EXPIRED', name='opportunitystatus', create_type=False), nullable=False),
        sa.Column('match_score', sa.Integer(), nullable=True),
        sa.Column('match_reasons', sa.JSON(), nullable=True),
        sa.Column('assigned_to', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('raw_data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('opportunities', 'ix_opportunities_notice_id'):
        op.create_index(op.f('ix_opportunities_notice_id'), 'opportunities', ['notice_id'], unique=False)
    if not _has_table('outputs'):
        op.create_table('outputs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('document_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('model_used', sa.String(length=50), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('outputs', 'idx_output_tenant_user'):
        op.create_index('idx_output_tenant_user', 'outputs', ['tenant_id', 'user_id'], unique=False)
    if not _has_index('outputs', 'ix_outputs_document_id'):
        op.create_index(op.f('ix_outputs_document_id'), 'outputs', ['document_id'], unique=False)
    if not _has_index('outputs', 'ix_outputs_tenant_id'):
        op.create_index(op.f('ix_outputs_tenant_id'), 'outputs', ['tenant_id'], unique=False)
    if not _has_index('outputs', 'ix_outputs_user_id'):
        op.create_index(op.f('ix_outputs_user_id'), 'outputs', ['user_id'], unique=False)
    if not _has_table('process_jobs'):
        op.create_table('process_jobs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('context_id', sa.String(), nullable=True),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('job_type', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=True),
        sa.Column('input_params', sa.JSON(), nullable=True),
        sa.Column('output_id', sa.String(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=True),
        sa.Column('max_retries', sa.Integer(), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True),
        sa.Column('model_used', sa.String(length=100), nullable=True),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key')
        )
    if not _has_index('process_jobs', 'ix_jobs_idempotency'):
        op.create_index('ix_jobs_idempotency', 'process_jobs', ['idempotency_key'], unique=True)
    if not _has_index('process_jobs', 'ix_jobs_status'):
        op.create_index('ix_jobs_status', 'process_jobs', ['status'], unique=False)
    if not _has_index('process_jobs', 'ix_jobs_tenant'):
        op.create_index('ix_jobs_tenant', 'process_jobs', ['tenant_id'], unique=False)
    if not _has_table('quotes'):
        op.create_table('quotes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=False),
        sa.Column('quote_number', sa.String(length=50), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', postgresql.ENUM('DRAFT', 'FINAL', 'SUBMITTED', 'SUPERSEDED', name='quotestatus', create_type=False), nullable=False),
        sa.Column('total_sell_price', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('total_cost', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('overall_margin_pct', sa.Float(), nullable=True),
        sa.Column('total_tax', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('shipping_cost', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('document_path', sa.String(length=1000), nullable=True),
        sa.Column('is_locked', sa.Boolean(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('quote_number')
        )
    if not _has_table('supplier_quote_files'):
        op.create_table('supplier_quote_files',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=True),
        sa.Column('file_name', sa.String(length=500), nullable=False),
        sa.Column('file_type', sa.String(length=50), nullable=True),
        sa.Column('file_content', sa.Text(), nullable=True),
        sa.Column('supplier_name', sa.String(length=500), nullable=True),
        sa.Column('total_quoted', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('quote_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('supplier_quote_requests'):
        op.create_table('supplier_quote_requests',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('project_id', sa.UUID(), nullable=True),
        sa.Column('supplier_id', sa.UUID(), nullable=True),
        sa.Column('supplier_name', sa.String(length=500), nullable=False),
        sa.Column('requested_date', sa.Date(), nullable=False),
        sa.Column('received', sa.Boolean(), nullable=False),
        sa.Column('received_date', sa.Date(), nullable=True),
        sa.Column('status', postgresql.ENUM('PENDING', 'RECEIVED', 'DELAYED', 'NOT_NEEDED', name='supplierquotestatus', create_type=False), nullable=False),
        sa.Column('quoted_amount', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['dev_projects.id'], ),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('tasks'):
        op.create_table('tasks',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('assigned_to', sa.String(length=255), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('status', postgresql.ENUM('PENDING', 'IN_PROGRESS', 'COMPLETED', 'OVERDUE', name='taskstatus', create_type=False), nullable=False),
        sa.Column('task_type', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('transcripts'):
        op.create_table('transcripts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('audio_file_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('full_text', sa.Text(), nullable=False),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('language', sa.String(length=10), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('segments', sa.JSON(), nullable=True),
        sa.Column('model_used', sa.String(length=50), nullable=True),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['audio_file_id'], ['audio_files.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('transcripts', 'ix_transcripts_audio_file_id'):
        op.create_index(op.f('ix_transcripts_audio_file_id'), 'transcripts', ['audio_file_id'], unique=False)
    if not _has_index('transcripts', 'ix_transcripts_tenant_id'):
        op.create_index(op.f('ix_transcripts_tenant_id'), 'transcripts', ['tenant_id'], unique=False)
    if not _has_index('transcripts', 'ix_transcripts_user_id'):
        op.create_index(op.f('ix_transcripts_user_id'), 'transcripts', ['user_id'], unique=False)
    if not _has_table('contract_staffing'):
        op.create_table('contract_staffing',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('contract_id', sa.UUID(), nullable=False),
        sa.Column('employee_id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(length=255), nullable=True),
        sa.Column('billing_rate', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('hours_monthly', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('decisions'):
        op.create_table('decisions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('context_id', sa.String(), nullable=True),
        sa.Column('job_id', sa.String(), nullable=True),
        sa.Column('decision_text', sa.Text(), nullable=False),
        sa.Column('options_considered', sa.JSON(), nullable=True),
        sa.Column('selected_option', sa.Text(), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('decided_by', sa.String(length=255), nullable=True),
        sa.Column('decision_date', sa.DateTime(), nullable=True),
        sa.Column('confidence_score', sa.Float(), nullable=True),
        sa.Column('accuracy_score', sa.Float(), nullable=True),
        sa.Column('reliability', sa.String(length=20), nullable=True),
        sa.Column('model_name', sa.String(length=100), nullable=True),
        sa.Column('model_version', sa.String(length=50), nullable=True),
        sa.Column('prompt_version', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('approved_by', sa.String(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('approval_justification', sa.Text(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('rejection_category', sa.String(length=100), nullable=True),
        sa.Column('superseded_by', sa.String(), nullable=True),
        sa.Column('supersedes', sa.String(), nullable=True),
        sa.Column('sla_hours', sa.Integer(), nullable=True),
        sa.Column('deadline', sa.DateTime(), nullable=True),
        sa.Column('escalation_level', sa.Integer(), nullable=True),
        sa.Column('escalated_to', sa.String(), nullable=True),
        sa.Column('escalated_at', sa.DateTime(), nullable=True),
        sa.Column('is_overdue', sa.Boolean(), nullable=True),
        sa.Column('outcome_text', sa.Text(), nullable=True),
        sa.Column('outcome_date', sa.DateTime(), nullable=True),
        sa.Column('outcome_matched', sa.Boolean(), nullable=True),
        sa.Column('outcome_notes', sa.Text(), nullable=True),
        sa.Column('outcome_recorded_by', sa.String(), nullable=True),
        sa.Column('required_approver_role', sa.String(length=50), nullable=True),
        sa.Column('approval_threshold_usd', sa.Float(), nullable=True),
        sa.Column('domain', sa.String(length=50), nullable=True),
        sa.Column('stakeholders', sa.JSON(), nullable=True),
        sa.Column('alignment_score', sa.Float(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('is_immutable', sa.Boolean(), nullable=True),
        sa.Column('correlation_id', sa.String(length=20), nullable=True),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['process_jobs.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('decisions', 'ix_decisions_context'):
        op.create_index('ix_decisions_context', 'decisions', ['context_id'], unique=False)
    if not _has_index('decisions', 'ix_decisions_status'):
        op.create_index('ix_decisions_status', 'decisions', ['status'], unique=False)
    if not _has_index('decisions', 'ix_decisions_tenant'):
        op.create_index('ix_decisions_tenant', 'decisions', ['tenant_id'], unique=False)
    if not _has_table('expenses'):
        op.create_table('expenses',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('category', postgresql.ENUM('SALARY', 'BENEFITS', 'IMMIGRATION', 'RENT', 'UTILITIES', 'SOFTWARE', 'TRAVEL', 'EQUIPMENT', 'INSURANCE', 'OTHER', name='expensecategory', create_type=False), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('expense_date', sa.Date(), nullable=False),
        sa.Column('contract_id', sa.UUID(), nullable=True),
        sa.Column('employee_id', sa.UUID(), nullable=True),
        sa.Column('is_corporate', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('financials'):
        op.create_table('financials',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=False),
        sa.Column('quote_id', sa.UUID(), nullable=True),
        sa.Column('stage', postgresql.ENUM('QUOTED', 'AWARDED', 'ORDERED', 'INVOICED', 'PAID', name='financialstage', create_type=False), nullable=False),
        sa.Column('po_number', sa.String(length=200), nullable=True),
        sa.Column('invoice_number', sa.String(length=200), nullable=True),
        sa.Column('invoice_amount', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('payment_date', sa.Date(), nullable=True),
        sa.Column('payment_amount', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['quote_id'], ['quotes.id'], ),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('invoice_line_items'):
        op.create_table('invoice_line_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('invoice_id', sa.UUID(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('unit', sa.String(length=50), nullable=False),
        sa.Column('rate', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('migration_mapping_versions'):
        op.create_table('migration_mapping_versions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('mapping_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('change_type', sa.String(length=30), nullable=True),
        sa.Column('changed_by', sa.UUID(), nullable=False),
        sa.Column('change_reason', sa.Text(), nullable=True),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['mapping_id'], ['migration_mappings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('migration_profiling_results'):
        op.create_table('migration_profiling_results',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('profiling_id', sa.String(length=20), nullable=False),
        sa.Column('field_id', sa.UUID(), nullable=False),
        sa.Column('schema_id', sa.UUID(), nullable=False),
        sa.Column('total_records', sa.Integer(), nullable=True),
        sa.Column('null_count', sa.Integer(), nullable=True),
        sa.Column('unique_count', sa.Integer(), nullable=True),
        sa.Column('min_length', sa.Integer(), nullable=True),
        sa.Column('max_length', sa.Integer(), nullable=True),
        sa.Column('avg_length', sa.Float(), nullable=True),
        sa.Column('patterns_detected', sa.JSON(), nullable=True),
        sa.Column('value_distribution', sa.JSON(), nullable=True),
        sa.Column('format_variants', sa.JSON(), nullable=True),
        sa.Column('outliers', sa.JSON(), nullable=True),
        sa.Column('pii_detected', sa.Boolean(), nullable=True),
        sa.Column('pii_type', sa.String(length=50), nullable=True),
        sa.Column('pii_confidence', sa.Float(), nullable=True),
        sa.Column('module_id', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['field_id'], ['migration_fields.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['schema_id'], ['migration_schemas.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('profiling_id')
        )
    if not _has_table('purchase_orders'):
        op.create_table('purchase_orders',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('po_number', sa.String(length=100), nullable=False),
        sa.Column('rfq_id', sa.UUID(), nullable=True),
        sa.Column('quote_id', sa.UUID(), nullable=True),
        sa.Column('supplier_id', sa.UUID(), nullable=True),
        sa.Column('supplier_name', sa.String(length=500), nullable=True),
        sa.Column('total_cost', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('total_sell', sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column('status', postgresql.ENUM('INTAKE', 'QUOTED', 'SUBMITTED', 'WON', 'LOST', 'ORDERED', 'SHIPPED', 'DELIVERED', 'CANCELLED', name='dealstatus', create_type=False), nullable=False),
        sa.Column('ordered_date', sa.Date(), nullable=True),
        sa.Column('shipped_date', sa.Date(), nullable=True),
        sa.Column('delivered_date', sa.Date(), nullable=True),
        sa.Column('tracking_number', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['quote_id'], ['quotes.id'], ),
        sa.ForeignKeyConstraint(['rfq_id'], ['rfqs.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('quote_line_items'):
        op.create_table('quote_line_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('quote_id', sa.UUID(), nullable=False),
        sa.Column('bom_item_id', sa.UUID(), nullable=True),
        sa.Column('supplier_id', sa.UUID(), nullable=True),
        sa.Column('part_number', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('unit_cost', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('inbound_freight', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('duty_rate', sa.Float(), nullable=False),
        sa.Column('handling_fee', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('forex_buffer_pct', sa.Float(), nullable=False),
        sa.Column('landed_cost', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('sell_price', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('margin_pct', sa.Float(), nullable=False),
        sa.Column('tax_amount', sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column('is_override', sa.Boolean(), nullable=False),
        sa.Column('override_justification', sa.Text(), nullable=True),
        sa.Column('deal_registration_id', sa.UUID(), nullable=True),
        sa.Column('snapshot_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['bom_item_id'], ['bom_items.id'], ),
        sa.ForeignKeyConstraint(['deal_registration_id'], ['deal_registrations.id'], ),
        sa.ForeignKeyConstraint(['quote_id'], ['quotes.id'], ),
        sa.ForeignKeyConstraint(['snapshot_id'], ['supplier_price_snapshots.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_table('actions'):
        op.create_table('actions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('decision_id', sa.String(), nullable=True),
        sa.Column('context_id', sa.String(), nullable=True),
        sa.Column('job_id', sa.String(), nullable=True),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('owner', sa.String(length=255), nullable=True),
        sa.Column('deadline', sa.DateTime(), nullable=True),
        sa.Column('priority', sa.String(length=20), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('executed_by', sa.String(), nullable=True),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.Column('execution_result', sa.JSON(), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('requires_approval', sa.Boolean(), nullable=True),
        sa.Column('approved_by', sa.String(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('correlation_id', sa.String(length=20), nullable=True),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.id'], ),
        sa.ForeignKeyConstraint(['decision_id'], ['decisions.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['process_jobs.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('actions', 'ix_actions_decision'):
        op.create_index('ix_actions_decision', 'actions', ['decision_id'], unique=False)
    if not _has_index('actions', 'ix_actions_status'):
        op.create_index('ix_actions_status', 'actions', ['status'], unique=False)
    if not _has_index('actions', 'ix_actions_tenant'):
        op.create_index('ix_actions_tenant', 'actions', ['tenant_id'], unique=False)
    if not _has_table('execution_queue'):
        op.create_table('execution_queue',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('action_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=True),
        sa.Column('max_attempts', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('scheduled_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['action_id'], ['actions.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('execution_queue', 'ix_eq_priority'):
        op.create_index('ix_eq_priority', 'execution_queue', ['priority'], unique=False)
    if not _has_index('execution_queue', 'ix_eq_status'):
        op.create_index('ix_eq_status', 'execution_queue', ['status'], unique=False)
    if not _has_index('execution_queue', 'ix_eq_tenant'):
        op.create_index('ix_eq_tenant', 'execution_queue', ['tenant_id'], unique=False)
    if not _has_table('traceability'):
        op.create_table('traceability',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('decision_id', sa.String(), nullable=True),
        sa.Column('action_id', sa.String(), nullable=True),
        sa.Column('output_id', sa.String(), nullable=True),
        sa.Column('source_document', sa.String(length=500), nullable=True),
        sa.Column('source_type', sa.String(length=50), nullable=True),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('timestamp_ref', sa.String(length=50), nullable=True),
        sa.Column('quote', sa.Text(), nullable=True),
        sa.Column('section', sa.String(length=255), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('match_method', sa.String(length=50), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=True),
        sa.Column('verified_by', sa.String(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['action_id'], ['actions.id'], ),
        sa.ForeignKeyConstraint(['decision_id'], ['decisions.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not _has_index('traceability', 'ix_trace_decision'):
        op.create_index('ix_trace_decision', 'traceability', ['decision_id'], unique=False)
    if not _has_index('traceability', 'ix_trace_tenant'):
        op.create_index('ix_trace_tenant', 'traceability', ['tenant_id'], unique=False)

    # Model-declared indexes on tables the chain already creates.
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

    # `review_decision_events` exists on both paths, but with different index
    # sets: a database built by the chain gets these three from 20260825, while
    # one where startup's create_all() made the table does not have them.
    if not _has_index('review_decision_events', 'ix_review_decision_events_review_id'):
        op.create_index(op.f('ix_review_decision_events_review_id'), 'review_decision_events', ['review_id'], unique=False)
    if not _has_index('review_decision_events', 'ix_review_decision_events_event_type'):
        op.create_index(op.f('ix_review_decision_events_event_type'), 'review_decision_events', ['event_type'], unique=False)
    if not _has_index('review_decision_events', 'ix_review_decision_events_actor_user_id'):
        op.create_index(op.f('ix_review_decision_events_actor_user_id'), 'review_decision_events', ['actor_user_id'], unique=False)

    # `rce_issues.issue_code` ends up guaranteed unique three times over: the
    # UNIQUE constraint 20260822 declared on the column, the plain index it also
    # created, and the unique index the model asks for and that this revision
    # created above. Three b-trees on one column, all maintained on every write.
    # Keep the one the model declares and drop the other two — uniqueness is
    # never unenforced, because the drop only runs once the unique index exists.
    _drop_constraint_if_redundant('rce_issues', 'rce_issues_issue_code_key',
                                  covered_by='ix_rce_issues_issue_code')
    if (_has_index('rce_issues', 'ix_rce_issues_issue_code')
            and _has_index('rce_issues', 'idx_rce_issue_code')):
        op.drop_index('idx_rce_issue_code', table_name='rce_issues')


def downgrade() -> None:
    # Put back what 20260822_rce_pipeline declared, before anything else, so the
    # column is never left without its original guarantees.
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

    _drop_table_if_present('traceability')
    _drop_table_if_present('execution_queue')
    _drop_table_if_present('actions')
    _drop_table_if_present('quote_line_items')
    _drop_table_if_present('purchase_orders')
    _drop_table_if_present('migration_profiling_results')
    _drop_table_if_present('migration_mapping_versions')
    _drop_table_if_present('invoice_line_items')
    _drop_table_if_present('financials')
    _drop_table_if_present('expenses')
    _drop_table_if_present('decisions')
    _drop_table_if_present('contract_staffing')
    _drop_table_if_present('transcripts')
    _drop_table_if_present('tasks')
    _drop_table_if_present('supplier_quote_requests')
    _drop_table_if_present('supplier_quote_files')
    _drop_table_if_present('quotes')
    _drop_table_if_present('process_jobs')
    _drop_table_if_present('outputs')
    _drop_table_if_present('opportunities')
    _drop_table_if_present('migration_mappings')
    _drop_table_if_present('migration_logic_artifacts')
    _drop_table_if_present('migration_fields')
    _drop_table_if_present('invoices')
    _drop_table_if_present('follow_up_queue')
    _drop_table_if_present('deal_registrations')
    _drop_table_if_present('contracts')
    _drop_table_if_present('communication_logs')
    _drop_table_if_present('bom_items')
    _drop_table_if_present('agency_contacts')
    _drop_table_if_present('tenant_users')
    _drop_table_if_present('supplier_price_snapshots')
    _drop_table_if_present('supplier_metrics')
    _drop_table_if_present('supplier_contacts')
    _drop_table_if_present('submissions')
    _drop_table_if_present('saved_searches')
    _drop_table_if_present('rfqs')
    _drop_table_if_present('policy_validations')
    _drop_table_if_present('placement_outcomes')
    _drop_table_if_present('outreach_logs')
    _drop_table_if_present('migration_validation_runs')
    _drop_table_if_present('migration_schemas')
    _drop_table_if_present('migration_manifest_versions')
    _drop_table_if_present('documents')
    _drop_table_if_present('contexts')
    _drop_table_if_present('cm_notes')
    _drop_table_if_present('cm_discharge_records')
    _drop_table_if_present('cm_care_plans')
    _drop_table_if_present('cm_billing_summaries')
    _drop_table_if_present('bench_candidates')
    _drop_table_if_present('audit_logs')
    _drop_table_if_present('audio_files')
    _drop_table_if_present('ats_activities')
    _drop_table_if_present('applications')
    _drop_table_if_present('ai_memory')
    _drop_table_if_present('validation_queue')
    _drop_table_if_present('users')
    _drop_table_if_present('tenants')
    _drop_table_if_present('tefca_import_history')
    _drop_table_if_present('technical_library')
    _drop_table_if_present('tax_jurisdictions')
    _drop_table_if_present('support_tickets')
    _drop_table_if_present('suppliers')
    _drop_table_if_present('state_audit_log')
    _drop_table_if_present('proposal_library')
    _drop_table_if_present('products')
    _drop_table_if_present('product_catalog')
    _drop_table_if_present('price_history')
    _drop_table_if_present('output_templates')
    _drop_table_if_present('migration_projects')
    _drop_table_if_present('job_postings')
    _drop_table_if_present('employees')
    _drop_table_if_present('dev_projects')
    _drop_table_if_present('customers')
    _drop_table_if_present('company_profiles')
    _drop_table_if_present('cm_patients')
    _drop_table_if_present('cm_government_cases')
    _drop_table_if_present('candidates')
    _drop_table_if_present('audit_log')
    _drop_table_if_present('agency_metrics')

    for old, new, table in INDEX_RENAMES:
        _rename_index(table, new, old)

    # Some of these types are also used by columns on tables this revision did
    # not create — `casestatus` is on `tefca_priority_cases`, which the chain
    # owns. Dropping the type would fail and take the whole downgrade with it,
    # so a type that something still depends on is left in place. That is the
    # correct outcome: the type is still in use.
    for name, _values in ENUM_TYPES:
        op.execute(
            "DO $$ BEGIN "
            "  BEGIN "
            '    DROP TYPE IF EXISTS "%s"; '
            "  EXCEPTION WHEN dependent_objects_still_exist THEN NULL; "
            "  END; "
            "END $$;" % name)
