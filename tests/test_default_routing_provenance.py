"""Default-routed classifications: provenance at write time, deterministic
backfill for legacy rows, and the reconciliation pair of checks.

The design under test (2026-10-30):

* `review_service.classification_provenance` makes the single-entity path
  persist the SAME documented reserved code (`DEFAULT-UNMATCHED` v0) the bulk
  path has always written when no rule matches — never a NULL rule.
* "Every determination cites a rule" is UNCHANGED: a classified row with no
  recorded provenance still fails it. No `reviewer_resolution IS NOT NULL`
  exemption exists, deliberately.
* A NEW check, "Every default-routed determination is adjudicated", fails while
  a DEFAULT-UNMATCHED row has no analyst resolution — so a backfilled
  REV-2026-001521 stays visible until a human resolves it.
* The migration backfill is deterministic: it touches ONLY rows whose own
  persisted rationale is the classifier's default-path text, and leaves a rule-
  less row with any other rationale exactly as found (uncertainty retained).
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import text

from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import ClassificationResult
from app.tefca_registry.rce import reconciliation
from app.tefca_registry.rce.arc_pipeline import (
    UNMATCHED_RULE_CODE, UNMATCHED_RULE_VERSION)
from app.tefca_registry.review_service import classification_provenance
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

_MIGRATION = (Path(__file__).resolve().parents[1]
              / "alembic" / "versions" / "20261030_default_routing_prov.py")

#: Verbatim from `bucket_classifier.BucketClassifier.classify`'s unmatched
#: branch. The test asserts against the migration's own prefix constant below,
#: so drift between the two is a test failure, not a silent miss.
_DEFAULT_TEXT = ("No active rule matched these results, so the review defaults "
                 "to B3 for manual examination rather than being passed "
                 "silently.")


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "mig_20261030_default_routing_prov", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check(result, prefix):
    return next(c for c in result["checks"] if c["check"].startswith(prefix))


def test_matched_rule_passes_through_unchanged():
    matched = ClassificationResult(
        bucket="B1", rule_code="INT-001", rule_version=3, rule_name="Integrity",
        rationale="Integrity (INT-001 v3): conditions met",
        matched_conditions=["x"], evidence_summary={}, evaluated_rules=["INT-001"])
    assert classification_provenance(matched) == (
        "INT-001", 3, "Integrity (INT-001 v3): conditions met")


def test_unmatched_default_persists_the_reserved_code_not_null():
    unmatched = ClassificationResult(
        bucket="B3", rule_code=None, rule_version=None,
        rule_name="Unmatched — default", rationale=_DEFAULT_TEXT,
        matched_conditions=[], evidence_summary={}, evaluated_rules=[])
    code, version, rationale = classification_provenance(unmatched)
    assert code == UNMATCHED_RULE_CODE == "DEFAULT-UNMATCHED"
    assert version == UNMATCHED_RULE_VERSION == 0
    assert rationale == f"[{UNMATCHED_RULE_CODE}] {_DEFAULT_TEXT}"


def test_migration_prefix_matches_the_classifier_text():
    mig = _load_migration()
    assert mig.DEFAULT_RATIONALE_PREFIX == _DEFAULT_TEXT + "%"
    assert mig.DEFAULT_ROUTING_CODE == UNMATCHED_RULE_CODE
    assert mig.DEFAULT_ROUTING_VERSION == UNMATCHED_RULE_VERSION


async def _promoted_entity_id(db, intake_id):
    return (await db.execute(text(
        "SELECT canonical_entity_id FROM rce_curated_records "
        "WHERE source_intake_id = CAST(:i AS uuid) "
        "  AND canonical_entity_id IS NOT NULL LIMIT 1"),
        {"i": str(intake_id)})).scalar()


async def test_backfill_is_deterministic_and_the_check_pair_behaves(rolled_back_db):
    db = rolled_back_db
    mig = _load_migration()
    rows = make_rows(2, arc="9.99.777.46")
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    entity_id = await _promoted_entity_id(db, intake_id)
    assert entity_id is not None

    # A legacy default-routed row, as run_review wrote it before this change:
    # bucket set, rule NULL, the classifier's own default text persisted (with
    # the evidence-provenance suffix run_review appends after it).
    legacy = reg.ReviewRecord(
        review_id="REV-9999-000001", entity_id=entity_id,
        classification_bucket="B3", classification_rule=None,
        classification_rationale=_DEFAULT_TEXT + " OIG_LEIE clear -> clear "
                                                 "(generated 2026-10-09)")
    # A rule-less row with a DIFFERENT rationale: provenance genuinely unknown.
    unknown = reg.ReviewRecord(
        review_id="REV-9999-000002", entity_id=entity_id,
        classification_bucket="B2", classification_rule=None,
        classification_rationale="Keyed in manually during UAT; origin unclear")
    db.add(legacy)
    db.add(unknown)
    await db.flush()

    before = await reconciliation.reconcile_delivery(db, intake_id)
    cites = _check(before, "Every determination cites a rule")
    assert cites["passed"] is False and "2 classified" in cites["detail"]
    assert _check(before, "Every default-routed determination is adjudicated")[
        "passed"] is True  # nothing carries the reserved code yet

    # The migration's own statements, applied to exactly what they select.
    selected = (await db.execute(
        text(mig.SELECT_LEGACY_SQL), {"p": mig.DEFAULT_RATIONALE_PREFIX})).all()
    assert [r[0] for r in selected] == ["REV-9999-000001"]
    for review_id, ent, bucket in selected:
        await db.execute(text(mig.BACKFILL_UPDATE_SQL), {
            "rid": review_id, "code": mig.DEFAULT_ROUTING_CODE,
            "ver": mig.DEFAULT_ROUTING_VERSION,
            "p": mig.DEFAULT_RATIONALE_PREFIX})
        await db.execute(text(mig.AUDIT_INSERT_SQL), {
            "id": str(uuid.uuid4()), "entity_id": str(ent), "actor": "test",
            "action": mig.AUDIT_ACTION,
            "meta": '{"review_id": "%s", "migration": "%s"}' % (
                review_id, mig.revision)})

    await db.refresh(legacy)
    await db.refresh(unknown)
    assert legacy.classification_rule == "DEFAULT-UNMATCHED"
    assert legacy.classification_rule_version == 0
    assert legacy.classification_rationale.startswith(_DEFAULT_TEXT)  # untouched
    assert unknown.classification_rule is None  # uncertainty retained

    mid = await reconciliation.reconcile_delivery(db, intake_id)
    cites = _check(mid, "Every determination cites a rule")
    assert cites["passed"] is False and "1 classified" in cites["detail"]
    adjudicated = _check(mid, "Every default-routed determination is adjudicated")
    assert adjudicated["passed"] is False  # backfilled, but no human has acted
    assert mid["dimensions"]["verification"]["default_unadjudicated"] == 1

    # An audited analyst resolution — the ONLY thing that may clear it.
    legacy.reviewer_resolution = "confirmed"
    await db.flush()
    after = await reconciliation.reconcile_delivery(db, intake_id)
    assert _check(after, "Every default-routed determination is adjudicated")[
        "passed"] is True
    assert _check(after, "Every determination cites a rule")["passed"] is False
    # ^ the unknown-provenance row STILL fails the rule check: resolution is
    #   not an exemption from provenance.

    # Downgrade determinism: only the audited backfill row reverts.
    await db.execute(text(
        "UPDATE review_records SET classification_rule = NULL, "
        "classification_rule_version = NULL "
        "WHERE classification_rule = :code "
        "  AND review_id IN (SELECT metadata->>'review_id' "
        "                    FROM tefca_reg_audit_log "
        "                    WHERE action = :action "
        "                      AND metadata->>'migration' = :rev)"),
        {"code": mig.DEFAULT_ROUTING_CODE, "action": mig.AUDIT_ACTION,
         "rev": mig.revision})
    await db.refresh(legacy)
    assert legacy.classification_rule is None
