"""Record totals and finding totals are different units and must stay separate
(2026-10-13): one record can carry several findings in different states.

Seeded delivery, three records (the support fixture lays down three lines):

  record 1  HELD       2 HIGH findings (one RESOLVED, one OPEN) + 1 INFORMATIONAL
  record 2  HELD       1 HIGH finding OPEN
  record 3  CREATED    1 HIGH finding RESOLVED, an applied NPI correction

So: 4 HIGH findings over 3 records; 2 of those findings are undecided and they
sit on 2 records; 2 records are held. No count equals any other by accident.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

import pytest

from support_delivery_api import headers_for, run, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"


@pytest.fixture(scope="module")
def delivery():
    d = seed_delivery(state="SUCCEEDED", issues=0)

    async def _decorate():
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.tefca_registry.rce import dispositions
        from app.tefca_registry.rce import models as m

        intake = uuid.UUID(d["intake_id"])
        async with async_session_maker() as db:
            run_id = (await db.execute(
                select(m.RceIngestionRun.id)
                .where(m.RceIngestionRun.source_intake_id == intake))).scalar()
            recs = [uuid.UUID(r) for r in d["record_ids"]]
            plan = [  # (record index, rule, severity, resolution)
                (0, "NPI-003", "HIGH", "RESOLVED"), (0, "ACT-001", "HIGH", "OPEN"),
                (0, "NPI-001", "INFORMATIONAL", "OPEN"),
                (1, "NPI-001", "HIGH", "OPEN"),
                (2, "NPI-002", "HIGH", "RESOLVED"),
            ]
            curated = {}
            for i, rec in enumerate(recs):
                row = m.RceCuratedRecord(
                    source_intake_id=intake, source_record_id=rec,
                    record_status="HELD" if i < 2 else "CORRECTED", issue_count=0,
                    rce_org_oid=f"oid-{i}", npi="1881659506", name=f"Multi Org {i}",
                    transformation_version="count-test")
                db.add(row)
                curated[i] = row
            await db.flush()
            for n, (i, rule, sev, res) in enumerate(plan):
                db.add(m.RceIssue(
                    issue_code=f"DQ-CNT-{uuid.uuid4().hex[:8]}", source_intake_id=intake,
                    source_record_id=recs[i], run_id=run_id, rule_id=rule, rule_version="1.0.0",
                    issue_type="NPI_LENGTH_INVALID" if rule.startswith("NPI") else "INACTIVE_NEW_ENTRANT",
                    severity=sev, resolution=res, field_name="NPI" if rule.startswith("NPI") else "active",
                    original_value="1881659506", correction_authority="HUMAN_REQUIRED",
                    description=f"count test finding {n} for rule {rule}"))
            await db.flush()
            db.add(m.RceCorrectionDetail(
                curated_record_id=curated[2].id, source_record_id=recs[2], column_name="npi",
                original_value="188165950", original_value_hash="0" * 64,
                corrected_value="1881659506", correction_reason="count test",
                correction_authority="HUMAN_REQUIRED", corrected_by="analyst@test.local"))
            for i, disp, code in ((0, "HELD", dispositions.REASON_HELD_QUALITY),
                                  (1, "HELD", dispositions.REASON_HELD_QUALITY),
                                  (2, "CREATED", dispositions.REASON_ANALYST)):
                await dispositions.record(
                    db, intake_id=intake, source_record_id=recs[i], disposition=disp,
                    reason_code=code, job_id=uuid.UUID(d["job_id"]), reason="seeded",
                    commit=False)
            await db.commit()
    run(_decorate())
    return d


def test_exceptions_block_keeps_findings_and_records_separate(client, delivery):
    r = client.get(f"{BASE}/delivery-jobs/{delivery['job_id']}/detail",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    ex = r.json()["exceptions"]
    # existing finding keys unchanged
    assert ex["total"] == 5 and ex["open"] == 3 and ex["resolved"] == 2
    # new, separately named
    assert ex["held_records"] == 2
    assert ex["high_critical_findings"] == 4
    assert ex["high_critical_records"] == 3
    assert ex["open_high_critical_findings"] == 2
    assert ex["open_high_critical_records"] == 2
    assert ex["records_with_findings"] == 3
    assert "findings" in ex["definitions"]["open_high_critical_findings"]
    assert "source records" in ex["definitions"]["held_records"]


def test_held_row_carries_both_resolved_and_undecided_state(client, delivery):
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/dispositions?disposition=HELD",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and body["unit"] == "records"
    multi = next(i for i in body["items"] if i["line_number"] == 2)
    fc = multi["finding_counts"]
    assert fc["total"] == 3 and fc["high_critical"] == 2
    assert fc["undecided_high_critical"] == 1 and fc["resolved_high_critical"] == 1
    assert fc["by_resolution"] == {"RESOLVED": 1, "OPEN": 2}
    assert multi["rule_codes"] == ["ACT-001"]          # only the undecided one holds it
    assert len(multi["finding_ids"]) == 3
    assert multi["npi_change"] == "UNCHANGED"            # nothing applied, nothing proposed
    assert multi["submitted_npi"] == multi["curated_npi"]


def test_filters_count_records_not_findings(client, delivery):
    h = headers_for("reviewer")
    base = f"{BASE}/deliveries/{delivery['intake_id']}/dispositions"
    # HIGH + undecided: 2 findings, 2 records
    assert client.get(f"{base}?severity=HIGH,CRITICAL&resolution=UNDECIDED", headers=h).json()["total"] == 2
    # HIGH in any state: 4 findings but 3 records
    assert client.get(f"{base}?severity=HIGH", headers=h).json()["total"] == 3
    # held AND has an undecided HIGH finding
    assert client.get(f"{base}?disposition=HELD&resolution=UNDECIDED&severity=HIGH", headers=h).json()["total"] == 2
    # a resolved HIGH finding exists on a non-held record
    only_resolved = client.get(f"{base}?severity=HIGH&resolution=RESOLVED&disposition=CREATED", headers=h).json()
    assert only_resolved["total"] == 1
    assert only_resolved["items"][0]["npi_change"] == "CORRECTED_APPLIED"
    assert only_resolved["items"][0]["npi_correction"]["corrected_by"] == "analyst@test.local"
    assert client.get(f"{base}?severity=BOGUS", headers=h).status_code == 422


def test_per_record_csv_is_one_row_per_record(client, delivery):
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exception-records.csv",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    assert r.headers["X-Total-Rows"] == "2" and r.headers["X-Returned-Rows"] == "2"
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 2                                   # 2 held records, not 3-4 findings
    multi = next(x for x in rows if x["source_row"] == "2")
    assert multi["delivery_job_id"] == delivery["job_id"]
    assert multi["findings_total"] == "3" and multi["undecided_high_critical"] == "1"
    assert multi["resolved_high_critical"] == "1" and multi["max_severity"] == "HIGH"
    assert multi["rule_codes"] == "ACT-001" and len(multi["finding_ids"].split(";")) == 3
    wide = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exception-records.csv?scope=all-with-findings",
                      headers=headers_for("reviewer"))
    assert wide.headers["X-Total-Rows"] == "3"
    assert client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exception-records.csv?scope=x",
                      headers=headers_for("reviewer")).status_code == 422
