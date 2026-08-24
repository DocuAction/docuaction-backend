"""
Phase 9 baseline freeze and integrity verification.

Read-only. Run before and after certification work; the two outputs must be
identical except where a change was deliberate and is explained.

Every figure is derived. Nothing is hard-coded except the expected digests,
which are the point of the exercise: they are what an earlier phase recorded,
and a mismatch means something moved that should not have.

DEVELOPMENT / TEST DATA. Nothing printed here is an ONC finding.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import secrets
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap() -> None:
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        raw = io.open(env_path, "rb").read().decode("utf-8", "replace")
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    os.environ["SECRET_KEY"] = secrets.token_urlsafe(64)
    sys.path.insert(0, ROOT)


_bootstrap()

import logging  # noqa: E402

logging.disable(logging.WARNING)

from sqlalchemy import text  # noqa: E402

from app.core.database import async_session_maker  # noqa: E402

# Recorded by the phase that produced them. A mismatch is the alarm.
EXPECTED = {
    "area1_records": 23_566,
    "area1_digest": "24524f70c370d6c42a2b03d5385295a5",
    "area1_sha256": "689472073480b1cc4faf604527eda47e4e59928f7a6128d84b2f28bb6e9e9e8d",
    "obs_1_0_0": 164_962,
    "digest_1_0_0": "84384bcd7aef04b137e30eb88848e2ee",
    "hops_1_0_0": 39_749,
    "obs_1_1_0": 188_528,
    "digest_1_1_0": "bd012e2d3dc220b4c91d281933ad6482",
    "hops_1_1_0": 116_218,
    "hop_digest_all": "95a23fe34a1872da4a57455c2b2c4824",
    "review_records": 43,
    "reportable": 0,
    "decision_events": 0,
}

ARTEFACT = os.path.join(ROOT, "uploads", "rce_deliveries",
                        "689472073480b1cc_onc-snapshot-20260720.csv")

_results = []


def check(name: str, actual, expected=None) -> bool:
    ok = True if expected is None else (actual == expected)
    _results.append((name, actual, expected, ok))
    return ok


async def main() -> int:
    async with async_session_maker() as db:
        async def scalar(sql, **kw):
            return (await db.execute(text(sql), kw)).scalar()

        print("=" * 100)
        print("PHASE 9 BASELINE FREEZE — DEVELOPMENT / TEST DATA, NOT ONC FINDINGS")
        print("=" * 100)

        # ── environment ──────────────────────────────────────────────────────
        from app.Tefca.connectors import is_running_mock
        from app.Tefca.evidence_version import (current_rule_version,
                                                historical_rule_versions)

        check("database is development", await scalar("select current_database()"),
              "docuaction-db")
        check("is_running_mock()", is_running_mock(), True)
        check("canonical evidence version", current_rule_version(),
              "phase6-bulk-1.1.0")
        check("superseded versions", ",".join(historical_rule_versions()),
              "phase6-bulk-1.0.0")

        # ── Area 1 ───────────────────────────────────────────────────────────
        check("Area-1 records",
              await scalar("select count(*) from rce_source_records"),
              EXPECTED["area1_records"])
        check("Area-1 content digest", await scalar(
            "select md5(string_agg(raw_line, chr(10) order by line_number)) "
            "from rce_source_records"), EXPECTED["area1_digest"])

        digest = hashlib.sha256()
        with open(ARTEFACT, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        check("Area-1 artefact SHA-256 on disk", digest.hexdigest(),
              EXPECTED["area1_sha256"])
        check("Area-1 intake SHA-256 recorded",
              await scalar("select sha256 from rce_source_intakes"),
              EXPECTED["area1_sha256"])
        check("Area-1 artefact is read-only on disk",
              not os.access(ARTEFACT, os.W_OK), True)

        # ── evidence, both generations ───────────────────────────────────────
        for label, version in (("1.0.0", "phase6-bulk-1.0.0"),
                               ("1.1.0", "phase6-bulk-1.1.0")):
            key = label.replace(".", "_")
            check(f"observations {label}", await scalar(
                "select count(*) from tefca_dimension_evidence "
                "where rule_version=:v", v=version), EXPECTED[f"obs_{key}"])
            check(f"observation digest {label}", await scalar(
                "select md5(string_agg(observation_hash,'' order by observation_hash)) "
                "from tefca_dimension_evidence where rule_version=:v", v=version),
                EXPECTED[f"digest_{key}"])
            check(f"relationship hops {label}", await scalar(
                "select count(*) from evidence_relationship_path p "
                "join tefca_dimension_evidence x on x.id=p.evidence_id "
                "where x.rule_version=:v", v=version), EXPECTED[f"hops_{key}"])

        check("relationship digest (all)", await scalar(
            "select md5(string_agg(p.id::text,'' order by p.id::text)) "
            "from evidence_relationship_path p"), EXPECTED["hop_digest_all"])

        # ── human decisions ──────────────────────────────────────────────────
        check("review records",
              await scalar("select count(*) from review_records"),
              EXPECTED["review_records"])
        check("reportable review records", await scalar(
            "select count(*) from review_records where reportable_at is not null"),
            EXPECTED["reportable"])
        check("decision events",
              await scalar("select count(*) from review_decision_events"),
              EXPECTED["decision_events"])
        check("resolved determinations", await scalar(
            "select count(*) from review_records "
            "where reviewer_resolution is not null"), 0)

        # ── no automatic verdicts in current evidence ────────────────────────
        check("automatic PASS/FAIL at the current version", await scalar(
            "select count(*) from tefca_dimension_evidence "
            "where rule_version=:v and disposition in ('PASS','FAIL')",
            v=current_rule_version()), 0)

        # ── per-(dimension, source) reconciliation ───────────────────────────
        rows = (await db.execute(text(
            "select evidence_dimension, source, count(*) "
            "from tefca_dimension_evidence where rule_version=:v "
            "group by 1,2 order by 1,2"),
            {"v": current_rule_version()})).all()
        population = await scalar("select count(*) from rce_source_records")

        print()
        print(f"PER-(DIMENSION, SOURCE) RECONCILIATION — each must equal the "
              f"population, {population:,}")
        for dimension, source, count in rows:
            check(f"  {dimension} / {source}", count, population)

        total = sum(c for _, _, c in rows)
        check("observations sum to population x pairs", total,
              population * len(rows))

        # ── de-dup key uniqueness, through the reporting path ────────────────
        from app.reports.data.report_data_service import ReportDataService

        service = ReportDataService(db)
        reported = await service._dimension_rows(None)
        scope = dict(service.evidence_scope)
        check("reporting path reads every observation",
              scope.get("observations_read"), EXPECTED["obs_1_1_0"])
        check("reporting path drops none",
              scope.get("collapsed_duplicates"), 0)
        keys = Counter((r.entity_id, r.evidence_dimension, r.source)
                       for r in reported)
        check("de-dup key is unique",
              len([k for k, c in keys.items() if c > 1]), 0)
        check("only the current version reaches a report",
              sorted({r.rule_version for r in reported}),
              [current_rule_version()])

        # ── report provenance ────────────────────────────────────────────────
        from app.reports.data.source_provenance import (
            authoritative_source_provenance, is_real_sha256)

        provenance = await authoritative_source_provenance(db)
        check("report source hash is real", is_real_sha256(provenance.sha256), True)
        check("report source hash is the Area-1 artefact", provenance.sha256,
              EXPECTED["area1_sha256"])
        check("data classification", provenance.data_classification,
              "DEVELOPMENT_TEST")

        # ── output ───────────────────────────────────────────────────────────
        print()
        print("=" * 100)
        print(f"{'CHECK':<62} {'ACTUAL':>18}  {'RESULT'}")
        print("-" * 100)
        failed = 0
        for name, actual, expected, ok in _results:
            shown = f"{actual:,}" if isinstance(actual, int) else str(actual)
            if len(shown) > 18:
                shown = shown[:15] + "..."
            verdict = "OK" if ok else f"DRIFT (expected {expected})"
            if not ok:
                failed += 1
            print(f"{name:<62} {shown:>18}  {verdict}")

        print("-" * 100)
        print(f"{len(_results)} checks, {len(_results) - failed} passed, "
              f"{failed} drifted")
        print("=" * 100)
        if failed:
            print("BASELINE NOT FROZEN — unexplained drift. Do not proceed.")
        else:
            print("BASELINE FROZEN — no drift.")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
