"""
Gate 4 isolated runtime acceptance for the official-delivery remediation.

Runs the REAL application objects (FastAPI app via TestClient, the delivery
runner, the reports generator) against an ISOLATED PostgreSQL database and
writes a machine-readable evidence file plus the rendered report artefacts.

    python -m tests.acceptance.isolated_acceptance --scenario all \
        --master-file "C:/Users/imran/Downloads/DocuAction_ONC_RCE_Client_Demo_Master.csv" \
        --out docs/evidence/acceptance_2026-09-16

SAFETY
    * Refuses to run unless DATABASE_URL points at 127.0.0.1 / localhost.
    * Never reads or writes a shared DEV or PROD database.
    * The 184-record master file is read from the path given; it is never copied
      into the repository.
    * Nothing here is a pytest test: it is an operator-run harness whose output
      is the evidence, not a green/red bit.

SCENARIOS
    A  three unique invalid-NPI records            (fixture in tests/fixtures/rce)
    B  existing-entity identifier conflict         (seed 3 master rows, then the
                                                    record fixture that mutates
                                                    their NPIs)
    C  the 184-record client demo master           (from --master-file)
    D  a failed delivery                           (curation forced to raise)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))  # runnable as a plain script from any cwd
FIXTURES = REPO / "tests" / "fixtures" / "rce"
UNIQUE_FIXTURE = FIXTURES / "DocuAction_ONC_RCE_3_Unique_Entity_Invalid_NPI_Test.csv"
RECORD_FIXTURE = FIXTURES / "DocuAction_ONC_RCE_3_Record_Invalid_NPI_Test.csv"

#: The acceptance accounts' password comes from the environment and is never
#: written to disk, evidence or logs. The harness refuses to run without it.
PASSWORD = os.environ.get("ACCEPTANCE_PASSWORD", "")
USERS = {
    "pm": ("acceptance-pm@synthetic.test", "program_manager"),
    "reviewer": ("acceptance-reviewer@synthetic.test", "reviewer"),
    "qalead": ("acceptance-qa@synthetic.test", "qalead"),
    "viewer": ("acceptance-viewer@synthetic.test", "viewer"),
    "admin": ("acceptance-admin@synthetic.test", "admin"),
}


# ── guards ───────────────────────────────────────────────────────────────────

def _require_isolated_db() -> str:
    url = os.environ.get("DATABASE_URL", "")
    host = urlparse(url.replace("postgresql+asyncpg://", "postgresql://")).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            f"Refusing to run: DATABASE_URL host is {host!r}; this harness only "
            f"runs against an isolated local database.")
    return url


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── app / users ──────────────────────────────────────────────────────────────

def _use_null_pool() -> None:
    """One engine, no pooled connections.

    The TestClient serves requests on its own event loop while the pipeline
    runs under `asyncio.run` on another. A pooled asyncpg connection is bound
    to the loop that opened it, so the same NullPool swap the test harness
    (tests/conftest.py) uses is applied here; `_dispatch` keeps the asyncpg
    json/jsonb codec listeners the engine registered.
    """
    from sqlalchemy.pool import NullPool
    from app.core import database as core_db

    sync = core_db._get_engine().sync_engine
    sync.pool = NullPool(sync.pool._creator, dialect=sync.dialect,
                         _dispatch=sync.pool.dispatch)


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    _use_null_pool()
    return TestClient(app, raise_server_exceptions=False)


def _login(client, email: str) -> Dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    if r.status_code != 200:
        raise RuntimeError(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate_users() -> None:
    """Signup leaves accounts pending; the isolated harness activates them by SQL."""
    from sqlalchemy import text
    from app.core.database import async_session_maker
    async with async_session_maker() as db:
        for email, role in USERS.values():
            await db.execute(text(
                "UPDATE users SET role=:r, status='active', is_active=true, "
                "is_verified=true WHERE email=:e"), {"r": role, "e": email})
        await db.commit()


def _ensure_users(client) -> Dict[str, Dict[str, str]]:
    for email, _role in USERS.values():
        client.post("/api/auth/signup", json={
            "email": email, "password": PASSWORD,
            "full_name": "Acceptance " + email.split("@")[0],
            "company": "Synthetic"})
    asyncio.run(_activate_users())
    return {key: _login(client, email) for key, (email, _r) in USERS.items()}


# ── pipeline ─────────────────────────────────────────────────────────────────

async def _process_queued(fail_stage: Optional[str] = None) -> List[Dict[str, Any]]:
    """Claim and run every queued job in-process, exactly as the poller would."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import delivery_jobs as jobs
    from app.tefca_registry.rce import delivery_runner as runner

    results = []
    patched = None
    if fail_stage:
        attr = f"_stage_{fail_stage.lower()}"
        patched = getattr(runner, attr)

        async def _boom(*a, **k):
            raise RuntimeError(f"acceptance: forced failure at {fail_stage}")
        setattr(runner, attr, _boom)
    try:
        while True:
            async with async_session_maker() as db:
                job = await jobs.claim_next_queued(db)
                if job is None:
                    break
                state = await runner.run_delivery_job(db, job)
                results.append({"job_id": str(job.id), "state": state})
    finally:
        if patched is not None:
            setattr(runner, attr, patched)
    return results


def _register(client, headers, path: Path, *, label: str, source="ONC/RCE",
              received_date="2026-09-15", delimiter="pipe") -> Dict[str, Any]:
    with path.open("rb") as fh:
        r = client.post(
            "/api/tefca/rce/official-deliveries", headers=headers,
            files={"file": (path.name, fh, "text/csv")},
            data={"delivery_period": label, "source": source,
                  "received_date": received_date, "delimiter": delimiter,
                  "notes": "Gate 4 isolated acceptance"})
    if r.status_code != 202:
        raise RuntimeError(f"registration failed: {r.status_code} {r.text[:300]}")
    return r.json()


def _get(client, headers, path: str, expect=(200,)) -> Any:
    r = client.get(path, headers=headers)
    if r.status_code not in expect:
        raise RuntimeError(f"GET {path} -> {r.status_code} {r.text[:300]}")
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith(
        "application/json") else r.content)


def _detail(client, headers, job_id: str) -> Dict[str, Any]:
    return _get(client, headers, f"/api/tefca/rce/delivery-jobs/{job_id}/detail")[1]


def _exceptions(client, headers, intake_id: str, **filters) -> Dict[str, Any]:
    q = "&".join(f"{k}={v}" for k, v in filters.items() if v is not None)
    return _get(client, headers,
                f"/api/tefca/rce/deliveries/{intake_id}/exceptions?limit=500&{q}")[1]


def _dispositions(client, headers, intake_id: str) -> Dict[str, Any]:
    return _get(client, headers,
                f"/api/tefca/rce/deliveries/{intake_id}/dispositions?limit=1000")[1]


def _report(client, headers, job_id: str, out: Path, tag: str) -> Dict[str, Any]:
    r = client.post("/api/reports/generate", headers=headers, json={
        "report_type": "delivery_processing", "format": "json",
        "parameters": {"job_id": job_id}})
    if r.status_code != 200:
        return {"error": r.status_code, "body": r.text[:400]}
    summary = r.json()
    report_id = summary["report_id"]
    saved = {}
    for fmt, ext in (("html", "html"), ("pdf", "pdf"), ("csv", "csv")):
        rr = client.get(f"/api/reports/{report_id}/{fmt}", headers=headers)
        saved[fmt] = rr.status_code
        if rr.status_code == 200:
            (out / f"{tag}_{report_id}.{ext}").write_bytes(rr.content)
            summary.setdefault("artefact_sha256", {})[f"{tag}_{report_id}.{ext}"] =                 hashlib.sha256(rr.content).hexdigest()
            if fmt == "csv":
                rows = _record_rows_from_csv(rr.content.decode("utf-8-sig"))
                summary["csv_rows"] = len(rows)
                summary["csv_dispositions"] = _count(rows, "disposition")
    summary["downloads"] = saved
    # the 422 contract: no identifier -> refused
    r2 = client.post("/api/reports/generate", headers=headers, json={
        "report_type": "delivery_processing", "format": "json", "parameters": {}})
    summary["no_identifier_status"] = r2.status_code
    return summary


def _record_rows_from_csv(text: str) -> List[Dict[str, str]]:
    """The record-level section of the delivery CSV (it carries a preamble and
    summary sections before the per-row table; sections are separated by blank
    lines and start with '## ')."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("## Record-level dispositions")), None)
    if start is None:
        return []
    body: List[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## ") or (not line.strip() and body):
            break
        if line.strip():
            body.append(line)
    rows = list(csv.DictReader(io.StringIO(chr(10).join(body))))
    for row in rows:  # normalise the disposition column name
        for key in list(row.keys()):
            if key and key.strip().lower() in ("disposition", "current disposition"):
                row["disposition"] = row[key]
    return rows


def _count(rows, key) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows:
        out[row.get(key)] = out.get(row.get(key), 0) + 1
    return out


# ── scenarios ────────────────────────────────────────────────────────────────

def _summarise(detail: Dict[str, Any]) -> Dict[str, Any]:
    status = detail.get("status") or {}
    return {
        "resolved_from": detail.get("resolved_from"),
        "job_id": (detail.get("job") or {}).get("job_id"),
        "intake_id": (detail.get("job") or {}).get("intake_id"),
        "state": (detail.get("job") or {}).get("state"),
        "stage": (detail.get("job") or {}).get("stage"),
        "processing_outcome": (status.get("processing_outcome") or {}).get("value"),
        "review_state": (status.get("review_state") or {}).get("value"),
        "dispositions": detail.get("dispositions"),
        "exceptions": detail.get("exceptions"),
        "reconciliation": {
            k: v for k, v in ((detail.get("reconciliation") or {}).get("snapshot") or {}).items()
            if k in ("id", "passed", "equation", "hash", "build_sha", "migration_revision")},
        "timeline": [{k: ev.get(k) for k in ("stage", "attempt", "status", "started_at",
                                             "completed_at", "duration_ms", "failure_reason")}
                     for ev in (detail.get("timeline") or [])],
        "verification": detail.get("verification"),
        "build": detail.get("build"),
        "availability": detail.get("availability"),
        "failed_stage": (detail.get("job") or {}).get("failed_stage"),
        "error_reason": (detail.get("job") or {}).get("error_reason"),
        "remediation_guidance": (detail.get("job") or {}).get("remediation_guidance"),
        "correlation": detail.get("correlation"),
    }


def scenario_a(client, tokens, out: Path) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"scenario": "A", "fixture": UNIQUE_FIXTURE.name,
                          "fixture_sha256": _sha256(UNIQUE_FIXTURE)}
    receipt = _register(client, tokens["pm"], UNIQUE_FIXTURE,
                        label=f"Gate4-A {uuid.uuid4().hex[:6]}")
    job_id = receipt["job"]["job_id"]
    ev["processing"] = asyncio.run(_process_queued())
    detail = _detail(client, tokens["reviewer"], job_id)
    ev["detail"] = _summarise(detail)
    intake_id = ev["detail"]["intake_id"]
    exc = _exceptions(client, tokens["reviewer"], intake_id)
    ev["exception_rows"] = [
        {k: row.get(k) for k in ("source_row", "entity_name", "submitted_value",
                                 "existing_value", "rule_code", "issue_type",
                                 "severity", "stage", "status")}
        for row in exc.get("items", []) if str(row.get("rule_code", "")).startswith("NPI")]
    ev["exception_totals"] = {k: exc.get(k) for k in ("total", "open", "by_code", "by_severity")}
    disp = _dispositions(client, tokens["reviewer"], intake_id)
    ev["disposition_rows"] = [
        {k: row.get(k) for k in ("line_number", "curated_name", "submitted_npi",
                                 "disposition", "reason_code", "entity_id")}
        for row in disp.get("items", [])]
    ev["work_queue"] = _get(
        client, tokens["reviewer"],
        f"/api/tefca/arc/operations/work-queue?queue_source=RCE_DQ_HUMAN_REQUIRED"
        f"&intake_id={intake_id}&limit=50", expect=(200, 403, 404))[1]
    ev["viewer_detail_blocks"] = {
        k: v for k, v in (_detail(client, tokens["viewer"], job_id).get("availability") or {}).items()}
    ev["report"] = _report(client, tokens["reviewer"], job_id, out, "A")
    ev["checks"] = _checks_a(ev)
    return ev


def _checks_a(ev) -> Dict[str, bool]:
    d = ev["detail"]
    codes = {r["issue_type"] for r in ev["exception_rows"]}
    disp = d.get("dispositions") or {}
    return {
        "three_received": (disp.get("total") == 3),
        "three_held": disp.get("held") == 3,
        "distinct_codes": {"NPI_LENGTH_INVALID", "NPI_CHECKSUM_INVALID",
                           "NPI_FORMAT_INVALID"} <= codes,
        "outcome_with_exceptions": d.get("processing_outcome") == "Completed — With Exceptions",
        "review_ready_for_analyst": d.get("review_state") == "Ready for Analyst Review",
        "equation_holds": bool(((d.get("reconciliation") or {}).get("equation") or {}).get("holds")),
        "no_invalid_identifier_promoted": disp.get("created", 0) == 0 and disp.get("updated", 0) == 0,
        "report_generated": "report_id" in (ev.get("report") or {}),
        "report_csv_three_rows": (ev.get("report") or {}).get("csv_rows") == 3,
        "report_requires_identifier": (ev.get("report") or {}).get("no_identifier_status") == 422,
        "viewer_blocks_gated": any(str(v).startswith("requires_role")
                                   for v in (ev.get("viewer_detail_blocks") or {}).values()),
    }


def _seed_master_rows(master: Path, ids: List[str]) -> Path:
    """Header + the master rows whose `id` is in `ids`; written to a temp file."""
    raw = master.read_bytes().decode("utf-8-sig").splitlines()
    header, rows = raw[0], raw[1:]
    keep = [r for r in rows if r.split("|", 1)[0] in ids]
    if len(keep) != len(ids):
        raise RuntimeError(f"master file lacks seed rows: found {len(keep)} of {len(ids)}")
    tmp = Path(os.environ.get("TEMP", "/tmp")) / f"gate4_seed_{uuid.uuid4().hex[:6]}.csv"
    tmp.write_text("\ufeff" + "\n".join([header, *keep]) + "\n", encoding="utf-8")
    return tmp


def scenario_b(client, tokens, out: Path, master: Optional[Path]) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"scenario": "B", "fixture": RECORD_FIXTURE.name,
                          "fixture_sha256": _sha256(RECORD_FIXTURE)}
    if not master or not master.exists():
        ev["skipped"] = "master file not supplied; cannot seed existing entities"
        return ev
    seed_ids = ["1.2.840.114350.1.13.10.2.7.3.688884.100",
                "1.2.840.114350.1.13.104.2.7.3.688884.100",
                "1.2.840.114350.1.13.105.2.7.3.688884.100"]
    seed = _seed_master_rows(master, seed_ids)
    try:
        seed_receipt = _register(client, tokens["pm"], seed,
                                 label=f"Gate4-B-seed {uuid.uuid4().hex[:6]}",
                                 received_date="2026-09-14")
        ev["seed_processing"] = asyncio.run(_process_queued())
        seed_detail = _summarise(_detail(client, tokens["reviewer"],
                                         seed_receipt["job"]["job_id"]))
        ev["seed"] = {"job_id": seed_detail["job_id"], "dispositions": seed_detail["dispositions"],
                      "outcome": seed_detail["processing_outcome"]}
    finally:
        seed.unlink(missing_ok=True)

    receipt = _register(client, tokens["pm"], RECORD_FIXTURE,
                        label=f"Gate4-B {uuid.uuid4().hex[:6]}")
    job_id = receipt["job"]["job_id"]
    ev["processing"] = asyncio.run(_process_queued())
    detail = _detail(client, tokens["reviewer"], job_id)
    ev["detail"] = _summarise(detail)
    intake_id = ev["detail"]["intake_id"]
    exc = _exceptions(client, tokens["reviewer"], intake_id)
    ev["exception_rows"] = [
        {k: row.get(k) for k in ("source_row", "entity_name", "submitted_value",
                                 "existing_value", "rule_code", "issue_type",
                                 "severity", "stage", "status")}
        for row in exc.get("items", []) if "NPI" in str(row.get("rule_code", ""))
        or "CONFLICT" in str(row.get("issue_type", ""))]
    conflicts = [r for r in ev["exception_rows"] if r["issue_type"] == "NPI_EXISTING_VALUE_CONFLICT"]
    ev["conflicts"] = conflicts
    # the UTMB row: submitted 1982916079 vs existing 1982916078
    target = next((r for r in conflicts if r["submitted_value"] == "1982916079"), None)
    ev["utmb_conflict"] = target
    # registry state before any decision
    ev["registry_before"] = _registry_npis(client, tokens["reviewer"], intake_id)
    # analyst decision: confirm existing on the UTMB conflict
    decision = None
    if target is not None:
        entity_id = _entity_for_conflict(client, tokens["reviewer"], intake_id, "1982916079")
        if entity_id:
            r = client.post("/api/tefca/rce/identifier-decisions", headers=tokens["reviewer"],
                            json={"entity_id": entity_id, "identifier_type": "npi",
                                  "decision": "CONFIRM_EXISTING",
                                  "reason": "Gate 4 acceptance: registered NPI confirmed against NPPES; "
                                            "submitted value fails the CMS check digit."})
            decision = {"status": r.status_code, "body": r.json() if r.status_code < 500 else r.text[:200]}
            # a second decision without a reason must be refused
            r2 = client.post("/api/tefca/rce/identifier-decisions", headers=tokens["reviewer"],
                             json={"entity_id": entity_id, "identifier_type": "npi",
                                   "decision": "CONFIRM_EXISTING", "reason": ""})
            decision["no_reason_status"] = r2.status_code
            # viewer must be refused
            r3 = client.post("/api/tefca/rce/identifier-decisions", headers=tokens["viewer"],
                             json={"entity_id": entity_id, "identifier_type": "npi",
                                   "decision": "CONFIRM_EXISTING", "reason": "x"})
            decision["viewer_status"] = r3.status_code
    ev["decision"] = decision
    ev["registry_after"] = _registry_npis(client, tokens["reviewer"], intake_id)
    ev["detail_after"] = _summarise(_detail(client, tokens["reviewer"], job_id))
    ev["audit"] = _get(client, tokens["reviewer"],
                       f"/api/tefca/rce/deliveries/{intake_id}/audit", expect=(200, 404))[1]
    ev["report"] = _report(client, tokens["reviewer"], job_id, out, "B")
    ev["checks"] = {
        "conflict_raised_for_utmb": target is not None,
        "both_values_visible": bool(target and target.get("submitted_value") == "1982916079"
                                    and target.get("existing_value") == "1982916078"),
        "record_held": (ev["detail"].get("dispositions") or {}).get("held", 0) >= 1,
        "no_overwrite_before_decision": "1982916078" in json.dumps(ev["registry_before"]),
        "submitted_not_discarded": "1982916079" in json.dumps(ev["exception_rows"]),
        "decision_accepted": bool(decision and decision.get("status") in (200, 201)),
        "decision_requires_reason": bool(decision and decision.get("no_reason_status") == 422),
        "viewer_cannot_decide": bool(decision and decision.get("viewer_status") in (401, 403)),
        "no_registry_change_on_confirm_existing": ev["registry_before"] == ev["registry_after"],
    }
    return ev


def _registry_npis(client, headers, intake_id) -> Dict[str, Any]:
    """Active identifier rows of every entity named by a conflict row of the delivery."""
    _code, exc = _get(client, headers,
                      f"/api/tefca/rce/deliveries/{intake_id}/exceptions?issue_type=NPI_EXISTING_VALUE_CONFLICT&limit=50",
                      expect=(200, 403))
    out: Dict[str, Any] = {}
    if not isinstance(exc, dict):
        return out
    for row in exc.get("items", []):
        entity_id = row.get("entity_id")
        if not entity_id or entity_id in out:
            continue
        code, detail = _get(client, headers, f"/api/tefca/registry/entities/{entity_id}",
                            expect=(200, 404, 403))
        if isinstance(detail, dict):
            idents = detail.get("identifiers") or detail.get("entity", {}).get("identifiers")
            out[entity_id] = {"name": detail.get("name") or (detail.get("entity") or {}).get("name"),
                              "identifiers": idents if idents is not None else detail}
        else:
            out[entity_id] = {"status": code}
    return out


def _entity_for_conflict(client, headers, intake_id, submitted_npi) -> Optional[str]:
    _c, exc = _get(client, headers,
                   f"/api/tefca/rce/deliveries/{intake_id}/exceptions?issue_type=NPI_EXISTING_VALUE_CONFLICT&limit=50")
    for row in exc.get("items", []):
        if row.get("submitted_value") == submitted_npi:
            return row.get("entity_id")
    return None


def scenario_c(client, tokens, out: Path, master: Optional[Path]) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"scenario": "C"}
    if not master or not master.exists():
        ev["skipped"] = "master file not supplied"
        return ev
    ev["fixture"] = master.name
    ev["fixture_sha256"] = _sha256(master)
    ev["fixture_lines"] = len(master.read_bytes().decode("utf-8-sig").splitlines())
    receipt = _register(client, tokens["pm"], master, label=f"Gate4-C {uuid.uuid4().hex[:6]}",
                        received_date="2026-09-14")
    job_id = receipt["job"]["job_id"]
    ev["processing"] = asyncio.run(_process_queued())
    detail = _detail(client, tokens["reviewer"], job_id)
    ev["detail"] = _summarise(detail)
    intake_id = ev["detail"]["intake_id"]
    disp = _dispositions(client, tokens["reviewer"], intake_id)
    rows = disp.get("items", [])
    lines = [r.get("line_number") for r in rows]
    ev["disposition_counts_from_rows"] = _count(rows, "disposition")
    ev["unique_line_numbers"] = len(set(lines))
    ev["row_count"] = len(rows)
    exc = _exceptions(client, tokens["reviewer"], intake_id)
    ev["exception_totals"] = {k: exc.get(k) for k in ("total", "open", "by_code", "by_severity")}
    ev["coverage"] = _get(client, tokens["reviewer"],
                          f"/api/tefca/rce/deliveries/{intake_id}/verification-coverage",
                          expect=(200, 404))[1]
    # Held rows of the client file are RESTRICTED evidence: written to a
    # separate, git-ignored file so the committed evidence carries no delivered
    # values. The sanitized evidence keeps only the count and the rule codes.
    held_rows = [r for r in rows if r.get("disposition") == "HELD"]
    restricted = out / "restricted"
    restricted.mkdir(parents=True, exist_ok=True)
    held_detail = []
    for r in held_rows:
        findings = [x for x in exc.get("items", []) if x.get("source_row") == r.get("line_number")]
        held_detail.append({
            "line_number": r.get("line_number"), "source_record_id": r.get("source_record_id"),
            "entity_name": r.get("curated_name"), "submitted_npi": r.get("submitted_npi"),
            "reason_code": r.get("reason_code"), "reason": r.get("reason"),
            "entity_id": r.get("entity_id"),
            "findings": [{k: x.get(k) for k in ("issue_code", "rule_code", "issue_type",
                                                "severity", "submitted_value", "existing_value",
                                                "description")} for x in findings],
            "required_analyst_action": (
                "Open the Exceptions tab for this delivery, filter source row "
                f"{r.get('line_number')}, and record a disposition (accept, reject, correct, "
                "confirm existing or confirm submitted) with a reason; the hold is "
                "recomputed and a new reconciliation snapshot is written."),
        })
    (restricted / "C_184_held_rows.json").write_text(
        json.dumps({"intake_id": intake_id, "job_id": job_id, "held": held_detail},
                   indent=2, default=str), encoding="utf-8")
    ev["held_rows_summary"] = [{"line_number": h["line_number"],
                                "rule_codes": sorted({f["rule_code"] for f in h["findings"]}),
                                "issue_types": sorted({f["issue_type"] for f in h["findings"]}),
                                "reason_code": h["reason_code"]} for h in held_detail]
    ev["report"] = _report(client, tokens["reviewer"], job_id, out, "C")
    d = ev["detail"]
    eq = ((d.get("reconciliation") or {}).get("equation") or {})
    api_disp = d.get("dispositions") or {}
    ev["checks"] = {
        "received_184": eq.get("received") == 184,
        "accounted_184": eq.get("accounted") == 184,
        "equation_holds": bool(eq.get("holds")),
        "rows_184": len(rows) == 184,
        "unique_lines_184": len(set(lines)) == 184,
        "api_rows_agree": all(api_disp.get(k.lower(), api_disp.get(k)) == v
                              for k, v in ev["disposition_counts_from_rows"].items()),
        "csv_agrees": (ev["report"].get("csv_dispositions") == ev["disposition_counts_from_rows"]),
        "timeline_has_durations": all(e.get("duration_ms") is not None
                                      for e in d.get("timeline", []) if e.get("status") != "STARTED"),
        "outcome_derived": d.get("processing_outcome") in ("Completed — Clean",
                                                            "Completed — With Exceptions"),
        "pecos_data_driven": isinstance(((d.get("verification") or {}).get("sources") or {}).get("pecos"), dict),
        "build_sha_present": bool((d.get("build") or {}).get("git_sha")),
        "report_linked": bool(ev["report"].get("delivery_link") or ev["report"].get("report_id")),
    }
    return ev


def scenario_d(client, tokens, out: Path) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"scenario": "D", "fixture": UNIQUE_FIXTURE.name}
    receipt = _register(client, tokens["pm"], UNIQUE_FIXTURE,
                        label=f"Gate4-D {uuid.uuid4().hex[:6]}")
    job_id = receipt["job"]["job_id"]
    ev["processing"] = asyncio.run(_process_queued(fail_stage="CURATION"))
    detail = _detail(client, tokens["reviewer"], job_id)
    ev["detail"] = _summarise(detail)
    # by intake id as well, when one exists
    intake_id = ev["detail"].get("intake_id")
    if intake_id:
        ev["detail_by_intake"] = _summarise(_detail(client, tokens["reviewer"], intake_id))
        ev["area1_records"] = _get(client, tokens["reviewer"],
                                   f"/api/tefca/rce/deliveries/{intake_id}/records?limit=5")[1]
    # a job that never produced an intake: undecidable delimiter
    bad = Path(os.environ.get("TEMP", "/tmp")) / f"gate4_bad_{uuid.uuid4().hex[:6]}.csv"
    bad.write_text("\ufeffnot a delivery at all\njust two lines with no delimiter\n", encoding="utf-8")
    try:
        r2 = _register(client, tokens["pm"], bad, label=f"Gate4-D2 {uuid.uuid4().hex[:6]}",
                       delimiter="")
        ev["processing_no_intake"] = asyncio.run(_process_queued())
        ev["detail_no_intake"] = _summarise(_detail(client, tokens["reviewer"], r2["job"]["job_id"]))
    finally:
        bad.unlink(missing_ok=True)
    ev["unknown_job_status"] = client.get(
        f"/api/tefca/rce/delivery-jobs/{uuid.uuid4()}/detail", headers=tokens["reviewer"]).status_code
    d = ev["detail"]
    ev["checks"] = {
        "outcome_failed": d.get("processing_outcome") == "Failed",
        "review_not_ready": d.get("review_state") == "Not Ready",
        "failed_stage_curation": d.get("failed_stage") == "CURATION",
        "error_reason_present": bool(d.get("error_reason")),
        "guidance_present": bool(d.get("remediation_guidance")),
        "timeline_shows_failure": any(e.get("status") == "FAILED" for e in d.get("timeline", [])),
        "correlation_present": bool((d.get("correlation") or {}).get("request_id")),
        "area1_evidence_readable": bool(ev.get("area1_records")),
        "no_intake_job_opens": bool((ev.get("detail_no_intake") or {}).get("job_id")),
        "unknown_job_is_404": ev["unknown_job_status"] == 404,
    }
    return ev


# ── sanitized evidence (what may be committed) ───────────────────────────────

_KEEP_DETAIL = ("job_id", "intake_id", "state", "stage", "processing_outcome",
                "review_state", "dispositions", "reconciliation", "build",
                "failed_stage", "correlation", "availability")


def _sanitize(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Counts, identifiers, hashes, codes and check results only.

    Delivered rows, entity names, identifier values and registry payloads are
    dropped, so the file can live in the repository regardless of which
    delivery produced it. The unsanitized file stays local.
    """
    out = {"generated_at": evidence.get("generated_at"),
           "health": evidence.get("health"),
           "database": evidence.get("database"), "scenarios": {}}
    for name, s in (evidence.get("scenarios") or {}).items():
        d = s.get("detail") or {}
        rep = s.get("report") or {}
        out["scenarios"][name] = {
            "scenario": name,
            "fixture": s.get("fixture"), "fixture_sha256": s.get("fixture_sha256"),
            "fixture_lines": s.get("fixture_lines"),
            "error": s.get("error"), "skipped": s.get("skipped"),
            "processing": s.get("processing"),
            "detail": {k: d.get(k) for k in _KEEP_DETAIL if k in d},
            "timeline": [{k: e.get(k) for k in ("stage", "attempt", "status",
                                                "started_at", "completed_at",
                                                "duration_ms")}
                         for e in d.get("timeline", [])],
            "exception_codes": sorted({r.get("issue_type") for r in s.get("exception_rows", [])
                                       if r.get("issue_type")}),
            "exception_totals": s.get("exception_totals"),
            "disposition_counts": s.get("disposition_counts_from_rows")
            or {r.get("disposition"): None for r in []},
            "row_count": s.get("row_count"), "unique_line_numbers": s.get("unique_line_numbers"),
            "held_rows_summary": s.get("held_rows_summary"),
            "artefact_sha256": s.get("artefact_sha256"),
            "report": {k: rep.get(k) for k in ("report_id", "report_type", "snapshot_id",
                                               "csv_rows", "csv_dispositions", "downloads",
                                               "no_identifier_status", "delivery_link",
                                               "artefact_sha256")},
            "decision": ({"status": (s.get("decision") or {}).get("status"),
                          "no_reason_status": (s.get("decision") or {}).get("no_reason_status"),
                          "viewer_status": (s.get("decision") or {}).get("viewer_status")}
                         if s.get("decision") else None),
            "checks": s.get("checks"),
        }
    return out


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="all", choices=["A", "B", "C", "D", "all"])
    parser.add_argument("--master-file", default=None)
    parser.add_argument("--out", default="docs/evidence/acceptance_2026-09-16")
    args = parser.parse_args(argv)

    _require_isolated_db()
    if len(PASSWORD) < 16:
        raise SystemExit("Refusing to run: set ACCEPTANCE_PASSWORD (>= 16 characters) for the "
                         "synthetic acceptance accounts. No default is provided.")
    out = (REPO / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    master = Path(args.master_file) if args.master_file else None

    client = _client()
    tokens = _ensure_users(client)
    health = client.get("/health").json()
    evidence: Dict[str, Any] = {
        "generated_at": _now(), "health": health,
        "database": urlparse(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://",
                                                                 "postgresql://")).hostname,
        "scenarios": {},
    }
    wanted = ["A", "B", "C", "D"] if args.scenario == "all" else [args.scenario]
    for name in wanted:
        try:
            if name == "A":
                evidence["scenarios"]["A"] = scenario_a(client, tokens, out)
            elif name == "B":
                evidence["scenarios"]["B"] = scenario_b(client, tokens, out, master)
            elif name == "C":
                evidence["scenarios"]["C"] = scenario_c(client, tokens, out, master)
            elif name == "D":
                evidence["scenarios"]["D"] = scenario_d(client, tokens, out)
        except Exception as exc:  # noqa: BLE001 — the evidence must record the failure
            import traceback
            evidence["scenarios"][name] = {"scenario": name,
                                           "error": f"{type(exc).__name__}: {exc}",
                                           "traceback": traceback.format_exc()[-3000:]}
    path = out / "isolated_acceptance_evidence.json"
    path.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    sanitized = out / "isolated_acceptance_evidence.sanitized.json"
    sanitized.write_text(json.dumps(_sanitize(evidence), indent=2, default=str), encoding="utf-8")
    print(json.dumps({name: (s.get("checks") or s.get("error") or s.get("skipped"))
                      for name, s in evidence["scenarios"].items()}, indent=2, default=str))
    print(f"evidence: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
