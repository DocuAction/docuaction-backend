"""Route-level proof of the CSV export manifests (2026-10-03): for each
per-delivery CSV export, the registered row count (a COUNT over the identical
relation and predicate, in a consistent REPEATABLE READ snapshot) equals the
CSV-aware parsed row count of what was actually sent, the logged SHA-256 and
byte count are those of the body, formula leaders are neutralised in the
export without touching the persisted value, and an embedded newline is one
record. Row grains: CSV_ROW_GRAINS.md.

Database-backed (isolated PostgreSQL); seeds real delivery rows through the
same helpers the exception-ledger tests use.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import uuid

import pytest

from support_delivery_api import headers_for, run, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"
LOGGER = "app.tefca_registry.rce.delivery_routes"
FORMULA_DESCRIPTION = "=HYPERLINK(\"http://evil.invalid\")\nsecond line of the finding"


@pytest.fixture(scope="module")
def delivery():
    d = seed_delivery(state="SUCCEEDED", issues=2)

    async def _poison_one_finding():
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.tefca_registry.rce import models as m

        async with async_session_maker() as db:
            issue = (await db.execute(
                select(m.RceIssue)
                .where(m.RceIssue.source_record_id.in_([uuid.UUID(r) for r in d["record_ids"]]))
                .order_by(m.RceIssue.issue_code).limit(1))).scalar_one()
            issue.description = FORMULA_DESCRIPTION
            d["poisoned_issue_code"] = issue.issue_code
            await db.commit()

    run(_poison_one_finding())
    return d


def _manifest(caplog, route: str):
    records = [r for r in caplog.records if r.getMessage() == "csv_export_manifest"
               and getattr(r, "route", None) == route]
    assert records, f"no csv_export_manifest log record for {route}"
    return records[-1]


def _rows(text: str):
    return list(csv.reader(io.StringIO(text)))


def _assert_manifest_matches_body(record, response, parsed_rows):
    assert record.export_id == response.headers["X-Export-Id"]
    assert record.sha256 == hashlib.sha256(response.content).hexdigest()
    assert record.byte_count == len(response.content)
    assert record.registered_row_count == len(parsed_rows) - 1  # minus header
    assert record.streamed_row_count == len(parsed_rows) - 1
    assert record.row_count_reconciles is True
    assert response.headers["X-Returned-Rows"] == str(record.registered_row_count)


@pytest.mark.parametrize("route", ["findings.csv", "identifier-conflicts.csv", "review-records.csv"])
def test_registered_count_sha256_and_bytes_match_what_was_sent(client, delivery, caplog, route):
    caplog.set_level(logging.INFO, logger=LOGGER)
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/{route}", headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    rows = _rows(r.text)
    _assert_manifest_matches_body(_manifest(caplog, route), r, rows)


def test_findings_csv_counts_two_seeded_findings_not_newline_bytes(client, delivery, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/findings.csv", headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    rows = _rows(r.text)
    assert len(rows) - 1 == 2, "two seeded findings = two records"
    # The poisoned description carries an embedded newline: the file has MORE
    # newline bytes than records, and the registered count must not be fooled.
    assert r.text.count("\n") > len(rows)
    assert _manifest(caplog, "findings.csv").registered_row_count == 2


def test_findings_csv_neutralises_the_formula_leader_and_keeps_the_newline(client, delivery):
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/findings.csv", headers=headers_for("reviewer"))
    rows = _rows(r.text)
    header = rows[0]
    by_code = {row[header.index("issue_code")]: row for row in rows[1:]}
    exported = by_code[delivery["poisoned_issue_code"]][header.index("description")]
    assert exported == "'" + FORMULA_DESCRIPTION, "leading '=' neutralised, embedded newline preserved"


def test_the_persisted_finding_is_not_mutated_by_the_export(delivery):
    async def _read():
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.tefca_registry.rce import models as m

        async with async_session_maker() as db:
            return (await db.execute(
                select(m.RceIssue.description).where(m.RceIssue.issue_code == delivery["poisoned_issue_code"])
            )).scalar_one()

    assert run(_read()) == FORMULA_DESCRIPTION


def test_dispositions_csv_total_equals_parsed_rows_and_carries_a_manifest(client, delivery, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/dispositions.csv", headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    rows = _rows(r.text)
    assert r.headers["X-Total-Rows"] == str(len(rows) - 1)
    assert r.headers["X-Returned-Rows"] == str(len(rows) - 1)
    record = _manifest(caplog, "dispositions.csv")
    assert record.export_id == r.headers["X-Export-Id"]
    assert record.sha256 == hashlib.sha256(r.content).hexdigest()
    assert record.byte_count == len(r.content)
    assert record.row_count_reconciles is True


def test_a_truncating_limit_is_recorded_as_not_reconciling(client, delivery, caplog):
    """`limit` is a caller's choice to take fewer rows than exist; the manifest
    says so explicitly rather than reporting a reconciled export."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/dispositions.csv?limit=1",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    total = int(r.headers["X-Total-Rows"])
    returned = int(r.headers["X-Returned-Rows"])
    record = _manifest(caplog, "dispositions.csv")
    assert record.registered_row_count == total and record.streamed_row_count == returned
    assert record.row_count_reconciles is (total == returned)


def test_exports_are_reviewer_only(client, delivery):
    for route in ("findings.csv", "identifier-conflicts.csv", "review-records.csv", "dispositions.csv"):
        r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/{route}", headers=headers_for("viewer"))
        assert r.status_code == 403, route
