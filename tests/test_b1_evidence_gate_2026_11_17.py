"""B1 evidence gate for the QA-approved reporting bucket (synthetic, rolled back).

Owner authorization 2026-11-17 (synthetic validation only): an approved B4 -> B1
move is reported as B1 only when saved evidence at the cutoff establishes B1
under the recorded rule version and the source-applicability policy, with no
unresolved B4 condition. Otherwise B4 is kept and the reason is stated; the
analyst decision and the QA approval stay visible.

WHAT THESE TESTS CAN AND CANNOT SHOW
  * unsupported B1 stays B4 (blank NPI, no stored input; unknown rule version);
  * a pending SAM candidate stays B4 even when the SAM extract is clear;
  * a record whose LATER evidence rows all read PASS also stays B4: the report
    has no as-of evaluator for evidence saved after the review, and the stored
    review-time input reproduces B4. "Supported B1 reported as B1" is therefore
    NOT demonstrated from the database - only the dataset contract is (a fact
    that states `approved_b1_established is True`), which the loader never sets.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.reports.data import october_review_dataset as ds_mod
from app.reports.data import october_review_loader as ld
from app.reports.data import reviewed_disposition as rd
from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import SEED_RULES_V2, SEED_RULES_V3
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401
from test_october_review_loader_db_2026_11_12 import (  # noqa: F401
    CUTOFF, T_OLD, _curate, _entity, _evidence, _records, _review, _run,
)
from test_qa_approved_reporting_bucket_validation_2026_11_16 import (
    BEFORE, LATER, AUTO, _c, _event, _rows, review_id,
)

pytestmark = pytest.mark.regression
NOT_B1 = "B4 -> B1 recorded but NOT applied: saved evidence at the cutoff does not establish B1"
AFTER_REVIEW = datetime(2026, 11, 8, 10, 0, 0)          # later than the review (T_OLD), before the cutoff
EXCL = "EXCLUSION_REVOCATION"


def _src(status, disposition, dimension):
    return {"status": status, "disposition": disposition, "dimension": dimension}


#: nppes / pecos / LEIE clean; live SAM returned a candidate (REVIEW -> not_found): RULE-005 v3 -> B4
PENDING_SAM_INPUT = {"sources": {
    "nppes": _src("verified", "PASS", "IDENTITY"), "pecos": _src("verified", "PASS", "MEDICARE_ENROLLMENT"),
    "oig_leie": _src("clear", "NOT_FOUND", EXCL), "sam_gov": _src("not_found", "REVIEW", EXCL),
    "sam_extract": _src("clear", "PASS", EXCL)}, "fields": {}}


async def _v3_rules_exist_in_this_transaction(db):
    """Records 1 and 3 are re-classified from the rule rows stored at version 3. The test used to rely on some
    earlier suite having committed them (it passed in the CI isolation job and failed alone on a freshly migrated
    database with "no rules are stored at rule version 3"). Add them here when absent - flushed, never committed,
    so the fixture's rollback removes them. `ensure_rules_v3` is not used because it commits."""
    from datetime import date
    from sqlalchemy import func
    if (await db.execute(select(func.count()).select_from(reg.ReviewRule)
                         .where(reg.ReviewRule.version == 3))).scalar():
        return
    for spec in SEED_RULES_V3:
        db.add(reg.ReviewRule(rule_code=spec["rule_code"], name=spec["name"], bucket=spec["bucket"],
                              priority=spec["priority"], conditions=spec["conditions"],
                              description=spec["description"], version=3,
                              effective_date=date(2026, 1, 1), is_active=True))
    await db.flush()


async def _seed(db, arc):
    """Four synthetic records, every one automated B4 with an approved B4 -> B1 by a different user.

    1  stored input: pending SAM candidate. LATER rows: all four sources PASS      (later evidence, not re-evaluated)
    2  blank submitted NPI, no stored input, no evidence                            (unsupported)
    3  stored input: pending SAM candidate. Cutoff rows: SAM_GOV REVIEW, extract PASS (pending-SAM protection)
    4  stored input present, rule version 99 (no rules stored at that version)
    """
    await _v3_rules_exist_in_this_transaction(db)
    rows = make_rows(4, arc=arc, purposesofuse="T-TRTMNT,T-PH")
    rows[0]["NPI"] = rows[2]["NPI"] = rows[3]["NPI"] = "1234567893"
    rows[1]["NPI"] = ""
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    for i in range(1, 5):
        ent = await _entity(db, f"g{i}")
        await _curate(db, intake_id, recs[i], "CLEAN", entity_id=ent)
        await _review(db, recs[i], ent, review_id(arc, i), "B4", T_OLD, rule="RULE-006" if i == 2 else "RULE-005")
        rev = (await db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == review_id(arc, i)))).scalars().one()
        if i != 2:
            rev.verification_results = {"classifier_input": PENDING_SAM_INPUT}
            rev.classification_rule_version = 99 if i == 4 else 3
        if i == 1:
            for source, dim in (("NPPES", "IDENTITY"), ("CMS_PPEF_ENROLLMENT", "MEDICARE_ENROLLMENT"),
                                ("OIG_LEIE", EXCL), ("SAM_GOV", EXCL)):
                await _evidence(db, ent, source, dim, "PASS", AFTER_REVIEW)
        if i == 3:
            await _evidence(db, ent, "SAM_GOV", EXCL, "REVIEW", T_OLD)
            await _evidence(db, ent, "SAM_EXTRACT_SUPPLEMENTARY", EXCL, "PASS", AFTER_REVIEW,
                            original_values={"publication_date": "2026-11-07", "extract_filename": "synthetic.csv"})
        await db.flush()
        await _event(db, review_id(arc, i), 1, "ANALYST_DETERMINATION", BEFORE, determination="RECLASSIFY",
                     determined_bucket="B1")
        await _event(db, review_id(arc, i), 2, "QA_REVIEW", LATER, qa_action="APPROVE")
    return intake_id


async def test_an_approved_b1_stays_b4_unless_saved_evidence_establishes_b1(rolled_back_db):
    db = rolled_back_db
    intake_id = await _seed(db, "9.99.777.96")
    ds = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="SYN-B1-GATE")
    rows = _rows(ds)
    for i in (1, 2, 3, 4):
        row = rows[i]
        assert (_c(row, "Bucket"), _c(row, AUTO), _c(row, "Status")) == ("B4", "B4", "CLEAN"), i
        assert NOT_B1 in _c(row, "Bucket Basis") and "RECLASSIFIED" not in _c(row, "Flag"), i
        # the analyst decision and the QA approval are preserved, in their own columns
        assert _c(row, "Reviewed Disposition (QA-approved only)") == "Reclassified B4 -> B1", i
        assert _c(row, "Analyst/QA Status") == rd.APPROVED, i
        assert "NOT re-evaluated by this report" in _c(row, "Bucket Basis"), i
        assert "no source-applicability policy row is active" in _c(row, "Bucket Basis"), i

    # 1: every source cell reads PASS from LATER evidence, yet the stored review-time input reproduces B4
    one = _c(rows[1], "Bucket Basis")
    for col in ("NPPES", "PECOS/CMS Enrollment", "OIG LEIE", "SAM.gov Exclusions"):
        assert _c(rows[1], col).startswith("PASS"), col
    assert "classifies B4 under RULE-005 v3 (sam_gov is not_found)" in one
    assert "sam_gov (stored input)" in one

    # 2: unsupported - blank submitted NPI, nothing stored to evaluate
    assert _c(rows[2], "Submitted NPI (original, verbatim)") == ""
    assert "stores no classifier input" in _c(rows[2], "Bucket Basis")

    # 3: pending-SAM protection - the extract is clear, the live candidate is not, and B4 stands
    three = _c(rows[3], "Bucket Basis")
    assert "pending exclusion candidate with no recorded determination" in three
    assert "SAM_GOV (evidence row at the cutoff)" in three and "sam_gov (stored input)" in three
    assert "a clear SAM extract never clears a live SAM candidate" in three
    assert "SAM extract (evidence row at the cutoff)" not in three, "the extract itself is clear, not pending"

    # 4: the rule version the review names has no stored rules
    assert "no rules are stored at rule version 99" in _c(rows[4], "Bucket Basis")

    s = ds["summary"]
    assert s["buckets"] == s["automated_buckets"] == {"B1": 0, "B2": 0, "B3": 0, "B4": 4}
    assert s["qa_approved_reclassifications"] == 0 and s["reclassification_transitions"] == {}
    stored = (await db.execute(select(reg.ReviewRecord.classification_bucket, reg.ReviewRecord.reclassified_to).where(
        reg.ReviewRecord.review_id.like("REV-796-%")))).all()
    assert sorted(stored) == [("B4", None)] * 4, "nothing was written back"


# ── pure (no database) ───────────────────────────────────────────────────────

class _Rev:
    def __init__(self, **k):
        self.__dict__.update(dict(review_id="REV-SYN-1", classification_bucket="B4", classification_rule="RULE-005",
                                  classification_rule_version=3, verification_results={"classifier_input": PENDING_SAM_INPUT}),
                             **k)


def _v(rules):
    return [dict(r, version=3) for r in rules]


def test_the_verdict_never_establishes_b1_and_always_says_why():
    v3 = _v(SEED_RULES_V3)
    out = rd.b1_evidence_verdict(_Rev(), rules=v3, policy_active=True)
    assert out["established"] is False
    assert "classifies B4 under RULE-005 v3" in out["reason"] and "sam_gov (stored input)" in out["reason"]
    assert "EXCLUSION:sam_gov:potential_hit_or_unknown_origin" in out["reason"]
    assert "policy row is active" not in out["reason"], "an active policy is not reported as missing"
    assert out["reason"].endswith(rd.B1_NO_LATER_EVIDENCE)

    # a stored input that would classify B1 under the recorded version does NOT reproduce the recorded B4:
    # that is a discrepancy for a person, never a reason to report B1
    clean = {"classifier_input": {"sources": dict(PENDING_SAM_INPUT["sources"],
                                                  sam_gov=_src("clear", "NOT_FOUND", EXCL)), "fields": {}}}
    out = rd.b1_evidence_verdict(_Rev(verification_results=clean), rules=v3, policy_active=True)
    assert out["established"] is False and "does not reproduce its recorded classification" in out["reason"]
    assert "re-evaluated B1 by RULE-001" in out["reason"]

    # rule version 2 does not block a pending SAM candidate in RULE-001; the candidate check does not depend on it
    out = rd.b1_evidence_verdict(_Rev(classification_rule_version=2), rules=[dict(r, version=2) for r in SEED_RULES_V2],
                                 policy_active=True)
    assert out["established"] is False and "pending exclusion candidate" in out["reason"]

    class Row:
        disposition = "REVIEW"
    out = rd.b1_evidence_verdict(_Rev(verification_results=None), rules=v3, policy_active=False,
                                 cutoff_rows={"SAM_GOV": Row(), "SAM extract": None})
    assert "stores no classifier input" in out["reason"] and "SAM_GOV (evidence row at the cutoff)" in out["reason"]
    assert "no source-applicability policy row is active" in out["reason"]
    for key in ("prior_risk_not_cleared", "analyst_review_dimensions"):
        vr = {"classifier_input": PENDING_SAM_INPUT, key: {"x": 1}}
        assert rd.b1_evidence_verdict(_Rev(verification_results=vr), rules=v3, policy_active=True)["established"] is False


def _fact(i, **kw):
    return dict(dict(source_line=i, agt_record_id=f"agt-{i}", bucket="B4", status="CLEAN", approved_bucket="B1",
                     approved_note="QA-approved reclassification B4 -> B1 (x)"), **kw)


def test_the_dataset_applies_b1_only_when_the_fact_states_established_evidence():
    facts = [_fact(1),                                                        # says nothing: fail closed
             _fact(2, approved_b1_established=False, approved_b1_reason="review R stores no classifier input"),
             _fact(3, approved_b1_established="yes"),                         # only the literal True counts
             _fact(4, approved_b1_established=True),                          # the contract; never set by the loader
             _fact(5, approved_b1_established=True, status="HELD", held_reason="blocking identity"),
             _fact(6, approved_bucket="B3", approved_note="")]                # B4 -> B3 is unaffected by the gate
    ds = ds_mod.build_dataset(facts, report_id="R")
    rows = {r[ds_mod.RECORD_COLUMNS.index("AGT_Record_ID")]: r for r in ds["sheets"]["Records"]["rows"]}
    assert [_c(rows[f"agt-{i}"], "Bucket") for i in range(1, 7)] == ["B4", "B4", "B4", "B1", "B4", "B3"]
    assert all(_c(rows[f"agt-{i}"], AUTO) == "B4" for i in range(1, 7))
    assert NOT_B1 + " - no evaluation of saved evidence was supplied" in _c(rows["agt-1"], "Bucket Basis")
    assert NOT_B1 + " - review R stores no classifier input" in _c(rows["agt-2"], "Bucket Basis")
    assert "preserved in their own columns" in _c(rows["agt-2"], "Bucket Basis")
    assert "NOT applied: the record remains HELD" in _c(rows["agt-5"], "Bucket Basis")
    s = ds["summary"]
    assert s["buckets"] == {"B1": 1, "B2": 0, "B3": 1, "B4": 4} and s["held_inside_b4"] == 1
    assert s["reclassification_transitions"] == {"B4 -> B1": 1, "B4 -> B3": 1}


# 2026-11-24: a source whose row at the cutoff the loader did not attribute to this record is named as not
# evaluated. It is never read as "no pending candidate", and another identifier's candidate is never cited.

def test_a_source_not_attributed_to_the_record_is_stated_as_not_evaluated():
    from types import SimpleNamespace

    from app.reports.data import reviewed_disposition as rd

    review = SimpleNamespace(review_id="REV-SYN-1", verification_results={}, classification_rule_version=None,
                             classification_bucket="B4", classification_rule="RULE-006")
    verdict = rd.b1_evidence_verdict(review, rules=None, policy_active=True,
                                     cutoff_rows={"SAM_GOV": None, "OIG_LEIE": None, "SAM extract": None},
                                     not_attributed=["SAM_GOV", "SAM_GOV"])
    assert verdict["established"] is False
    assert "not attributed to this record, so not evaluated: SAM_GOV" in verdict["reason"]
    assert "pending exclusion candidate" not in verdict["reason"]
    plain = rd.b1_evidence_verdict(review, rules=None, policy_active=True, cutoff_rows=None)
    assert plain["established"] is False and "not attributed" not in plain["reason"]
