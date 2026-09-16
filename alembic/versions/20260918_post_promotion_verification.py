"""Post-promotion verification: audit columns on rce_issues.

Revision ID: 20260918_post_promotion_verification
Revises: 20260917_delivery_traceability
Create Date: 2026-09-18

WHY THIS EXISTS
---------------
Decision 2 of the pre-merge review (2026-09-16) requires that a finding
discovered AFTER a record has already been promoted (a deactivated NPI, an
invalid active identifier, a material identifier conflict a later
verification cycle surfaces) is recorded with "actor, timestamps, rule,
evidence source, build SHA, request/trace ID, and before/after state" -
without ever touching the original promotion event. `rce_issues` already
carries actor-ish fields for the DECIDE side (`resolved_by`, `resolved_at`,
`qa_approved_by`, `qa_approved_at`) and the RULE side (`rule_id`, `issue_type`,
`suggested_source`); it has nowhere to put the correlation id, the build SHA
that recorded the finding, or the before/after state of what changed. This
migration adds exactly those four columns, nullable, additive only.

No new table. The bridge module's own rule ("there is no new case table, and
there must never be one") generalises: a finding is one more fact about a
source record, and `rce_issues` is already the one ledger for facts like that
- fragmenting evidence across a second table for no reason this project has
not already rejected once.

OWNERSHIP
---------
`rce_issues` is a pre-existing, actively-written table. The precedent
migration `20260915_curated_text_columns` altered columns on tables of the
same class (`rce_curated_records`, `tefca_reg_entities`,
`tefca_entity_contacts`) with a plain `op.alter_column` and no ownership
precondition, and that migration applied successfully on the real DEV
database. A nullable `ADD COLUMN` is strictly safer than an `ALTER COLUMN`
type change (no rewrite, no truncation risk), so the same precedent applies
here without new machinery. If the migration role does not own this table,
PostgreSQL refuses the `ALTER TABLE` outright with a clear permission error -
loud, not silent - rather than this migration adding a bespoke precondition
for a risk the prior migration already accepted for a materially similar
table.

SAFETY
------
Four nullable columns, no default, no backfill, no constraint of their own.
Existing rows read back with these columns NULL, exactly as absent evidence
should read. Downgrade drops them; nothing but this migration's own columns
is affected there, so a downgrade cannot destroy evidence the way dropping an
append-only evidence TABLE would - these columns evidence a finding, they are
not the finding itself (`issue_code`, `description`, `resolution` already
are, and this migration does not touch them).

Also widens `rce_reconciliation_snapshots.ck_rce_snapshot_trigger` to accept
the two new snapshot triggers Decision 2 needs (see
`traceability_models.SNAPSHOT_TRIGGERS`). The downgrade narrows it back only
when no snapshot row actually uses a new trigger value, refusing otherwise
rather than deleting or invalidating evidence rows.
"""
import sqlalchemy as sa
from alembic import op

#: Kept at or under 32 characters: `alembic_version.version_num` is
#: VARCHAR(32) and a revision id that does not fit fails the UPDATE with no
#: hint that length was the problem.
revision = "20260918_pp_verification"
down_revision = "20260917_delivery_traceability"
branch_labels = None
depends_on = None


def _offline() -> bool:
    return op.get_context().as_sql


def _has_column(table: str, column: str) -> bool:
    if _offline():
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


#: (name, type). All nullable; none has a default.
COLUMNS = (
    ("correlation_id", sa.String(64)),
    ("build_sha", sa.String(40)),
    ("before_state", sa.dialects.postgresql.JSONB()),
    ("after_state", sa.dialects.postgresql.JSONB()),
)


#: `rce_reconciliation_snapshots.ck_rce_snapshot_trigger` was created by the
#: PRIOR revision (20260917) with the narrower vocabulary. Both revisions are
#: part of the same unmerged branch, so widening it here — rather than a
#: later migration altering a constraint something else may already depend
#: on — carries no compatibility risk. Guarded on the constraint's actual
#: definition, not just its name, so a second run is a no-op.
_NEW_TRIGGERS = ("PIPELINE", "DISPOSITION", "MANUAL", "RECONSTRUCTION",
                 "POST_PROMOTION_VERIFICATION", "POST_PROMOTION_RESOLUTION")


def _trigger_check_is_current() -> bool:
    if _offline():
        return False
    bind = op.get_bind()
    definition = bind.execute(sa.text(
        "select pg_get_constraintdef(oid) from pg_constraint "
        "where conname = 'ck_rce_snapshot_trigger'")).scalar()
    return definition is not None and "POST_PROMOTION_VERIFICATION" in definition


def upgrade() -> None:
    for name, type_ in COLUMNS:
        if not _has_column("rce_issues", name):
            op.add_column("rce_issues", sa.Column(name, type_, nullable=True))
    if not _trigger_check_is_current():
        op.drop_constraint("ck_rce_snapshot_trigger", "rce_reconciliation_snapshots",
                           type_="check")
        op.create_check_constraint(
            "ck_rce_snapshot_trigger", "rce_reconciliation_snapshots",
            "trigger IN (%s)" % ", ".join("'%s'" % v for v in _NEW_TRIGGERS))


class DowngradeWouldOrphanSnapshotsError(RuntimeError):
    """A downgrade would leave a snapshot row violating the narrower CHECK."""


def downgrade() -> None:
    if not _offline():
        bind = op.get_bind()
        using_new = bind.execute(sa.text(
            "select count(*) from rce_reconciliation_snapshots where trigger in "
            "('POST_PROMOTION_VERIFICATION', 'POST_PROMOTION_RESOLUTION')")).scalar()
        if using_new:
            raise DowngradeWouldOrphanSnapshotsError(
                f"{using_new} reconciliation snapshot(s) use a trigger this "
                f"downgrade would remove from the allowed set. Narrowing the "
                f"constraint would either violate it or require deleting "
                f"evidence rows, which this migration refuses to do.")
    if _trigger_check_is_current():
        op.drop_constraint("ck_rce_snapshot_trigger", "rce_reconciliation_snapshots",
                           type_="check")
        op.create_check_constraint(
            "ck_rce_snapshot_trigger", "rce_reconciliation_snapshots",
            "trigger IN (%s)" % ", ".join(
                "'%s'" % v for v in ("PIPELINE", "DISPOSITION", "MANUAL", "RECONSTRUCTION")))
    for name, _type in COLUMNS:
        if _offline() or _has_column("rce_issues", name):
            op.drop_column("rce_issues", name)
