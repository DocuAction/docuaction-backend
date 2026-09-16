"""The exception ledger (`GET /deliveries/{intake_id}/exceptions`) and the
record-level dispositions (`/dispositions`, `/dispositions.csv`), on the
isolated database - contract 2026-09-17, section 6.

Pinned: the row shape, the stage derivation, every filter, the totals over the
filtered set, the disposition-history composition, the CSV, and that these are
reviewer-only.
"""

from __future__ import annotations

import csv
import io
import uuid

import pytest

from support_delivery_api import headers_for, register_review_record, run, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"

ROW_KEYS = {
    "issue_id", "issue_code", "delivery", "source_row", "source_record_id", "entity_name",
    "submitted_value", "existing_value", "normalized_value", "rule_code", "issue_type",
    "severity", "stage", "description", "reason_code", "created_at", "status", "assignee",
    "disposition", "disposition_history", "actor", "decided_at", "evidence_source",
    "correlation_id", "build_sha",
    # frontend alignment (2026-09-17)
    "entity_id", "identifier_type",
}


@pytest.fixture(scope="module")
def delivery():
    d = seed_delivery(state="SUCCEEDED", issues=2)

    async def _decorate():
        from app.core.database import async_session_maker
        from app.tefca_registry.rce import dispositions

        async with async_session_maker() as db:
            rec = uuid.UUID(d["record_ids"][0])
            await dispositions.record(
                db, intake_id=uuid.UUID(d["intake_id"]), source_record_id=rec,
                disposition="HELD", reason_code=dispositions.REASON_HELD_QUALITY,
                job_id=uuid.UUID(d["job_id"]), reason="seeded hold")
            await dispositions.record(
                db, intake_id=uuid.UUID(d["intake_id"]), source_record_id=rec,
                disposition="EXCLUDED", reason_code=dispositions.REASON_ANALYST,
                reason="analyst excluded", actor="analyst@test.local", actor_type="HUMAN",
                commit=True)
    run(_decorate())
    return d


# -- shape -----------------------------------------------------------------------------

def test_ledger_rows_have_the_contract_shape(client, delivery):
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2 and body["totals"]["total"] == 2
    for row in body["items"]:
        missing = ROW_KEYS - set(row)
        assert not missing, missing
        assert row["delivery"] == {"job_id": delivery["job_id"], "intake_id": delivery["intake_id"]}
        assert row["source_row"] >= 2
        assert row["submitted_value"] == "1881659506"
        assert row["entity_name"].startswith("Lane A Org")
        assert row["normalized_value"] == "1881659506"  # curated NPI for field NPI
        assert row["status"] == "OPEN"
        assert row["evidence_source"] == "rce_quality_rules"
    assert body["totals"]["by_code"] == {"NPI-003": 1, "NPI-008": 1}
    assert body["totals"]["by_severity"] == {"HIGH": 2}
    assert body["totals"]["open"] == 2 and body["totals"]["resolved"] == 0
    assert body["total"] == 2  # list envelope: items + total
    by_code = {row["rule_code"]: row for row in body["items"]}
    assert by_code["NPI-008"]["identifier_type"] == "npi"   # conflict row
    assert by_code["NPI-003"]["identifier_type"] is None    # plain quality finding
    assert by_code["NPI-003"]["entity_id"] is None          # nothing promoted in the seed


def test_stage_is_derived_from_the_rule(client, delivery):
    from app.tefca_registry.rce.exception_ledger import stage_for

    assert stage_for("NPI-003", "NPI_CHECKSUM_INVALID") == "QUALITY"
    assert stage_for("NPI-008", "NPI_EXISTING_VALUE_CONFLICT") == "PROMOTION"
    assert stage_for("X", "IDENTIFIER_EXISTING_VALUE_CONFLICT") == "PROMOTION"
    assert stage_for("NPI-005", "NPI_NOT_FOUND") == "VERIFICATION"
    assert stage_for("NPI-006", "NPI_DEACTIVATED") == "VERIFICATION"
    assert stage_for("NPI-009", "NPI_VERIFICATION_UNAVAILABLE") == "VERIFICATION"
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions",
                   headers=headers_for("reviewer")).json()
    stages = {row["rule_code"]: row["stage"] for row in r["items"]}
    assert stages == {"NPI-003": "QUALITY", "NPI-008": "PROMOTION"}


def test_disposition_history_is_composed_per_record(client, delivery):
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions",
                   headers=headers_for("reviewer")).json()
    first = [row for row in r["items"] if row["source_record_id"] == delivery["record_ids"][0]][0]
    history = first["disposition_history"]
    assert [h["disposition"] for h in history] == ["HELD", "EXCLUDED"]
    assert [h["sequence"] for h in history] == [1, 2]
    assert first["disposition"] == "EXCLUDED"
    assert first["reason_code"] == "ANALYST_DISPOSITION"
    assert first["correlation_id"] and first["build_sha"]
    second = [row for row in r["items"] if row["source_record_id"] == delivery["record_ids"][1]][0]
    assert second["disposition_history"] == [] and second["disposition"] is None


# -- filters ----------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_codes", [
    ("rule_code=NPI-003", {"NPI-003"}),
    ("issue_type=NPI_EXISTING_VALUE_CONFLICT", {"NPI-008"}),
    ("severity=HIGH", {"NPI-003", "NPI-008"}),
    ("severity=LOW", set()),
    ("stage=QUALITY", {"NPI-003"}),
    ("stage=PROMOTION", {"NPI-008"}),
    ("stage=VERIFICATION", set()),
    ("status=OPEN", {"NPI-003", "NPI-008"}),
    ("status=RESOLVED", set()),
    ("npi=1881659506", {"NPI-003", "NPI-008"}),
    ("npi=0000000000", set()),
    ("entity_name=lane a org", {"NPI-003", "NPI-008"}),
    ("entity_name=no such org", set()),
    ("source_row=2", {"NPI-003"}),
    ("source_row=3", {"NPI-008"}),
    ("disposition=EXCLUDED", {"NPI-003"}),
    ("disposition=HELD", set()),
    ("from=2000-01-01", {"NPI-003", "NPI-008"}),
    ("to=2000-01-01", set()),
    ("from=2000-01-01&to=2999-12-31", {"NPI-003", "NPI-008"}),
])
def test_each_filter_narrows_rows_and_totals_together(client, delivery, query, expected_codes):
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions?{query}",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, (query, r.text)
    body = r.json()
    assert {row["rule_code"] for row in body["items"]} == expected_codes, query
    assert body["totals"]["total"] == len(expected_codes), query


def test_assigned_to_filter_uses_the_bridge_case(client, delivery):
    """A case held by an analyst is found by the holder's user id."""
    holder = uuid.uuid4()

    async def _hold():
        from app.core.database import async_session_maker
        from app.tefca_registry import models as reg

        async with async_session_maker() as db:
            hold_id = uuid.uuid4()
            register_review_record(hold_id)
            db.add(reg.ReviewRecord(
                id=hold_id, review_id=f"REV-{uuid.uuid4().hex[:12].upper()}"[:20],
                entity_id=None, source_record_id=uuid.UUID(delivery["record_ids"][1]),
                assigned_to_user_id=holder,
                verification_results={"queue_source": "RCE_DQ_HUMAN_REQUIRED",
                                      "source_intake_id": delivery["intake_id"],
                                      "source_record_id": delivery["record_ids"][1]}))
            await db.commit()
    run(_hold())
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions?assigned_to={holder}",
                   headers=headers_for("reviewer")).json()
    assert [row["rule_code"] for row in r["items"]] == ["NPI-008"]
    assert r["items"][0]["assignee"]["user_id"] == str(holder)
    none = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions?assigned_to={uuid.uuid4()}",
                      headers=headers_for("reviewer")).json()
    assert none["items"] == [] and none["totals"]["total"] == 0


def test_bad_filter_values_are_422(client, delivery):
    for query in ("stage=NOPE", "disposition=NOPE", "assigned_to=not-a-uuid", "from=yesterday"):
        r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions?{query}",
                       headers=headers_for("reviewer"))
        assert r.status_code == 422, (query, r.status_code)


def test_pagination(client, delivery):
    r1 = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions?limit=1&offset=0",
                    headers=headers_for("reviewer")).json()
    r2 = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions?limit=1&offset=1",
                    headers=headers_for("reviewer")).json()
    assert r1["count"] == 1 and r2["count"] == 1
    assert r1["items"][0]["issue_id"] != r2["items"][0]["issue_id"]
    assert r1["totals"]["total"] == r2["totals"]["total"] == 2  # totals ignore the page


def test_unknown_intake_is_404(client):
    r = client.get(f"{BASE}/deliveries/{uuid.uuid4()}/exceptions", headers=headers_for("reviewer"))
    assert r.status_code == 404


# -- dispositions -----------------------------------------------------------------------

def test_dispositions_listing_and_filters(client, delivery):
    url = f"{BASE}/deliveries/{delivery['intake_id']}/dispositions"
    body = client.get(url, headers=headers_for("reviewer")).json()
    assert body["total"] == 1  # only one record has a disposition in the seed
    row = body["items"][0]
    assert row["disposition"] == "EXCLUDED" and row["sequence"] == 2
    assert row["line_number"] == 2 and row["submitted_npi"] == "1881659506"
    assert client.get(url + "?disposition=HELD", headers=headers_for("reviewer")).json()["total"] == 0
    assert client.get(url + "?disposition=EXCLUDED", headers=headers_for("reviewer")).json()["total"] == 1
    assert client.get(url + "?source_row=2", headers=headers_for("reviewer")).json()["total"] == 1
    assert client.get(url + "?source_row=3", headers=headers_for("reviewer")).json()["total"] == 0
    assert client.get(url + "?entity_name=lane%20a", headers=headers_for("reviewer")).json()["total"] == 1
    assert client.get(url + "?npi=1881659506", headers=headers_for("reviewer")).json()["total"] == 1
    assert client.get(url + "?disposition=NOPE", headers=headers_for("reviewer")).status_code == 422


def test_dispositions_csv(client, delivery):
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/dispositions.csv",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["X-Total-Rows"] == "1"
    rows = list(csv.reader(io.StringIO(r.text)))
    from app.tefca_registry.rce.exception_ledger import DISPOSITION_CSV_COLUMNS
    assert rows[0] == list(DISPOSITION_CSV_COLUMNS)
    assert len(rows) == 2
    record = dict(zip(rows[0], rows[1]))
    assert record["disposition"] == "EXCLUDED" and record["line_number"] == "2"


def test_ledger_and_dispositions_are_reviewer_only(client, delivery):
    for path in ("exceptions", "dispositions", "dispositions.csv", "audit"):
        r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/{path}",
                       headers=headers_for("viewer"))
        assert r.status_code == 403, path
        assert r.json()["required_role"] == "reviewer"


# -- audit union ----------------------------------------------------------------------------

def test_audit_union_orders_sources_by_time(client, delivery):
    r = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/audit",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    sources = {e["source"] for e in body["items"]}
    assert "rce_disposition_events" in sources
    times = [e["at"] for e in body["items"]]
    assert times == sorted(times, reverse=True)
    assert body["source_caps"]["per_source"] == 2000


# -- analyst disposition endpoint ------------------------------------------------------------

def test_issue_disposition_requires_a_reason(client, delivery):
    url = f"{BASE}/issues/{delivery['issue_ids'][0]}/dispositions"
    assert client.post(url, json={"decision": "REJECT"},
                       headers=headers_for("reviewer")).status_code == 422
    assert client.post(url, json={"decision": "REJECT", "reason": "   "},
                       headers=headers_for("reviewer")).status_code == 422
    assert client.post(f"{BASE}/issues/{uuid.uuid4()}/dispositions",
                       json={"decision": "REJECT", "reason": "x"},
                       headers=headers_for("reviewer")).status_code == 404
    # an unknown decision is a 422 naming the vocabulary
    r = client.post(url, json={"decision": "NOPE", "reason": "x"}, headers=headers_for("reviewer"))
    assert r.status_code == 422 and "ACCEPT" in r.json()["error"]


def test_lineage_carries_disposition_history_and_identifier_decisions(client, delivery):
    curated = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/curated",
                         headers=headers_for("reviewer")).json()["items"]
    by_source = {c["source_record_id"]: c["id"] for c in curated}
    curated_id = by_source[delivery["record_ids"][0]]
    r = client.get(f"{BASE}/curated/{curated_id}/lineage", headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [h["disposition"] for h in body["disposition_history"]] == ["HELD", "EXCLUDED"]
    assert body["identifier_decisions"] == []
    assert client.get(f"{BASE}/curated/{curated_id}/lineage",
                      headers=headers_for("viewer")).status_code == 403


def test_identifier_decision_body_accepts_issue_and_record_refs(client, delivery):
    url = f"{BASE}/identifier-decisions"
    base = {"identifier_type": "npi", "decision": "CONFIRM_EXISTING", "reason": "r",
            "entity_id": str(uuid.uuid4()), "issue_id": delivery["issue_ids"][1],
            "source_record_id": delivery["record_ids"][1]}
    # shape is accepted (404 is the unknown entity, not a validation error)
    assert client.post(url, json=base, headers=headers_for("reviewer")).status_code == 404
    assert client.post(url, json={**base, "issue_id": "nope"},
                       headers=headers_for("reviewer")).status_code == 422
    assert client.post(url, json={**base, "decision": "CONFLICT_RAISED"},
                       headers=headers_for("reviewer")).status_code == 422


def test_issue_disposition_is_applied_and_audited(client, delivery):
    url = f"{BASE}/issues/{delivery['issue_ids'][0]}/dispositions"
    # `reject` is in curation.DISPOSITION_DECISIONS (lane P) and applies to a
    # plain quality finding (NPI-003); the conflict-only decisions are refused.
    r = client.post(url, json={"decision": "REJECT", "reason": "known vendor test record"},
                    headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "REJECT" and body["applied_by"]
    ledger = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/exceptions",
                        headers=headers_for("reviewer")).json()
    row = [x for x in ledger["items"] if x["issue_id"] == delivery["issue_ids"][0]][0]
    # The Issue Ledger state the decision lands in is curation.apply_disposition's
    # (lane P) to define - REJECT on a plain quality finding currently parks at
    # UNDER_REVIEW. What THIS API guarantees: the finding left OPEN, the actor
    # is recorded, and the totals agree with the rows (re-promotion may have
    # raised new findings, so no fixed count is asserted).
    assert row["status"] != "OPEN"
    assert row["actor"] or row["decided_at"] or row["resolution_notes"]
    open_rows = [x for x in ledger["items"] if x["status"] in ("OPEN", "PROPOSED", "UNDER_REVIEW")]
    assert ledger["totals"]["open"] == len(open_rows)
    assert ledger["totals"]["total"] == len(ledger["items"])
    audit = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/audit",
                       headers=headers_for("reviewer")).json()
    assert any(e["action"] == "analyst_disposition" for e in audit["items"])


def test_identifier_decision_validates_and_404s(client, delivery):
    url = f"{BASE}/identifier-decisions"
    base = {"identifier_type": "npi", "decision": "CONFIRM_EXISTING", "reason": "r"}
    assert client.post(url, json={**base, "entity_id": "nope"},
                       headers=headers_for("reviewer")).status_code == 422
    assert client.post(url, json={**base, "entity_id": str(uuid.uuid4())},
                       headers=headers_for("reviewer")).status_code == 404
