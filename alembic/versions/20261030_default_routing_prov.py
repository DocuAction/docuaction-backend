"""Deterministic provenance backfill for default-routed classifications.

Revision ID: 20261030_default_routing_prov
Revises: 20261012_sod_db_enforcement
Create Date: 2026-10-30

STATUS: DRAFT. Written for review. NOT applied to DEV or PROD by this change; it
must go through the governed migration pipeline after owner approval.

WHY THIS EXISTS
---------------
Three writers persist a classified ReviewRecord. Two of them — the bulk pipeline
(`arc_pipeline.verify_and_classify`) and shadow reassessment — already record the
classifier's "no rule matched" default under the documented reserved code
`DEFAULT-UNMATCHED` v0 (arc_pipeline.py, "THE UNMATCHED PATH STILL HAS TO CITE
ITS PROVENANCE"). The third — the Priority Review / ad-hoc single-entity path,
`review_service.run_review` — stored the raw classifier output, i.e.
`classification_rule = NULL`, for the same documented default. Reconciliation's
"Every determination cites a rule" check then flags the row as provenance-less,
which is half right: the routing WAS documented (the persisted rationale is the
classifier's own default-path statement), only the reserved code was missing.

On DEV exactly one row matches: REV-2026-001521 (measured 2026-10-30 via the
delivery's review-records export: 2,566 rows, 1 with a bucket and no rule, 0
already carrying DEFAULT-UNMATCHED).

WHAT THIS CHANGES
-----------------
Data only; no schema change.

1. Rows whose own persisted rationale is the classifier default-path text
   (`bucket_classifier.BucketClassifier.classify`, unmatched branch) and whose
   rule is NULL get `classification_rule = 'DEFAULT-UNMATCHED'`,
   `classification_rule_version = 0`. The bucket, the rationale and every other
   column are untouched — the historical determination is not rewritten, the
   provenance the row already states in prose is recorded in the column
   reconciliation reads.

2. One `tefca_reg_audit_log` row per updated review
   (action `classification_provenance_backfill`) naming the review_id, the
   before/after values and this revision — the backfill is attributable, not
   silent.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
* A classified row with a NULL rule whose rationale does NOT carry the
  classifier's default text is left exactly as it is. Its provenance is
  genuinely unknown, and "Every determination cites a rule" must keep failing
  for it. Uncertainty is retained, not papered over.
* No `reviewer_resolution` is set. The companion reconciliation check added in
  this PR ("Every default-routed determination is adjudicated") keeps
  REV-2026-001521 visibly failing until an analyst resolves it through the
  audited confirm/reclassify path.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20261030_default_routing_prov"
down_revision = "20261012_sod_db_enforcement"
branch_labels = None
depends_on = None

#: The reserved code the other two classification writers already use
#: (`arc_pipeline.UNMATCHED_RULE_CODE` / `UNMATCHED_RULE_VERSION`). Restated
#: here as literals because a migration must stay replayable after the
#: application code moves on.
DEFAULT_ROUTING_CODE = "DEFAULT-UNMATCHED"
DEFAULT_ROUTING_VERSION = 0

#: The exact opening of `bucket_classifier`'s unmatched-branch rationale. The
#: single-entity path appends evidence provenance AFTER this text and prefixes
#: nothing, while the bulk path prefixes "[DEFAULT-UNMATCHED] " (and always sets
#: the rule code anyway) — so this prefix match selects precisely the rows the
#: single-entity path default-routed, and nothing else.
DEFAULT_RATIONALE_PREFIX = (
    "No active rule matched these results, so the review defaults to B3 "
    "for manual examination rather than being passed silently.%")

SELECT_LEGACY_SQL = (
    "SELECT review_id, entity_id, classification_bucket "
    "FROM review_records "
    "WHERE classification_bucket IS NOT NULL "
    "  AND classification_rule IS NULL "
    "  AND classification_rationale LIKE :p")

BACKFILL_UPDATE_SQL = (
    "UPDATE review_records "
    "SET classification_rule = :code, classification_rule_version = :ver "
    "WHERE review_id = :rid "
    "  AND classification_bucket IS NOT NULL "
    "  AND classification_rule IS NULL "
    "  AND classification_rationale LIKE :p")

AUDIT_INSERT_SQL = (
    "INSERT INTO tefca_reg_audit_log "
    "(id, entity_id, action, actor_email, metadata, created_at) "
    "VALUES (:id, :entity_id, :action, :actor, CAST(:meta AS jsonb), now())")

AUDIT_ACTION = "classification_provenance_backfill"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(SELECT_LEGACY_SQL), {"p": DEFAULT_RATIONALE_PREFIX}).fetchall()
    for review_id, entity_id, bucket in rows:
        conn.execute(sa.text(BACKFILL_UPDATE_SQL), {
            "rid": review_id, "code": DEFAULT_ROUTING_CODE,
            "ver": DEFAULT_ROUTING_VERSION, "p": DEFAULT_RATIONALE_PREFIX})
        conn.execute(sa.text(AUDIT_INSERT_SQL), {
            "id": str(uuid.uuid4()),
            "entity_id": str(entity_id) if entity_id else None,
            "action": AUDIT_ACTION,
            "actor": "migration",
            "meta": json.dumps({
                "review_id": review_id,
                "bucket": bucket,
                "classification_rule": {"from": None,
                                        "to": DEFAULT_ROUTING_CODE},
                "classification_rule_version": {"from": None,
                                                "to": DEFAULT_ROUTING_VERSION},
                "basis": ("classification_rationale carries the classifier's "
                          "default-path text verbatim"),
                "migration": revision,
            })})


def downgrade() -> None:
    # Revert exactly the rows this migration touched, identified by their own
    # audit rows. The audit rows themselves are kept: the log is append-only,
    # and a reverted backfill is still a thing that happened.
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE review_records SET classification_rule = NULL, "
        "classification_rule_version = NULL "
        "WHERE classification_rule = :code "
        "  AND review_id IN (SELECT metadata->>'review_id' "
        "                    FROM tefca_reg_audit_log "
        "                    WHERE action = :action "
        "                      AND metadata->>'migration' = :rev)"),
        {"code": DEFAULT_ROUTING_CODE, "action": AUDIT_ACTION,
         "rev": revision})
