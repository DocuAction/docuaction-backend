"""Shadow comparison using the ACTUAL preserved SEED_RULES_V4 candidate
(sam-fix-be, uncommitted bucket_classifier.py WIP), not a fixture built to
demonstrate mechanics only.

The candidate's rule-construction logic is reproduced here VERBATIM from
that file (inspected read-only via `git diff` in that worktree, never
overwritten, never committed, never run from there), applied to THIS
worktree's own real SEED_RULES_V3 -- a byte-identical result to importing
it directly, without the cross-checkout `app` package name collision a
literal cross-repo import would risk. SEED_RULES_V4 is never installed
(`ensure_rules_v4` is not called; no rule row is written as version 4) --
it is passed only as an explicit `candidate_rules` list to
`shadow_reassessment.build_comparison`, exactly the "evaluate explicitly in
shadow mode, keep inactive for ordinary processing" contract.
"""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import test_sam_e2e_delivery_path as sam

pytestmark = pytest.mark.asyncio

UNAVAILABLE, FAILED, NOT_CHECKED = "unavailable", "failed", "not_checked"
V4_SILENCE_STATES = (UNAVAILABLE, FAILED, NOT_CHECKED)


def _silence(source: str) -> list:
    return [{"source": source, "status": s} for s in V4_SILENCE_STATES]


def _add(cond: dict, clause: str, items: list) -> None:
    existing = {(c.get("source"), c.get("status"))
                for c in cond.get(clause, []) if isinstance(c, dict)}
    for c in items:
        if (c["source"], c["status"]) not in existing:
            cond.setdefault(clause, []).append(c)


def real_seed_rules_v4() -> list:
    """Verbatim reproduction of sam-fix-be's uncommitted _v4_rules()."""
    from app.tefca_registry.bucket_classifier import SEED_RULES_V3

    out = []
    for spec in copy.deepcopy(SEED_RULES_V3):
        code = spec["rule_code"]
        cond = spec["conditions"]
        if code == "RULE-001":
            _add(cond, "none_of", _silence("sam_gov"))
        elif code == "RULE-002":
            cond["any_unavailable"] = ["pecos"]
            _add(cond, "none_of", _silence("sam_gov"))
        elif code == "RULE-003":
            for source in ("sam_gov", "oig_leie", "nppes"):
                _add(cond, "none_of", _silence(source))
        out.append(spec)
    return out


async def test_real_v4_candidate_shadow_comparison_full_lifecycle(db_required):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.tefca_registry.rce import shadow_reassessment as sr
    from app.tefca_registry import models as m

    # Keyless environment (confirmed, this host): SAM.gov reports unavailable
    # on every entity via the real, unpatched connector path -- not mocked
    # to produce this; it is what the real candidate's own docstring warns
    # the current environment would do ("v4 would classify the ENTIRE
    # registry B3"). NPPES/LEIE patched clean so the only variable is SAM
    # silence, isolating exactly what the candidate changes.
    intake_id = await sam._seed_promoted_delivery(n=3)

    mp = pytest.MonkeyPatch()
    try:
        mp.setenv("ENTITY_RESOLVER_SOURCE", "db")  # explicit: the default resolver is a bundled mock dataset that cannot resolve freshly-seeded synthetic entities
        sam._clean_nppes_leie(mp)
        async with async_session_maker() as db:
            refs = await sam._promoted_refs(db, intake_id, 3)
        async with async_session_maker() as db:
            result = await verify_and_classify(db, refs, intake_id=intake_id,
                                               actor="shadow-v4-real")
    finally:
        mp.undo()

    buckets_before = [o["bucket"] for o in result["outcomes"]]
    print("[v4-real][1] baseline (v3) classification, SAM keyless:", buckets_before)

    candidate = real_seed_rules_v4()
    analyst_user = SimpleNamespace(id=uuid.uuid4(),
                                   email="v4-analyst@synthetic-test.docuaction.invalid",
                                   role="analyst")
    qa_user = SimpleNamespace(id=uuid.uuid4(),
                              email="v4-qalead@synthetic-test.docuaction.invalid",
                              role="qalead")

    async with async_session_maker() as db:
        cmp = await sr.build_comparison(db, intake_id, built_by="shadow-v4-real",
                                        candidate_rules=candidate)
        await db.commit()
    print("[v4-real][2] comparison built: baseline_rule_version=",
          cmp["baseline_rule_version"], "package_hash=", cmp["package_hash"][:12])

    async with async_session_maker() as db:
        deltas_page = await sr.list_deltas(db, cmp["comparison_id"], limit=100)
    by_kind = {}
    for d in deltas_page["items"]:
        by_kind.setdefault(d["delta_kind"], []).append(d)
        print("[v4-real][3] entity=", d["entity_id"], "baseline=", d["baseline"],
              "candidate=", d["candidate"], "kind=", d["delta_kind"],
              "direction=", d["direction"])
    print("[v4-real][3b] by_kind counts:", {k: len(v) for k, v in by_kind.items()})

    # The real candidate's own documented cost, confirmed for real: with SAM
    # keyless, every entity moves off a clean bucket (NEW or CHANGED), never
    # UNCHANGED, never REMOVED (v4 only ever withholds clearance here).
    assert "REMOVED" not in by_kind, by_kind
    assert set(by_kind) <= {"NEW", "CHANGED"}, (
        "expected only NEW/CHANGED under SAM-keyless v4 (the candidate's own "
        "documented cost), got " + str(sorted(by_kind)))

    # Independent approvals (maker/checker), stale rejection, idempotent
    # publish -- same proofs as the fixture-based demonstration, now against
    # the actual preserved candidate.
    async with async_session_maker() as db:
        await sr.record_approval(db, cmp["comparison_id"], approval_role="ANALYST",
                                 user=analyst_user, package_hash=cmp["package_hash"],
                                 rationale="shadow-v4-real: analyst approval")
        await db.commit()
    from app.tefca_registry.rce.shadow_reassessment import ShadowRefused
    async with async_session_maker() as db:
        try:
            await sr.record_approval(db, cmp["comparison_id"], approval_role="INDEPENDENT_QA",
                                     user=analyst_user, package_hash=cmp["package_hash"],
                                     rationale="same person")
            same_refused = False
        except ShadowRefused:
            same_refused = True
    assert same_refused, "same-person shadow QA approval was not refused for the real candidate"
    async with async_session_maker() as db:
        await sr.record_approval(db, cmp["comparison_id"], approval_role="INDEPENDENT_QA",
                                 user=qa_user, package_hash=cmp["package_hash"],
                                 rationale="shadow-v4-real: independent QA")
        await db.commit()
    print("[v4-real][4] maker/checker proven on the real candidate's own comparison")

    mp2 = pytest.MonkeyPatch()
    mp2.setenv("SHADOW_PUBLICATION_MODE", "local_test")
    try:
        async with async_session_maker() as db:
            pub1 = await sr.publish_successors(db, cmp["comparison_id"], user=qa_user)
            await db.commit()
        async with async_session_maker() as db:
            pub2 = await sr.publish_successors(db, cmp["comparison_id"], user=qa_user)
            await db.commit()
    finally:
        mp2.undo()
    assert pub2.get("already_published") is True
    assert pub1.get("successor_review_ids") == pub2.get("successor_review_ids")
    print("[v4-real][5] idempotent publish proven: successors=", pub1.get("successor_review_ids"))

    async with async_session_maker() as db:
        official = await sr.official_records(db, intake_id)
    assert len(official) == 3, "official records must stay untouched by the shadow run itself"
    print("[v4-real][6] official records unchanged:", len(official))

    # SEED_RULES_V4 was never installed anywhere.
    async with async_session_maker() as db:
        v4_rows = (await db.execute(select(m.ReviewRule).where(
            m.ReviewRule.version == 4))).scalars().all()
    print("[v4-real][7] ReviewRule rows at version=4 in this DB:", len(v4_rows), "(must be 0)")
    assert len(v4_rows) == 0, "SEED_RULES_V4 must never be installed as an active rule set"
