"""Preflight — schema, identifiers, conditional blanks and missing context,
recorded in four separate dimensions, with originals untouched and derived
normalizations recorded beside them.

Synthetic data throughout (SYNTHETIC-TRACE names, 9.99.777 OIDs). Runs
against the isolated DATABASE_URL database; skips when none is reachable.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select


QHIN = "2.16.840.1.113883.4.391.1000"
NPI_BAD_CHECKSUM = "1982916079"


def _valid_npi(seed: int) -> str:
    from app.services.npi_validator import CMS_PREFIX, _luhn_total

    base = f"{1_000_000_000 + (seed % 900_000_000):09d}"[-9:]
    for d in range(10):
        candidate = base + str(d)
        if _luhn_total(CMS_PREFIX + candidate) % 10 == 0:
            return candidate
    return base + "0"


def _rows(tag: str):
    base = {
        "domains": "RCE", "orgManagingOrg": QHIN, "purposesofuse": "T-TRTMNT",
        "active": "1", "sequoiaorgtype": "Participant", "organizationNodeType": "initiating-node",
        "address_text": "Primary", "address_line": "1 Synthetic Way",
        "address_city": "Testville", "address_state": "MA", "address_postalCode": "02101",
        "address_country": "US", "partOf": QHIN,
    }
    rows = []
    for i, over in enumerate((
        # 0: clean
        {"NPI": _valid_npi(11), "hl7orgrole": "provider"},
        # 1: bad checksum, ZIP zero stripped, lower-case state, padded address,
        #    blank role (NPI present, so no PF-CTX-001)
        {"NPI": NPI_BAD_CHECKSUM, "address_postalCode": "2718", "address_state": "ma",
         "address_line": "  2 Synthetic Way  ", "hl7orgrole": ""},
        # 2: no NPI + blank role (PF-CTX-001), unresolved parent, bad HCID/CCN shapes
        {"NPI": "", "hl7orgrole": "", "partOf": "9.99.777.does.not.exist",
         "HCID": "not-a-urn", "CCN": "ABC"},
        # 3: blank name (REQ-002) and blank TEFCAID (ID-005)
        {"NPI": _valid_npi(13), "hl7orgrole": "provider", "name": "", "TEFCAID": ""},
    )):
        v = dict(base)
        v.update({"id": f"9.99.777.{tag}.{i}",
                  "HCID": f"urn:oid:9.99.777.{tag}.{i}",
                  "TEFCAID": f"TEFCA-PF-{tag}-{i}",
                  "name": f"SYNTHETIC-TRACE Preflight Org {tag} {i}"})
        v.update(over)
        rows.append(v)
    return rows


def _bytes(rows, headers) -> bytes:
    lines = ["|".join(headers)]
    for r in rows:
        lines.append("|".join(str(r.get(h.strip().lstrip("﻿"), r.get(h, "")) or "")
                              for h in headers))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


async def _ingest(raw: bytes, tag: str):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.intake import ingest_delivery

    async with async_session_maker() as db:
        result = await ingest_delivery(
            db, raw, filename=f"SYNTHETIC-PF-{tag}.psv",
            delivery_label=f"SYNTHETIC-TRACE-preflight-{tag}", declared_delimiter="|",
            received_by="pytest-preflight@synthetic-test.docuaction.invalid")
    return uuid.UUID(str(result["intake_id"]))


async def _parsed_snapshot(intake_id):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        rows = (await db.execute(
            select(m.RceSourceRecord.line_number, m.RceSourceRecord.parsed,
                   m.RceSourceRecord.raw_line, m.RceSourceRecord.record_sha256)
            .where(m.RceSourceRecord.source_intake_id == intake_id)
            .order_by(m.RceSourceRecord.line_number))).all()
    return [tuple(r) for r in rows]


@pytest.mark.asyncio
async def test_preflight_four_dimensions_and_untouched_originals(db_required):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight as pf
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce.field_map import RCE_FIELDS

    tag = uuid.uuid4().hex[:8]
    intake_id = await _ingest(_bytes(_rows(tag), list(RCE_FIELDS)), tag)
    before = await _parsed_snapshot(intake_id)

    async with async_session_maker() as db:
        run = await pf.run_preflight(db, intake_id, actor="pytest-preflight")
    assert run["status"] == "COMPLETE"
    assert run["classification_gate"] == pm.GATE_CLEAR_WITH_FINDINGS
    assert run["records_evaluated"] == 4
    assert run["summary"]["every_record_evaluated"] is True
    assert run["summary"]["originals_modified"] is False

    # Originals untouched, byte for byte.
    assert await _parsed_snapshot(intake_id) == before

    async with async_session_maker() as db:
        findings = await pf.list_findings(db, uuid.UUID(run["run_id"]), limit=1000)
        norms = await pf.list_normalizations(db, uuid.UUID(run["run_id"]), limit=1000)
    by_code = {}
    for f in findings["items"]:
        by_code.setdefault(f["code"], []).append(f)

    # Every finding carries all four dimensions, separately.
    for f in findings["items"]:
        assert f["applicability"] in pm.APPLICABILITY
        assert f["execution"] in pm.EXECUTION
        assert isinstance(f["evidence"], dict)
        assert f["disposition"] in pm.DISPOSITION
        assert f["category"] in pm.PREFLIGHT_CATEGORIES

    # Identifiers: the quality rule was REUSED (rule_ref names it).
    bad = [f for f in by_code.get("NPI_CHECKSUM_INVALID", []) if f["line_number"] == 3]
    assert bad and bad[0]["rule_ref"] == "NPI-003" and bad[0]["category"] == "IDENTIFIER"
    assert bad[0]["applicability"] == pm.APPLIES and bad[0]["execution"] == pm.EXEC_DONE
    assert bad[0]["disposition"] == pm.DISP_OPEN
    assert bad[0]["original_value"] == NPI_BAD_CHECKSUM

    # Preflight-only identifier shape checks.
    assert any(f["line_number"] == 4 for f in by_code.get("PF-HCID-001", []))
    assert any(f["line_number"] == 4 for f in by_code.get("PF-CCN-001", []))

    # Missing context: unresolved applicability, insufficient execution,
    # informational -- never an "open" finding against the record.
    ctx = [f for f in by_code.get("PF-CTX-001", []) if f["line_number"] == 4]
    assert ctx and ctx[0]["category"] == "MISSING_CONTEXT"
    assert ctx[0]["applicability"] == pm.UNRESOLVED
    assert ctx[0]["execution"] == pm.EXEC_INSUFFICIENT
    assert ctx[0]["disposition"] == pm.DISP_INFORMATIONAL
    assert ctx[0]["evidence"]["npi_required_predicate"] is False
    # ...and not for line 3, whose NPI is present.
    assert not any(f["line_number"] == 3 for f in by_code.get("PF-CTX-001", []))
    # Delivery-level context fact recorded exactly once.
    assert len(by_code.get("PF-CTX-000", [])) == 1
    assert by_code["PF-CTX-000"][0]["evidence"]["hl7orgrole_blank"] == 2

    unresolved = [f for f in by_code.get("PART_OF_UNRESOLVED", []) if f["line_number"] == 4]
    assert unresolved and unresolved[0]["category"] == "MISSING_CONTEXT"
    assert unresolved[0]["execution"] == pm.EXEC_INSUFFICIENT and unresolved[0]["rule_ref"] == "INT-002"

    # Conditional blanks on REQUIRED fields.
    assert any(f["line_number"] == 5 and f["category"] == "CONDITIONAL_BLANK"
               for f in by_code.get("MISSING_NAME", []))
    assert any(f["line_number"] == 5 and f["rule_ref"] == "ID-005"
               for f in by_code.get("MISSING_TEFCAID", []))

    # Normalizations: original beside derived, with the method; never applied.
    methods = {(n["line_number"], n["field_name"], n["method"]): n for n in norms["items"]}
    zp = methods[(3, "address_postalCode", "ZERO_PAD")]
    assert zp["original_value"] == "2718" and zp["derived_value"] == "02718"
    up = methods[(3, "address_state", "UPPERCASE")]
    assert up["original_value"] == "ma" and up["derived_value"] == "MA"
    ws = methods[(3, "address_line", "WHITESPACE_TRIM")]
    assert ws["original_value"] == "  2 Synthetic Way  " and ws["derived_value"] == "2 Synthetic Way"
    line3 = next(r for r in before if r[0] == 3)
    assert line3[1]["address_postalCode"] == "2718"      # Area 1 still carries the original
    assert run["normalizations_count"] == len(norms["items"]) >= 3

    # Filtering by category / disposition.
    async with async_session_maker() as db:
        only_ctx = await pf.list_findings(db, uuid.UUID(run["run_id"]), category="MISSING_CONTEXT")
        with pytest.raises(ValueError):
            await pf.list_findings(db, uuid.UUID(run["run_id"]), category="BOGUS")
    assert only_ctx["total"] >= 2 and all(i["category"] == "MISSING_CONTEXT" for i in only_ctx["items"])

    # latest_run returns this run.
    async with async_session_maker() as db:
        latest = await pf.latest_run(db, intake_id)
    assert str(latest.id) == run["run_id"]


@pytest.mark.asyncio
async def test_preflight_blocks_on_a_missing_column_and_reports_a_rename(db_required):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight as pf
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce.field_map import RCE_FIELDS

    # A header with NPI removed entirely: BLOCKED.
    tag = uuid.uuid4().hex[:8]
    headers = [h for h in RCE_FIELDS if h != "NPI"]
    intake_id = await _ingest(_bytes(_rows(tag), headers), tag)
    async with async_session_maker() as db:
        run = await pf.run_preflight(db, intake_id, actor="pytest-preflight")
        findings = await pf.list_findings(db, uuid.UUID(run["run_id"]), category="SCHEMA")
    assert run["classification_gate"] == pm.GATE_BLOCKED
    sch = {f["code"]: f for f in findings["items"]}
    assert sch["PF-SCH-001"]["disposition"] == pm.DISP_BLOCKED
    assert sch["PF-SCH-001"]["evidence"]["missing_columns"] == ["NPI"]
    assert sch["PF-SCH-001"]["source_record_id"] is None   # delivery-level

    # A header where NPI is spelled "npi ": a rename candidate, open, NOT blocked,
    # and NOT auto-mapped.
    tag2 = uuid.uuid4().hex[:8]
    headers2 = ["npi " if h == "NPI" else h for h in RCE_FIELDS]
    rows2 = _rows(tag2)
    for r in rows2:
        r["npi "] = r.get("NPI", "")
    intake2 = await _ingest(_bytes(rows2, headers2), tag2)
    async with async_session_maker() as db:
        run2 = await pf.run_preflight(db, intake2, actor="pytest-preflight")
        findings2 = await pf.list_findings(db, uuid.UUID(run2["run_id"]), category="SCHEMA")
    codes2 = {f["code"]: f for f in findings2["items"]}
    assert "PF-SCH-001" not in codes2 and "PF-SCH-002" not in codes2
    assert codes2["PF-SCH-004"]["disposition"] == pm.DISP_OPEN
    assert codes2["PF-SCH-004"]["evidence"] == {"delivered_name": "npi", "expected_name": "NPI"}
    assert run2["classification_gate"] != pm.GATE_BLOCKED


async def test_preflight_blocks_on_a_duplicate_header(db_required):
    """Added 2026-10-04 (Round 22, docs/review/DELTA-2026-10-04.md): a
    delivered header with the SAME column name twice. A dict-based reader
    keeps only one value per name -- which one is undefined -- so this is a
    PARSING trust problem, same severity class as a missing column
    (PF-SCH-001), and BLOCKS the whole delivery (PF-SCH-008)."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight as pf
    from app.tefca_registry.rce import preflight_shadow_models as pm
    from app.tefca_registry.rce.field_map import RCE_FIELDS

    tag = uuid.uuid4().hex[:8]
    # HCID delivered twice; drop CCN to keep the column count the expected 41
    # (a duplicate is a distinct defect from a missing column, and this
    # keeps the two findings from being conflated in one delivery).
    headers = [h for h in RCE_FIELDS if h != "CCN"] + ["HCID"]
    rows = _rows(tag)
    for r in rows:
        r.setdefault("HCID", r.get("HCID", ""))
    intake_id = await _ingest(_bytes(rows, headers), tag)
    async with async_session_maker() as db:
        run = await pf.run_preflight(db, intake_id, actor="pytest-preflight")
        findings = await pf.list_findings(db, uuid.UUID(run["run_id"]), category="SCHEMA")
    assert run["classification_gate"] == pm.GATE_BLOCKED
    sch = {f["code"]: f for f in findings["items"]}
    assert sch["PF-SCH-008"]["disposition"] == pm.DISP_BLOCKED
    assert sch["PF-SCH-008"]["evidence"]["duplicate_columns"] == ["HCID"]
    assert sch["PF-SCH-008"]["evidence"]["occurrence_counts"]["HCID"] == 2
    assert sch["PF-SCH-008"]["source_record_id"] is None  # delivery-level


def test_preflight_routes_floor_and_shape(db_required):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.tefca_registry.rce.field_map import RCE_FIELDS
    from support_delivery_api import headers_for, run

    tag = uuid.uuid4().hex[:8]
    intake_id = run(_ingest(_bytes(_rows(tag), list(RCE_FIELDS)), tag))
    client = TestClient(app)

    # viewer: denied on every preflight surface (delivered values).
    assert client.post(f"/api/tefca/rce/deliveries/{intake_id}/preflight",
                       headers=headers_for("viewer")).status_code == 403
    assert client.get(f"/api/tefca/rce/deliveries/{intake_id}/preflight",
                      headers=headers_for("viewer")).status_code == 403

    # reviewer: before any run, 404 with a reason.
    r = client.get(f"/api/tefca/rce/deliveries/{intake_id}/preflight", headers=headers_for("reviewer"))
    assert r.status_code == 404

    r = client.post(f"/api/tefca/rce/deliveries/{intake_id}/preflight", headers=headers_for("reviewer"))
    assert r.status_code == 201, r.text
    body = r.json()
    run_id = body["run_id"]
    assert body["classification_gate"] == "CLEAR_WITH_FINDINGS"

    r = client.get(f"/api/tefca/rce/deliveries/{intake_id}/preflight", headers=headers_for("reviewer"))
    assert r.status_code == 200 and r.json()["run_id"] == run_id

    r = client.get(f"/api/tefca/rce/preflight-runs/{run_id}/findings?category=IDENTIFIER",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200 and r.json()["total"] >= 1
    for item in r.json()["items"]:
        assert {"applicability", "execution", "evidence", "disposition"} <= set(item)
    assert client.get(f"/api/tefca/rce/preflight-runs/{run_id}/findings?category=BOGUS",
                      headers=headers_for("reviewer")).status_code == 422
    r = client.get(f"/api/tefca/rce/preflight-runs/{run_id}/normalizations",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200 and r.json()["total"] >= 3
    assert client.get(f"/api/tefca/rce/preflight-runs/{run_id}/normalizations",
                      headers=headers_for("viewer")).status_code == 403
    assert client.get(f"/api/tefca/rce/preflight-runs/{uuid.uuid4()}/findings",
                      headers=headers_for("reviewer")).status_code == 404
