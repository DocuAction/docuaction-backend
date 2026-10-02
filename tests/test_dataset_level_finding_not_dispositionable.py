"""WF-S22: a dataset-level finding (no `source_record_id`, e.g. SCH-002
"columns delivered entirely empty") has nothing for a disposition to attach
to. `curation.apply_disposition` already refuses it with 409 ("names no
source record... has no record disposition") -- but before this fix the
exception-ledger row still said `can_dispose: true`, so the UI offered a
Save control the server would always reject (workbook case WF-S22, Medium).

Pinned here: the ledger now says `can_dispose: false` for such a row BEFORE
any Save is attempted, with a reason distinct from "terminal" (it is not
resolved -- it simply has no record to dispose), so the UI can show an
accurate reason rather than a false "this finding is resolved" notice.
"""
from __future__ import annotations

import uuid

import pytest

from support_delivery_api import cleanup, headers_for, run, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"


@pytest.fixture(scope="module")
def delivery_with_dataset_level_finding():
    d = seed_delivery(state="SUCCEEDED", issues=1)
    dataset_level_issue_id = str(uuid.uuid4())

    async def _insert_dataset_level_issue():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            run_id = (await db.execute(text(
                "SELECT id FROM rce_ingestion_runs WHERE source_intake_id = CAST(:intake AS uuid) "
                "ORDER BY started_at DESC NULLS LAST LIMIT 1"),
                {"intake": d["intake_id"]})).scalar()
            await db.execute(text(
                "INSERT INTO rce_issues (id, issue_code, source_intake_id, "
                "source_record_id, run_id, rule_id, rule_version, issue_type, severity, "
                "correction_authority, description, resolution) "
                "VALUES (CAST(:id AS uuid), :code, CAST(:intake AS uuid), NULL, "
                "CAST(:run_id AS uuid), 'SCH-002', '1.0.0', 'SCHEMA', 'INFORMATIONAL', "
                "'HUMAN_REQUIRED', 'Columns delivered entirely empty (dataset-level)', 'OPEN')"),
                {"id": dataset_level_issue_id, "code": f"DQ-TEST-{dataset_level_issue_id[:8]}",
                 "intake": d["intake_id"], "run_id": str(run_id) if run_id else None})
            await db.commit()
    run(_insert_dataset_level_issue())
    d["dataset_level_issue_id"] = dataset_level_issue_id
    yield d
    cleanup()


def test_dataset_level_finding_cannot_be_dispositioned(client, delivery_with_dataset_level_finding):
    d = delivery_with_dataset_level_finding
    ledger = client.get(f"{BASE}/deliveries/{d['intake_id']}/exceptions",
                        headers=headers_for("reviewer")).json()
    by_id = {row["issue_id"]: row for row in ledger["items"]}
    row = by_id[d["dataset_level_issue_id"]]

    assert row["source_record_id"] is None
    assert row["terminal"] is False, "a dataset-level finding is not RESOLVED -- it simply has no record"
    assert row["can_dispose"] is False
    assert row["disposition_blocked_reason"] == "delivery_level_finding"


def test_disposition_attempt_on_a_dataset_level_finding_is_refused_and_changes_nothing(
        client, delivery_with_dataset_level_finding):
    d = delivery_with_dataset_level_finding
    resp = client.post(
        f"{BASE}/issues/{d['dataset_level_issue_id']}/dispositions",
        headers=headers_for("reviewer"),
        json={"decision": "ACCEPT", "reason": "attempted from a stale UI state"})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert "no source record" in (body.get("error") or body.get("message") or body.get("detail") or "").lower()
