"""review_records: ownership, an Area 1 anchor, and pre-promotion review.

Revision ID: 20260831_review_case
Revises: 20260830_run_lifecycle
Create Date: 2026-08-29

WHAT THIS FIXES
---------------
Two blockers proven against the delivered population by the human-review
operational gate. Both were measured, not assumed.

L1 — A REVIEW CASE COULD NOT BE OWNED.
`review_records` had no ownership column at all. `tefca_analyst_queue` does have
`claimed_by`, but its `record_id` is NOT NULL against `tefca_evidence_records`,
which holds zero rows and hangs off `tefca_entities` (2 rows) — it cannot
reference a review case without manufacturing evidence rows to satisfy a foreign
key, which is a parallel case system by another name. The only writable field on
`review_records`, `verification_results`, is documented as "a SNAPSHOT taken at
review time, not a pointer to live state" — the thing a finished report cites.
Putting mutable ownership there would let a cited snapshot change underneath a
delivered report. So two analysts could work the same case and nothing would say
so.

L2 — THE RECORDS MOST NEEDING REVIEW COULD NOT BE REVIEWED.
`entity_id` was NOT NULL. A curated record is HELD precisely because it carries
an unresolved substantive problem, and `promote_delivery` deliberately promotes
only CLEAN and CORRECTED records — so a HELD record has no entity. Requiring one
meant HELD implied UNREVIEWABLE. Measured on the August delivery: of 138
HUMAN_REQUIRED findings, 6 sat on the 4 HELD records and could not open a case,
and those 6 included ALL FOUR of the delivery's HIGH-severity identity findings.

The fix is to correct the REVIEW model, not promotion. Promotion's exclusion of
HELD records is right and is untouched; nothing here promotes anything, and no
entity is synthesised to stand in for one.

WHAT THIS DOES
--------------
    entity_id            NOT NULL -> NULL      (foreign key RETAINED)
  + source_record_id     uuid NULL, indexed    Area 1 anchor
  + assigned_to_user_id  uuid NULL, indexed    ownership
  + assigned_at          timestamp NULL
  + ck_review_record_has_subject                entity OR source record

WHY NO `case_status` COLUMN
---------------------------
Because it would be a second answer to a question already answered. Ownership is
`assigned_to_user_id`. Submitted, returned, escalated and approved are already
determined by `review_decision_events` and read through `qa_gate`
(`_latest_determination`, `_qa_after`, `is_reportable`), with `reportable_at` as
the derived approval marker. A status column would have to be kept in step with
those events by convention, and the day it drifted the two would disagree with
no way to say which was right.

WHY NO FOREIGN KEYS ON THE TWO NEW UUID COLUMNS
-----------------------------------------------
Both follow conventions this schema already sets.

`assigned_to_user_id` mirrors `review_decision_events.actor_user_id` and
`review_records.reclassified_by`, which are plain UUIDs. A case must stay
attributable after the person who held it is deactivated; a foreign key would
turn deactivating a leaver into a referential problem instead of an HR one.

`source_record_id` mirrors `tefca_entity_contacts.source_record_id`, the
existing pattern for a registry table pointing at Area 1. Area 1 has no delete
path, so the reference cannot dangle, and the registry does not take an
ownership dependency on Area 1's separate role.

SAFETY
------
Additive and nullable throughout. No table is recreated, no row is rewritten, no
foreign key is dropped, no Government data is touched, and Area 1 is not
involved. The 43 existing review records all carry an `entity_id`, so every one
of them satisfies the new CHECK — verified before it is added, and the migration
refuses rather than adding a constraint that would invalidate stored rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_review_case"
down_revision = "20260830_run_lifecycle"
branch_labels = None
depends_on = None

TABLE = "review_records"
CHECK_NAME = "ck_review_record_has_subject"


class SubjectAnchorViolation(RuntimeError):
    """Stored rows would not satisfy the CHECK. Refuse rather than force it."""


def _offline() -> bool:
    return op.get_context().as_sql


def _column_names() -> set:
    if _offline():
        return set()
    return {r[0] for r in op.get_bind().execute(sa.text(
        "select column_name from information_schema.columns "
        "where table_name = :t"), {"t": TABLE})}


def _constraint_exists(name: str) -> bool:
    # This project's design has app-startup Base.metadata.create_all() run against
    # the CURRENT models, and review_records is built by an earlier registry
    # create_all migration - so on a freshly built database this CHECK constraint
    # already exists (the model declares it). Guard its creation exactly as the
    # column/index steps above are guarded, so the migration is idempotent against
    # that create_all and a fresh `alembic upgrade head` does not fail.
    if _offline():
        return False
    return op.get_bind().execute(sa.text(
        "select 1 from pg_constraint where conname = :n"), {"n": name}).first() is not None


def upgrade() -> None:
    existing = _column_names()

    # ── ownership ────────────────────────────────────────────────────────────
    if "assigned_to_user_id" not in existing:
        op.add_column(TABLE, sa.Column("assigned_to_user_id", UUID(as_uuid=True),
                                       nullable=True))
        op.create_index("idx_review_records_assignee", TABLE,
                        ["assigned_to_user_id"])
    if "assigned_at" not in existing:
        op.add_column(TABLE, sa.Column("assigned_at", sa.DateTime(),
                                       nullable=True))

    # ── Area 1 anchor ────────────────────────────────────────────────────────
    if "source_record_id" not in existing:
        op.add_column(TABLE, sa.Column("source_record_id", UUID(as_uuid=True),
                                       nullable=True))
        op.create_index("idx_review_records_source_record", TABLE,
                        ["source_record_id"])

    # ── pre-promotion review ─────────────────────────────────────────────────
    # Only the NOT NULL is lifted. `review_records_entity_id_fkey` stays, so an
    # entity that IS named still has to be a real one.
    op.alter_column(TABLE, "entity_id", existing_type=UUID(as_uuid=True),
                    nullable=True)

    # ── the case must still be ABOUT something ───────────────────────────────
    # Skip if create_all already established it (idempotent from a fresh build).
    if not _constraint_exists(CHECK_NAME):
        if not _offline():
            orphans = op.get_bind().execute(sa.text(
                f"select count(*) from {TABLE} "
                f"where entity_id is null and source_record_id is null")).scalar()
            if orphans:
                raise SubjectAnchorViolation(
                    f"{orphans} row(s) in {TABLE} would have neither an entity nor "
                    f"a source record. Adding {CHECK_NAME} would leave stored rows "
                    f"violating it; anchor them first.")
        op.create_check_constraint(
            CHECK_NAME, TABLE,
            "entity_id IS NOT NULL OR source_record_id IS NOT NULL")


def downgrade() -> None:
    """Reverse cleanly ONLY while no row depends on the new capability.

    Restoring NOT NULL on `entity_id` would fail against any pre-promotion case,
    which is correct: those rows are the reason the column was relaxed, and
    silently deleting them to make a downgrade succeed would destroy review work.
    The downgrade refuses instead and says what to do.
    """
    op.drop_constraint(CHECK_NAME, TABLE, type_="check")

    if not _offline():
        anchorless = op.get_bind().execute(sa.text(
            f"select count(*) from {TABLE} where entity_id is null")).scalar()
        if anchorless:
            raise SubjectAnchorViolation(
                f"{anchorless} review record(s) have no entity — they are "
                f"pre-promotion cases. Restoring NOT NULL would require "
                f"deleting real review work. Resolve or export them first.")

    op.alter_column(TABLE, "entity_id", existing_type=UUID(as_uuid=True),
                    nullable=False)
    op.drop_index("idx_review_records_source_record", table_name=TABLE)
    op.drop_column(TABLE, "source_record_id")
    op.drop_index("idx_review_records_assignee", table_name=TABLE)
    op.drop_column(TABLE, "assigned_at")
    op.drop_column(TABLE, "assigned_to_user_id")
