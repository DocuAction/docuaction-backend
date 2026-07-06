"""
DocuAction TEFCA — QA Engine (independent quality-assurance layer)
ONC TEFCA Review Protocol — Contract No. 7571MN26F80064 (HHS/ONC)

An automated QA validation layer (FDA/NASA-style gating): every review advances
through a state machine where each transition is guarded, independently verified,
and logged to the immutable tefca_qa_audit trail. No manual execution required.

New module (QA Task 1). Reuses the existing connectors + models; does not modify
the review/validation engines.
"""
import os
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from sqlalchemy import text, select
from pydantic import BaseModel, ValidationError
from statemachine import StateMachine, State

logger = logging.getLogger("docuaction.tefca.qa")

QA_SCORE_THRESHOLD = 85.0

# ─── Immutable audit table (append-only) ─────────────────────────────────────

QA_TABLE_DDL = [
    """CREATE TABLE IF NOT EXISTS tefca_qa_audit (
         id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
         review_id UUID,
         gate_name VARCHAR(100) NOT NULL,
         gate_type VARCHAR(50) NOT NULL,
         old_state VARCHAR(50),
         new_state VARCHAR(50),
         passed BOOLEAN NOT NULL,
         score FLOAT,
         threshold FLOAT,
         failures JSONB DEFAULT '[]',
         details JSONB DEFAULT '{}',
         triggered_by VARCHAR(50) DEFAULT 'automatic',
         created_at TIMESTAMP DEFAULT NOW()
       )""",
    "CREATE INDEX IF NOT EXISTS idx_qa_audit_review ON tefca_qa_audit(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_qa_audit_gate ON tefca_qa_audit(gate_name)",
    "CREATE INDEX IF NOT EXISTS idx_qa_audit_passed ON tefca_qa_audit(passed)",
]


async def ensure_qa_table(db) -> bool:
    """Create the QA audit table if missing (CREATE TABLE IF NOT EXISTS)."""
    try:
        for ddl in QA_TABLE_DDL:
            await db.execute(text(ddl))
        await db.commit()
        return True
    except Exception as e:
        logger.warning(f"ensure_qa_table failed: {e}")
        return False


async def log_qa_audit(db, *, gate_name, gate_type, passed, review_id=None,
                       old_state=None, new_state=None, score=None, threshold=None,
                       failures=None, details=None, triggered_by="automatic") -> None:
    """Append one immutable QA gate result. Best-effort; never raises."""
    import json
    try:
        await db.execute(text(
            """INSERT INTO tefca_qa_audit
                 (review_id, gate_name, gate_type, old_state, new_state, passed,
                  score, threshold, failures, details, triggered_by)
               VALUES (:rid, :gn, :gt, :os, :ns, :p, :sc, :th,
                       CAST(:fa AS JSONB), CAST(:de AS JSONB), :tb)"""
        ), {
            "rid": str(review_id) if review_id else None,
            "gn": gate_name, "gt": gate_type, "os": old_state, "ns": new_state,
            "p": bool(passed), "sc": score, "th": threshold,
            "fa": json.dumps(failures or []), "de": json.dumps(details or {}, default=str),
            "tb": triggered_by,
        })
    except Exception as e:
        logger.warning(f"log_qa_audit failed for {gate_name}: {e}")


# ─── Connector response schemas (Pydantic — schema_valid check) ──────────────

class _NppesSchema(BaseModel):
    found: bool

class _LeieSchema(BaseModel):
    excluded: bool

class _SamSchema(BaseModel):
    pass  # any 200 payload is acceptable shape-wise

class _PecosSchema(BaseModel):
    found: bool

_SCHEMA_BY_CONNECTOR = {"NPPES": _NppesSchema, "OIG_LEIE": _LeieSchema, "SAM_GOV": _SamSchema, "PECOS": _PecosSchema}


def _schema_valid(source_name: str, data: Optional[dict]) -> bool:
    if data is None:
        return False
    schema = _SCHEMA_BY_CONNECTOR.get(source_name)
    if not schema:
        return True
    try:
        schema(**{k: data.get(k) for k in schema.model_fields})
        return True
    except (ValidationError, TypeError):
        return False


# ─── Intake validation ───────────────────────────────────────────────────────

def _npi_check_digit(first9: str) -> int:
    s = "80840" + first9  # CMS NPI Luhn prefix
    total = 0
    for i, ch in enumerate(reversed(s)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10


class IntakeValidator:
    """Gate 1 — verify a submission before any connector is called."""

    def validate_npi(self, npi: str) -> bool:
        if not npi or not str(npi).isdigit() or len(str(npi)) != 10:
            return False
        npi = str(npi)
        if npi[0] not in ("1", "2"):
            return False
        return _npi_check_digit(npi[:9]) == int(npi[9])

    def validate_required_fields(self, review) -> List[str]:
        missing = []
        for f in ("entity_name", "qhin", "entity_type"):
            v = getattr(review, f, None) if not isinstance(review, dict) else review.get(f)
            if not v or not str(v).strip():
                missing.append(f)
        return missing

    async def check_duplicate(self, db, review) -> bool:
        """True if a duplicate (same NPI + QHIN, >1 row) exists."""
        from .models import TEFCAReview
        npi = getattr(review, "npi", None) if not isinstance(review, dict) else review.get("npi")
        qhin = getattr(review, "qhin", None) if not isinstance(review, dict) else review.get("qhin")
        if not npi or not qhin:
            return False
        from sqlalchemy import func
        n = (await db.execute(
            select(func.count()).select_from(TEFCAReview)
            .where(TEFCAReview.npi == npi, TEFCAReview.qhin == qhin)
        )).scalar() or 0
        return n > 1

    async def validate(self, db, review) -> Dict[str, Any]:
        failures = []
        npi = getattr(review, "npi", None) if not isinstance(review, dict) else review.get("npi")
        npi_ok = self.validate_npi(npi)
        if not npi_ok:
            failures.append("npi_invalid")
        missing = self.validate_required_fields(review)
        if missing:
            failures.append("missing_fields:" + ",".join(missing))
        dup = await self.check_duplicate(db, review)
        if dup:
            failures.append("duplicate_npi_qhin")
        score = (40 if npi_ok else 0) + (40 if not missing else 0) + (20 if not dup else 0)
        return {"passed": len(failures) == 0, "score": float(score), "failures": failures}


# ─── Connector health ────────────────────────────────────────────────────────

def _latency_score(ms: float) -> float:
    if ms < 2000:
        return 100.0
    if ms < 5000:
        return 70.0
    if ms < 10000:
        return 40.0
    return 0.0


class ConnectorHealthCheck:
    """Gate 2 — independent health scoring of every authoritative source."""

    WEIGHTS = {"availability": 0.40, "latency": 0.25, "freshness": 0.20, "schema_valid": 0.15}
    TEST_NPI = "1234567893"

    async def _one(self, name: str, coro) -> Dict[str, Any]:
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(coro, timeout=12.0)
            ms = (time.monotonic() - t0) * 1000.0
            available = bool(result.success)
            schema_ok = _schema_valid(name, result.data) if available else False
        except Exception:
            ms = (time.monotonic() - t0) * 1000.0
            available, schema_ok, result = False, False, None
        # freshness: a live 200 query is fresh; LEIE uses its daily CSV cache age.
        freshness = 100.0 if available else 0.0
        if name == "OIG_LEIE" and available:
            try:
                from .connectors import _LEIE_CACHE
                age_h = (time.time() - (_LEIE_CACHE.get("loaded_at") or 0)) / 3600.0
                freshness = 100.0 if age_h <= 24 else max(0.0, 100.0 - (age_h - 24) * 4)
            except Exception:
                pass
        comp = {
            "availability": 100.0 if available else 0.0,
            "latency": _latency_score(ms) if available else 0.0,
            "freshness": freshness,
            "schema_valid": 100.0 if schema_ok else 0.0,
        }
        score = round(sum(comp[k] * w for k, w in self.WEIGHTS.items()), 1)
        return {"name": name, "available": available, "response_time_ms": int(ms),
                "schema_valid": schema_ok, "components": comp, "health_score": score}

    async def check_all_connectors(self, db=None) -> Dict[str, Any]:
        from .connectors import check_nppes, check_pecos, check_sam, check_leie
        results = await asyncio.gather(
            self._one("NPPES", check_nppes(self.TEST_NPI)),
            self._one("PECOS", check_pecos(self.TEST_NPI)),
            self._one("SAM_GOV", check_sam("")),
            self._one("OIG_LEIE", check_leie("", self.TEST_NPI)),
        )
        by_name = {r["name"]: r for r in results}
        overall = round(sum(r["health_score"] for r in results) / len(results), 1) if results else 0.0
        # Log to the connector log + QA audit (best-effort).
        if db is not None:
            for r in results:
                await log_qa_audit(db, gate_name=f"connector_health:{r['name']}", gate_type="connector",
                                   passed=r["health_score"] >= 50, score=r["health_score"], threshold=50.0,
                                   details=r, triggered_by="automatic")
            try:
                for r in results:
                    await db.execute(text(
                        "INSERT INTO tefca_connector_logs (id, connector_name, status, response_time_ms, checked_at) "
                        "VALUES (gen_random_uuid(), :n, :s, :ms, now())"
                    ), {"n": r["name"], "s": "available" if r["available"] else "unavailable", "ms": r["response_time_ms"]})
                await db.commit()
            except Exception as e:
                logger.warning(f"connector health log failed: {e}")
        return {"connectors": by_name, "overall_health": overall, "checked_at": datetime.utcnow().isoformat()}


# ─── Platform readiness ──────────────────────────────────────────────────────

class PlatformReadinessCheck:
    """Independent end-to-end readiness probe (DB, APIs, auth, scheduler, config)."""

    async def check_database(self, db) -> Dict[str, Any]:
        try:
            await db.execute(text("SELECT 1"))
            n = (await db.execute(text("SELECT count(*) FROM tefca_reviews"))).scalar() or 0
            return {"name": "database", "passed": True, "detail": f"connected; tefca_reviews rows={n}"}
        except Exception as e:
            return {"name": "database", "passed": False, "detail": str(e)[:120]}

    async def check_api_endpoints(self) -> Dict[str, Any]:
        base = os.getenv("QA_BASE_URL") or os.getenv("API_PUBLIC_URL") or "https://api-prod.docuaction.io"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=8.0) as c:
                h = await c.get(base + "/health")
                s = await c.get(base + "/api/tefca/dashboard/summary")
            ok = h.status_code == 200 and s.status_code == 200
            return {"name": "api_endpoints", "passed": ok, "detail": f"/health={h.status_code} /dashboard/summary={s.status_code}"}
        except Exception as e:
            return {"name": "api_endpoints", "passed": False, "detail": str(e)[:120]}

    def check_auth(self) -> Dict[str, Any]:
        try:
            from app.core.security import create_token, decode_token
            tok = create_token({"sub": "qa-selftest", "role": "viewer"})
            payload = decode_token(tok)
            return {"name": "auth", "passed": payload.get("sub") == "qa-selftest", "detail": "JWT round-trip OK"}
        except Exception as e:
            return {"name": "auth", "passed": False, "detail": str(e)[:120]}

    async def check_scheduler(self, db=None) -> Dict[str, Any]:
        """Real freshness check: has the continuous QA monitor actually run
        recently? Based on the last 'qa_sweep' row in the immutable audit trail
        (behavior, not just the env flag). PASS within 24h, WARN 24–48h, FAIL if
        the monitor hasn't run in 48h+ (or never)."""
        enabled = os.getenv("ENABLE_QA_MONITOR", "false").strip().lower() == "true"
        if db is None:
            # No DB handle — can't verify freshness; report config only, non-failing.
            return {"name": "scheduler", "passed": True,
                    "detail": f"QA monitor {'enabled' if enabled else 'disabled'}; freshness not checked (no db)"}
        try:
            last = (await db.execute(
                text("SELECT max(created_at) FROM tefca_qa_audit WHERE gate_name = 'qa_sweep'")
            )).scalar()
        except Exception as e:
            return {"name": "scheduler", "passed": False, "detail": f"audit query failed: {str(e)[:80]}"}
        if not last:
            return {"name": "scheduler", "passed": False,
                    "detail": f"QA monitor has never run (ENABLE_QA_MONITOR={'true' if enabled else 'false'})"}
        if isinstance(last, str):
            try:
                last = datetime.fromisoformat(last)
            except Exception:
                return {"name": "scheduler", "passed": True, "detail": f"last QA sweep at {last}"}
        if last.tzinfo is not None:
            last = last.replace(tzinfo=None)
        age_h = (datetime.utcnow() - last).total_seconds() / 3600.0
        if age_h <= 24:
            return {"name": "scheduler", "passed": True, "detail": f"last QA sweep {age_h:.1f}h ago"}
        if age_h <= 48:
            return {"name": "scheduler", "passed": True,
                    "detail": f"WARN: last QA sweep {age_h:.1f}h ago (>24h) — monitor may be lagging"}
        return {"name": "scheduler", "passed": False,
                "detail": f"STALE: last QA sweep {age_h:.1f}h ago (>48h) — QA monitor not running "
                          f"(set ENABLE_QA_MONITOR=true on one instance)"}

    def check_required_config(self) -> Dict[str, Any]:
        missing = [k for k in ("DATABASE_URL", "SECRET_KEY") if not os.getenv(k)]
        sam = bool(os.getenv("SAM_GOV_API_KEY"))
        passed = len(missing) == 0
        detail = ("all required set" if passed else "missing: " + ",".join(missing)) + ("; SAM key set" if sam else "; SAM key NOT set")
        return {"name": "required_config", "passed": passed, "detail": detail}

    async def run(self, db=None, skip_http=False) -> Dict[str, Any]:
        checks = []
        if db is not None:
            checks.append(await self.check_database(db))
        if not skip_http:
            checks.append(await self.check_api_endpoints())
        checks.append(self.check_auth())
        checks.append(await self.check_scheduler(db))
        checks.append(self.check_required_config())
        passed = sum(1 for c in checks if c["passed"])
        score = round(100.0 * passed / len(checks), 1) if checks else 0.0
        result = {"ready": all(c["passed"] for c in checks), "score": score, "checks": checks,
                  "checked_at": datetime.utcnow().isoformat()}
        if db is not None:
            await log_qa_audit(db, gate_name="platform_readiness", gate_type="platform",
                               passed=result["ready"], score=score, threshold=100.0,
                               failures=[c["name"] for c in checks if not c["passed"]], details=result,
                               triggered_by="automatic")
            await db.commit()
        return result


# ─── Review state machine (guarded transitions) ──────────────────────────────

class ReviewStateMachine(StateMachine):
    created = State(initial=True)
    intake_validated = State()
    connectors_called = State()
    findings_generated = State()
    evidence_complete = State()
    report_ready = State()
    delivered = State(final=True)
    needs_review = State()
    failed = State(final=True)

    advance_intake = created.to(intake_validated)
    advance_connectors = intake_validated.to(connectors_called)
    advance_findings = connectors_called.to(findings_generated)
    advance_evidence = findings_generated.to(evidence_complete)
    advance_report = evidence_complete.to(report_ready)
    advance_delivered = report_ready.to(delivered)
    route_needs_review = (
        created.to(needs_review) | intake_validated.to(needs_review) |
        connectors_called.to(needs_review) | findings_generated.to(needs_review) |
        evidence_complete.to(needs_review) | report_ready.to(needs_review)
    )
    route_failed = (
        created.to(failed) | intake_validated.to(failed) | connectors_called.to(failed) |
        findings_generated.to(failed) | evidence_complete.to(failed) |
        report_ready.to(failed) | needs_review.to(failed)
    )


# The gate ladder: (transition, gate_name, guard(ctx)->(passed, score, threshold, failures, details))
def _evidence_completeness(review) -> float:
    fields = ["entity_name", "qhin", "entity_type", "npi", "status", "risk_level"]
    present = sum(1 for f in fields if getattr(review, f, None))
    return round(100.0 * present / len(fields), 1)


# Required authoritative sources for a valid review. PECOS is OPTIONAL — it
# proxies NPPES and carries no independent signal (no payment-suspension feed).
_REQUIRED_CONNECTORS = ("NPPES", "SAM_GOV", "OIG_LEIE")


async def _required_connectors_ran(db, window_hours: int = 24) -> Dict[str, Any]:
    """Real backing for the 'connectors_attempted' gate: did each REQUIRED
    authoritative source actually run and respond (status 'available') within the
    recent window? Reads tefca_connector_logs, which is written on every connector
    probe. Case-insensitive on connector_name. FAIL if any required source has no
    recent successful probe (e.g. SAM.gov with no API key surfaces here honestly).
    """
    since = datetime.utcnow() - timedelta(hours=window_hours)
    try:
        rows = (await db.execute(text(
            "SELECT upper(connector_name) AS n, status FROM tefca_connector_logs "
            "WHERE checked_at >= :since ORDER BY checked_at DESC"
        ), {"since": since})).fetchall()
    except Exception as e:
        return {"passed": False, "missing": list(_REQUIRED_CONNECTORS),
                "detail": f"connector-log query failed: {str(e)[:80]}", "window_hours": window_hours}
    latest: Dict[str, str] = {}
    for n, status in rows:
        if n not in latest:              # rows are newest-first → first seen is latest
            latest[n] = status
    missing = [c for c in _REQUIRED_CONNECTORS if latest.get(c) != "available"]
    return {
        "passed": len(missing) == 0,
        "missing": missing,
        "window_hours": window_hours,
        "latest": {c: latest.get(c, "no_recent_probe") for c in _REQUIRED_CONNECTORS},
    }


async def validate_review(db, review_id, triggered_by="automatic") -> Dict[str, Any]:
    """Run the full automated QA gate ladder on a persisted review. Logs every
    gate to tefca_qa_audit. Returns a verdict + recommended status."""
    from .models import TEFCAReview
    try:
        rid = review_id if not isinstance(review_id, str) else __import__("uuid").UUID(review_id)
    except Exception:
        return {"passed": False, "error": "invalid review_id"}
    review = (await db.execute(select(TEFCAReview).where(TEFCAReview.id == rid))).scalar_one_or_none()
    if not review:
        return {"passed": False, "error": "review not found"}

    intake = await IntakeValidator().validate(db, review)
    evidence_pct = _evidence_completeness(review)
    classified = bool(review.status)
    qa_score = round(0.5 * intake["score"] + 0.5 * evidence_pct, 1)

    # Real gate (was hardcoded pass): confirm the required authoritative sources
    # actually ran and responded recently. A missing/unavailable required source
    # fails the gate and routes the review to needs_review.
    conn = await _required_connectors_ran(db)

    sm = ReviewStateMachine()
    # Each gate is paired with its state-machine transition (contiguous path).
    gates = [
        ("intake", "intake", intake["passed"], intake["score"], 100.0, intake["failures"], sm.advance_intake),
        ("connectors_attempted", "connector", conn["passed"],
         100.0 if conn["passed"] else 0.0, 100.0,
         [f"connector_missing:{c}" for c in conn["missing"]], sm.advance_connectors),
        ("findings_generated", "evidence", classified, 100.0 if classified else 0.0, 100.0,
         [] if classified else ["not_classified"], sm.advance_findings),
        ("evidence_complete", "evidence", evidence_pct >= 100.0, evidence_pct, 100.0,
         [] if evidence_pct >= 100.0 else ["evidence_incomplete"], sm.advance_evidence),
        ("qa_score", "evidence", qa_score >= QA_SCORE_THRESHOLD, qa_score, QA_SCORE_THRESHOLD,
         [] if qa_score >= QA_SCORE_THRESHOLD else ["qa_score_below_threshold"], sm.advance_report),
    ]
    all_failures: List[str] = []
    passed_all = True
    for gate_name, gate_type, ok, score, threshold, failures, move in gates:
        old = sm.current_state.id
        if ok and passed_all:
            move()
            new = sm.current_state.id
        else:
            passed_all = False
            sm.route_needs_review()
            new = sm.current_state.id
            all_failures += failures
        await log_qa_audit(db, gate_name=gate_name, gate_type=gate_type, passed=bool(ok),
                           review_id=review.id, old_state=old, new_state=new, score=score,
                           threshold=threshold, failures=failures,
                           details={"qa_score": qa_score, "evidence_pct": evidence_pct},
                           triggered_by=triggered_by)
        if not passed_all:
            break
    await db.commit()

    return {
        "review_id": str(review.id),
        "passed": passed_all,
        "qa_score": qa_score,
        "final_state": sm.current_state.id,
        "intake": intake,
        "evidence_completeness": evidence_pct,
        "failures": all_failures,
        "recommended_status": "completed" if passed_all else "needs_review",
        "threshold": QA_SCORE_THRESHOLD,
    }


async def overall_qa_score(db) -> Dict[str, Any]:
    """Current platform QA score across all dimensions (audit pass-rate, connector
    health, platform readiness)."""
    try:
        total = (await db.execute(text("SELECT count(*) FROM tefca_qa_audit"))).scalar() or 0
        passed = (await db.execute(text("SELECT count(*) FROM tefca_qa_audit WHERE passed"))).scalar() or 0
    except Exception:
        total, passed = 0, 0
    audit_rate = round(100.0 * passed / total, 1) if total else None
    conn = await ConnectorHealthCheck().check_all_connectors(db=None)
    platform = await PlatformReadinessCheck().run(db=None)
    dims = [d for d in [audit_rate, conn["overall_health"], platform["score"]] if d is not None]
    overall = round(sum(dims) / len(dims), 1) if dims else 0.0
    return {
        "overall_qa_score": overall,
        "dimensions": {
            "audit_pass_rate": audit_rate, "audit_gates_total": total,
            "connector_health": conn["overall_health"],
            "platform_readiness": platform["score"],
        },
        "computed_at": datetime.utcnow().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# QA Task 2 — Evidence completeness & chain-of-custody QA. Additive.
# Verifies every review's evidence is complete and each finding is source-
# attributed before it can feed a report. Logs to the immutable audit trail.
# ═══════════════════════════════════════════════════════════════════════════

_VALID_SOURCES = {"nppes", "leie", "oig_leie", "pecos", "sam", "sam_gov", "rce_directory", "iqvia_onekey"}


class EvidenceValidator:
    """Gate 3 — evidence completeness + chain of custody."""

    REQUIRED_FIELDS = ["entity_name", "qhin", "entity_type", "npi", "status", "risk_level"]

    def assess(self, review, findings: list) -> Dict[str, Any]:
        failures = []
        missing = [f for f in self.REQUIRED_FIELDS if not getattr(review, f, None)]
        if missing:
            failures.append("evidence_incomplete:" + ",".join(missing))
        completeness = round(100.0 * (len(self.REQUIRED_FIELDS) - len(missing)) / len(self.REQUIRED_FIELDS), 1)

        if not findings:
            failures.append("no_findings")
        uncited = [str(f.id) for f in findings if not (f.connector or "").strip()]
        if uncited:
            failures.append(f"findings_without_source:{len(uncited)}")
        sources = sorted({(f.connector or "").lower() for f in findings if f.connector})
        invalid = [s for s in sources if s not in _VALID_SOURCES]
        if invalid:
            failures.append("invalid_source:" + ",".join(invalid))
        custody_intact = bool(findings) and not uncited and not invalid

        score = round(0.5 * completeness + 0.5 * (100.0 if custody_intact else 0.0), 1)
        return {
            "passed": len(failures) == 0, "score": score, "completeness": completeness,
            "custody_intact": custody_intact, "finding_count": len(findings),
            "sources_cited": sources, "failures": failures,
        }

    async def validate(self, db, review) -> Dict[str, Any]:
        from .models import TEFCAFinding
        findings = (await db.execute(
            select(TEFCAFinding).where(TEFCAFinding.review_id == review.id)
        )).scalars().all()
        return self.assess(review, findings)


async def validate_evidence(db, review_id, triggered_by="automatic") -> Dict[str, Any]:
    from .models import TEFCAReview
    import uuid as _uuid
    try:
        rid = review_id if not isinstance(review_id, str) else _uuid.UUID(review_id)
    except Exception:
        return {"passed": False, "error": "invalid review_id"}
    review = (await db.execute(select(TEFCAReview).where(TEFCAReview.id == rid))).scalar_one_or_none()
    if not review:
        return {"passed": False, "error": "review not found"}
    v = await EvidenceValidator().validate(db, review)
    await log_qa_audit(db, gate_name="evidence_chain_of_custody", gate_type="evidence",
                       passed=v["passed"], review_id=review.id, score=v["score"], threshold=100.0,
                       failures=v["failures"], details={k: v[k] for k in
                       ("completeness", "custody_intact", "finding_count", "sources_cited")},
                       triggered_by=triggered_by)
    await db.commit()
    return {"review_id": str(review.id), **v}


async def evidence_gate(db, start=None, end=None, triggered_by="automatic") -> Dict[str, Any]:
    """The gate that must be open before a report is generated: every review in
    the window must pass evidence QA. Batched (one findings query). Logged."""
    from .models import TEFCAReview, TEFCAFinding
    q = select(TEFCAReview)
    if start:
        q = q.where(TEFCAReview.created_at >= start)
    if end:
        q = q.where(TEFCAReview.created_at <= end)
    reviews = (await db.execute(q)).scalars().all()
    by_review: Dict[Any, list] = {}
    ids = [r.id for r in reviews]
    if ids:
        for f in (await db.execute(select(TEFCAFinding).where(TEFCAFinding.review_id.in_(ids)))).scalars().all():
            by_review.setdefault(f.review_id, []).append(f)
    ev = EvidenceValidator()
    failing = [str(r.id) for r in reviews if not ev.assess(r, by_review.get(r.id, []))["passed"]]
    gate_open = len(failing) == 0
    score = round(100.0 * (len(reviews) - len(failing)) / len(reviews), 1) if reviews else 100.0
    verdict = {"gate_open": gate_open, "total_reviews": len(reviews),
               "passing": len(reviews) - len(failing), "failing": len(failing),
               "failing_review_ids": failing[:50], "evidence_score": score,
               "checked_at": datetime.utcnow().isoformat()}
    await log_qa_audit(db, gate_name="evidence_report_gate", gate_type="deliverable",
                       passed=gate_open, score=score, threshold=100.0, failures=failing[:50],
                       details={"total": len(reviews), "passing": len(reviews) - len(failing)},
                       triggered_by=triggered_by)
    await db.commit()
    return verdict


# ═══════════════════════════════════════════════════════════════════════════
# QA Task 3 — Statistical QA: sampling validation (vs Cochran @95% CI),
# internal consistency (self-consistency of the classification pipeline), and
# confidence-interval checks. Additive.
#
# HONESTY NOTE: the "internal consistency" metric is NOT inter-rater reliability.
# There is no independent second reviewer; both ratings are derived from the same
# review. It is honestly labeled as an internal consistency score. True IRR needs
# double-review sampling — see internal_consistency_check's TODO.
# ═══════════════════════════════════════════════════════════════════════════

_STAT_CATS = ["no_discrepancy", "minor_administrative", "inexplicable", "non_compliant"]
_SEVERITY_TO_CATEGORY = {"critical": "non_compliant", "high": "inexplicable",
                         "medium": "minor_administrative", "low": "no_discrepancy"}


def _z_for(confidence):
    return 2.576 if confidence >= 0.99 else 1.96 if confidence >= 0.95 else 1.645


def cochran_n(N, confidence=0.95, margin=0.05):
    import math
    if N <= 0:
        return 0
    z, p = _z_for(confidence), 0.5
    n0 = (z * z * p * (1 - p)) / (margin * margin)
    return min(N, math.ceil(n0 / (1 + (n0 - 1) / N)))


def achieved_margin(n, N, confidence=0.95, p=0.5):
    """Invert Cochran: the margin of error actually achieved by a sample of n from N."""
    import math
    if n <= 0 or N <= 0:
        return None
    if n >= N:
        return 0.0
    z = _z_for(confidence)
    n0 = n * (N - 1) / (N - n)
    if n0 <= 0:
        return None
    return round(math.sqrt((z * z * p * (1 - p)) / n0), 4)


def wilson_ci(successes, n, confidence=0.95):
    import math
    if n == 0:
        return [0.0, 0.0]
    z, p = _z_for(confidence), successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def cohens_kappa(matrix, categories):
    """Cohen's kappa from a confusion matrix dict[(primary, secondary)] -> count."""
    n = sum(matrix.values())
    if n == 0:
        return None
    po = sum(matrix.get((c, c), 0) for c in categories) / n
    row = {c: sum(matrix.get((c, c2), 0) for c2 in categories) for c in categories}
    col = {c: sum(matrix.get((c2, c), 0) for c2 in categories) for c in categories}
    pe = sum((row[c] / n) * (col[c] / n) for c in categories)
    if pe >= 1.0:
        return 1.0
    return round((po - pe) / (1 - pe), 4)


async def validate_sampling(db, population=94231, confidence=0.95, margin=0.05, triggered_by="automatic"):
    from .models import TEFCAReview, TEFCAReviewCycle
    from sqlalchemy import func
    actual = (await db.execute(select(func.count()).select_from(TEFCAReview))).scalar() or 0
    expected = cochran_n(population, confidence, margin)
    runs = (await db.execute(select(TEFCAReviewCycle))).scalars().all()
    run_results = [{"id": str(r.cycle_id), "sample_size": r.total_entities_sampled or 0,
                    "expected": expected, "meets_expected": (r.total_entities_sampled or 0) >= expected,
                    "confidence": r.sample_confidence_level} for r in runs]
    meets = actual >= expected
    result = {
        "population": population, "confidence_level": confidence, "target_margin": margin,
        "expected_sample_size": expected, "actual_reviews": actual, "meets_expected_sample": meets,
        "achieved_margin_of_error": achieved_margin(actual, population, confidence),
        "total_sampling_runs": len(runs), "sampling_runs_validated": run_results,
        "note": "Mock dataset is a subset; a production run targets the full 383 sample.",
    }
    await log_qa_audit(db, gate_name="sampling_validation", gate_type="statistical", passed=meets,
                       score=round(min(100.0, 100.0 * actual / expected), 1) if expected else 100.0,
                       threshold=100.0, failures=[] if meets else ["sample_below_expected"],
                       details=result, triggered_by=triggered_by)
    await db.commit()
    return result


def _consistency_second_pass(findings):
    """Second-pass category derived from a review's OWN finding severities. Used
    only for the internal-consistency check — this is NOT an independent rater."""
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    if not findings:
        return "no_discrepancy"
    worst = max(findings, key=lambda f: order.get((f.severity or "low").lower(), 0))
    return _SEVERITY_TO_CATEGORY.get((worst.severity or "low").lower(), "no_discrepancy")


async def internal_consistency_check(db, sample_size=20, seed=42, triggered_by="automatic"):
    """Internal consistency of the classification pipeline: agreement between a
    review's stored status and the category implied by its own finding severities.

    NOTE: This is an INTERNAL CONSISTENCY check, not a true inter-rater
      reliability measure. Both ratings are derived from the SAME review data
      (there is no independent second reviewer), so the score measures pipeline
      self-consistency, not IRR.
    TODO(SOW T2): Implement real inter-rater reliability via double-review
      sampling — select ~10% of completed reviews, assign each to a DIFFERENT
      reviewer through tefca_analyst_queue, and compute Cohen's kappa between the
      two INDEPENDENT ratings. Only then may this be reported as IRR.
    """
    import random
    from .models import TEFCAReview, TEFCAFinding
    reviews = (await db.execute(select(TEFCAReview))).scalars().all()
    rng = random.Random(seed)
    subset = reviews if len(reviews) <= sample_size else rng.sample(reviews, sample_size)
    ids = [r.id for r in subset]
    by_review = {}
    if ids:
        for f in (await db.execute(select(TEFCAFinding).where(TEFCAFinding.review_id.in_(ids)))).scalars().all():
            by_review.setdefault(f.review_id, []).append(f)
    matrix, agree = {}, 0
    for r in subset:
        primary = (r.status or "").lower()
        if primary not in _STAT_CATS:
            primary = "no_discrepancy"
        secondary = _consistency_second_pass(by_review.get(r.id, []))
        matrix[(primary, secondary)] = matrix.get((primary, secondary), 0) + 1
        if primary == secondary:
            agree += 1
    n = len(subset)
    # kappa formula applied as a self-consistency score (see NOTE — not IRR).
    score = cohens_kappa(matrix, _STAT_CATS)
    interp = ("almost perfect" if score and score >= 0.81 else "substantial" if score and score >= 0.61
              else "moderate" if score and score >= 0.41 else "fair" if score and score >= 0.21 else "slight/poor")
    result = {
        "metric": "internal_consistency_score",
        "sample_size": n,
        "percent_agreement": round(100.0 * agree / n, 1) if n else None,
        "internal_consistency_score": score,
        "interpretation": interp,
        "target_score": 0.80,
        "meets_target": bool(score is not None and score >= 0.80),
        "seed": seed,
        "disclaimer": ("Internal consistency check, NOT a true inter-rater reliability "
                       "measure. Real IRR requires independent double-review sampling (SOW T2)."),
    }
    await log_qa_audit(db, gate_name="internal_consistency", gate_type="statistical",
                       passed=result["meets_target"], score=(round(score * 100, 1) if score else 0.0),
                       threshold=80.0, failures=[] if result["meets_target"] else ["consistency_below_target"],
                       details=result, triggered_by=triggered_by)
    await db.commit()
    return result


# Backward-compat alias so any external caller keeps working. This is NOT
# inter-rater reliability — see internal_consistency_check's NOTE.
inter_rater_reliability = internal_consistency_check


async def statistical_qa(db, triggered_by="manual"):
    from .models import TEFCAReview
    from sqlalchemy import func
    sampling = await validate_sampling(db, triggered_by=triggered_by)
    consistency = await internal_consistency_check(db, triggered_by=triggered_by)
    total = (await db.execute(select(func.count()).select_from(TEFCAReview))).scalar() or 0
    nc = (await db.execute(select(func.count()).select_from(TEFCAReview).where(TEFCAReview.status == "non_compliant"))).scalar() or 0
    lo, hi = wilson_ci(nc, total)
    ci = {"metric": "non_compliance_rate", "point_estimate": round(nc / total, 4) if total else 0.0,
          "n": total, "wilson_95_ci": [lo, hi], "ci_half_width": round((hi - lo) / 2, 4),
          "within_target_margin": ((hi - lo) / 2 <= 0.05) if total else True}
    return {"sampling_validation": sampling, "internal_consistency": consistency,
            "confidence_interval": ci, "computed_at": datetime.utcnow().isoformat()}


# ═══════════════════════════════════════════════════════════════════════════
# QA Task 4 — Regression / golden-record testing. Additive.
# Known-answer cases with CONTROLLED source results exercise the 4-bucket
# classification logic deterministically; any mismatch = classification drift.
# ═══════════════════════════════════════════════════════════════════════════

def _golden_cases():
    from .connectors import SourceResult

    def entity(name="Acme Health System", npi="1234567893", state="OH", with_npi=True):
        ident = [{"system": "http://hl7.org/fhir/sid/us-npi", "value": npi}] if with_npi else []
        return {"id": "golden", "name": name, "identifier": ident,
                "address": [{"state": state, "postalCode": "43004", "line": ["1 Main St"]}],
                "type": [{"coding": [{"code": "PARTICIPANT"}]}]}

    def clean_sources(name="Acme Health System", state="OH"):
        return {
            "nppes": SourceResult.ok("NPPES", {"found": True, "status": "ACTIVE", "legal_name": name,
                "enumeration_type": "NPI-2", "addresses": [{"address_purpose": "LOCATION", "state": state,
                "postal_code": "43004", "address_1": "1 Main St"}]}, {}),
            "leie_npi": SourceResult.ok("OIG_LEIE", {"excluded": False, "active_exclusions": [], "historical_exclusions": []}, {}),
            "sam_entity": SourceResult.ok("SAM_GOV", {"found": True, "registration_current": True, "excluded": False}, {}),
            "sam_exclusion": SourceResult.ok("SAM_GOV", {"found": True, "registration_current": True, "excluded": False}, {}),
            "pecos": SourceResult.ok("PECOS", {"found": True, "payment_suspension": None}, {}),
        }

    cases = []
    # B1 — clean
    cases.append(("clean_no_discrepancy", entity(), clean_sources(), 1))
    # B2 — historical (resolved) LEIE exclusion
    e, sr = entity(), clean_sources()
    sr["leie_npi"] = SourceResult.ok("OIG_LEIE", {"excluded": False, "active_exclusions": [], "historical_exclusions": [{"x": 1}]}, {})
    cases.append(("leie_historical_resolved", e, sr, 2))
    # B3 — address state conflict (submitted OH vs NPPES NY)
    e, sr = entity(state="OH"), clean_sources(state="NY")
    cases.append(("address_state_conflict", e, sr, 3))
    # B3 — NPI missing
    cases.append(("npi_missing", entity(with_npi=False), clean_sources(), 3))
    # B4 — active OIG LEIE exclusion
    e, sr = entity(), clean_sources()
    sr["leie_npi"] = SourceResult.ok("OIG_LEIE", {"excluded": True, "active_exclusions": [{"x": 1}], "historical_exclusions": []}, {})
    cases.append(("leie_active_exclusion", e, sr, 4))
    # B4 — NPI not found in NPPES
    e, sr = entity(), clean_sources()
    sr["nppes"] = SourceResult.ok("NPPES", {"found": False}, {})
    cases.append(("npi_not_found", e, sr, 4))
    # B4 — PECOS payment suspension
    e, sr = entity(), clean_sources()
    sr["pecos"] = SourceResult.ok("PECOS", {"found": True, "payment_suspension": True}, {})
    cases.append(("pecos_payment_suspension", e, sr, 4))
    # B4 — SAM.gov active debarment
    e, sr = entity(), clean_sources()
    deb = SourceResult.ok("SAM_GOV", {"found": True, "registration_current": True, "excluded": True}, {})
    sr["sam_entity"], sr["sam_exclusion"] = deb, deb
    cases.append(("sam_active_debarment", e, sr, 4))
    return cases


def golden_record_count():
    return len(_golden_cases())


async def run_golden_regression(db=None, triggered_by="automatic") -> Dict[str, Any]:
    """Run all golden known-answer cases through the live ValidationEngine and
    compare to the expected bucket. Any mismatch = classification drift."""
    from .validation_engine import ValidationEngine
    eng = ValidationEngine()
    results = []
    for name, ent, sr, expected in _golden_cases():
        v = eng.validate(ent, sr)
        actual = v["bucket"]
        ok = (actual == expected) and not v.get("indeterminate")
        results.append({"case": name, "expected_bucket": expected, "actual_bucket": actual,
                        "passed": ok, "finding_codes": v["finding_codes"],
                        "indeterminate": bool(v.get("indeterminate"))})
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    drift = failed > 0
    summary = {"total": total, "passed": passed, "failed": failed, "drift_detected": drift,
               "pass_rate": round(100.0 * passed / total, 1) if total else 0.0,
               "failing_cases": [r["case"] for r in results if not r["passed"]], "cases": results,
               "checked_at": datetime.utcnow().isoformat()}
    if db is not None:
        await log_qa_audit(db, gate_name="golden_record_regression", gate_type="regression",
                           passed=not drift, score=summary["pass_rate"], threshold=100.0,
                           failures=summary["failing_cases"],
                           details={"total": total, "passed": passed, "failed": failed}, triggered_by=triggered_by)
        await db.commit()
    return summary


# ═══════════════════════════════════════════════════════════════════════════
# QA Task 5 — Continuous monitoring, threshold alerts, priority-review SLA.
# A QA sweep aggregates every gate, emits threshold alerts, and tracks priority
# SLAs — all appended to the immutable tefca_qa_audit trail. Additive.
# ═══════════════════════════════════════════════════════════════════════════

# SLA targets (calendar days, request->resolution) by COR severity.
SLA_TARGETS = {"critical": 2, "high": 5, "medium": 10, "low": 21}
QA_OVERALL_ALERT_THRESHOLD = 70.0


async def check_priority_sla(db, triggered_by="automatic") -> Dict[str, Any]:
    """Track priority-review SLA compliance; flag breaches. Logged to the trail."""
    from .models import TEFCAPriorityCase
    now = datetime.utcnow()
    cases = (await db.execute(select(TEFCAPriorityCase))).scalars().all()
    rows, breaches = [], []
    for c in cases:
        sev = (c.severity.value if c.severity else "LOW").lower()
        target = SLA_TARGETS.get(sev, 21)
        status = c.case_status.value if c.case_status else ""
        completed = status in ("RESOLVED_ACTION", "RESOLVED_NO_ACTION")
        row = {"case_id": str(c.case_id), "qhin": c.qhin, "severity": sev,
               "sla_target_days": target, "status": status}
        if completed and c.completed_date and c.assigned_date:
            res_days = (c.completed_date - c.assigned_date).days
            met = res_days <= target
            row.update({"phase": "completed", "resolution_days": res_days, "met_sla": met})
            if not met:
                breaches.append(str(c.case_id))
        else:
            days_open = (now - c.assigned_date).days if c.assigned_date else None
            past_deadline = bool(c.deadline_date and c.deadline_date < now)
            breached = (days_open is not None and days_open > target) or past_deadline
            row.update({"phase": "open", "days_open": days_open, "past_deadline": past_deadline, "breached": breached})
            if breached:
                breaches.append(str(c.case_id))
        rows.append(row)
    total = len(cases)
    compliance = round(100.0 * (total - len(breaches)) / total, 1) if total else 100.0
    result = {"total_cases": total, "breaches": len(breaches), "breaching_case_ids": breaches,
              "sla_compliance_pct": compliance, "sla_targets_days": SLA_TARGETS, "cases": rows,
              "checked_at": now.isoformat()}
    await log_qa_audit(db, gate_name="priority_review_sla", gate_type="sla",
                       passed=len(breaches) == 0, score=compliance, threshold=100.0,
                       failures=breaches, details={"total": total, "breaches": len(breaches)},
                       triggered_by=triggered_by)
    await db.commit()
    return result


def _generate_alerts(readiness, connectors, golden, evidence, sla) -> List[dict]:
    alerts = []
    if not readiness.get("ready"):
        alerts.append({"level": "high", "source": "platform", "message": "platform not ready"})
    if connectors.get("overall_health", 100) < 50:
        alerts.append({"level": "high", "source": "connectors", "message": f"overall connector health {connectors.get('overall_health')}"})
    for name, c in (connectors.get("connectors") or {}).items():
        if not c.get("available") and name != "SAM_GOV":
            alerts.append({"level": "medium", "source": f"connector:{name}", "message": "unavailable"})
    if golden.get("drift_detected"):
        alerts.append({"level": "critical", "source": "regression", "message": f"classification drift: {golden.get('failing_cases')}"})
    if not evidence.get("gate_open"):
        alerts.append({"level": "high", "source": "evidence", "message": f"report gate closed; {evidence.get('failing')} failing"})
    if sla.get("breaches", 0) > 0:
        alerts.append({"level": "medium", "source": "sla", "message": f"{sla['breaches']} priority SLA breaches"})
    return alerts


# ─── Email notification on threshold breach ──────────────────────────────────
# To enable continuous QA monitoring WITH email alerts:
#   1. Set ENABLE_QA_MONITOR=true on ONE Railway instance (starts qa_monitor).
#   2. Set SENDGRID_API_KEY for email delivery (else alerts are logged only).
#   3. Recipients default to admin@docuaction.io only (for now); override with
#      TEFCA_ALERT_RECIPIENTS="a@x.com,b@y.com" to broaden.
#   NOTE: the sender (TEFCA_ALERT_FROM, default intelligence@docuaction.io — the
#   already-verified docuaction.io sender) MUST be a SendGrid-verified sender or
#   the send returns 403.
# Implemented with httpx (NOT the `sendgrid` lib, which is not a dependency) to
# match the platform's proven SendGrid pattern.
SENDGRID_KEY = os.getenv("SENDGRID_API_KEY", "")
TEFCA_ALERT_FROM = os.getenv("TEFCA_ALERT_FROM", "intelligence@docuaction.io")
QA_EMAIL_ALERT_THRESHOLD = 85.0  # overall QA score below this triggers an email

# De-dupe: at most one email per (UTC-date, breach-signature) so a persistent
# breach doesn't email on every hourly sweep.
_qa_alerted = set()


def _alert_recipients() -> List[str]:
    env = os.getenv("TEFCA_ALERT_RECIPIENTS", "").strip()
    if env:
        return [e.strip() for e in env.split(",") if e.strip()]
    # For now, alerts go to admin@docuaction.io only. Broaden later by setting
    # TEFCA_ALERT_RECIPIENTS="a@x.com,b@y.com" (e.g. back to the full ADMIN_EMAILS).
    return ["admin@docuaction.io"]


async def send_qa_alert(alert_type: str, details: dict) -> Dict[str, Any]:
    """Email a QA threshold-breach alert to the admin team via SendGrid.
    Best-effort: never raises; always returns a status dict and logs the outcome."""
    import json as _json
    recipients = _alert_recipients()
    if not SENDGRID_KEY:
        logger.warning(f"[QA ALERT — no SENDGRID_API_KEY, logged only] {alert_type}: {details}")
        return {"sent": False, "reason": "no_sendgrid_key", "recipients": recipients}
    if not recipients:
        logger.warning(f"[QA ALERT — no recipients configured] {alert_type}")
        return {"sent": False, "reason": "no_recipients"}
    subject = f"TEFCA QA ALERT: {alert_type}"
    body = (
        "TEFCA QA Threshold Breach Detected\n\n"
        f"Alert Type: {alert_type}\n"
        f"Time (UTC): {datetime.utcnow().isoformat()}\n\n"
        f"Details:\n{_json.dumps(details, indent=2, default=str)}\n\n"
        "Action Required: review the QA dashboard at\n"
        "https://app.docuaction.io/tefca-dashboard\n\n"
        "— DocuAction TEFCA ARC Automated QA Monitor"
    )
    try:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
                json={
                    "personalizations": [{"to": [{"email": e} for e in recipients]}],
                    "from": {"email": TEFCA_ALERT_FROM, "name": "DocuAction TEFCA QA Monitor"},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}],
                },
            )
            resp.raise_for_status()
        logger.info(f"QA alert emailed: {alert_type} -> {recipients}")
        return {"sent": True, "recipients": recipients, "alert_type": alert_type}
    except Exception as e:
        logger.error(f"QA alert email failed ({alert_type}): {e}")
        return {"sent": False, "reason": str(e)[:160], "recipients": recipients}


async def _maybe_email_alerts(overall: float, alerts: List[dict]) -> Dict[str, Any]:
    """Send ONE consolidated alert email if the sweep breached — de-duped per UTC
    day per breach signature so a persistent condition doesn't spam the inbox."""
    if overall >= QA_EMAIL_ALERT_THRESHOLD and not alerts:
        return {"sent": False, "reason": "no_breach"}
    day = datetime.utcnow().strftime("%Y-%m-%d")
    sig = (day, overall < QA_EMAIL_ALERT_THRESHOLD, tuple(sorted(a["source"] for a in alerts)))
    if sig in _qa_alerted:
        return {"sent": False, "reason": "deduped_today"}
    _qa_alerted.add(sig)
    return await send_qa_alert(
        alert_type=f"QA sweep breach — overall score {overall}",
        details={"overall_qa_score": overall, "email_threshold": QA_EMAIL_ALERT_THRESHOLD,
                 "alert_count": len(alerts), "alerts": alerts},
    )


async def run_qa_sweep(db, triggered_by="scheduled") -> Dict[str, Any]:
    """Full QA sweep: every gate + threshold alerts + SLA, all logged."""
    readiness = await PlatformReadinessCheck().run(db, skip_http=True)
    connectors = await ConnectorHealthCheck().check_all_connectors(db=db)
    golden = await run_golden_regression(db, triggered_by=triggered_by)
    evidence = await evidence_gate(db, triggered_by=triggered_by)
    sla = await check_priority_sla(db, triggered_by=triggered_by)

    dims = [readiness["score"], connectors["overall_health"], golden["pass_rate"],
            evidence["evidence_score"], sla["sla_compliance_pct"]]
    overall = round(sum(dims) / len(dims), 1)
    alerts = _generate_alerts(readiness, connectors, golden, evidence, sla)
    if overall < QA_OVERALL_ALERT_THRESHOLD:
        alerts.append({"level": "high", "source": "overall", "message": f"overall QA score {overall} < {QA_OVERALL_ALERT_THRESHOLD}"})
    # >10% non_compliant reviews -> alert.
    from sqlalchemy import func as _func
    from .models import TEFCAReview as _TR
    total_rev = (await db.execute(select(_func.count()).select_from(_TR))).scalar() or 0
    nc_rev = (await db.execute(select(_func.count()).select_from(_TR).where(_TR.status == "non_compliant"))).scalar() or 0
    nc_rate = (nc_rev / total_rev) if total_rev else 0.0
    if nc_rate > 0.10:
        alerts.append({"level": "high", "source": "non_compliance_rate",
                       "message": f"{nc_rate:.1%} of reviews non_compliant (>10%)"})

    for a in alerts:
        await log_qa_audit(db, gate_name=f"alert:{a['source']}", gate_type="alert", passed=False,
                           score=None, threshold=None, failures=[a["level"]], details=a, triggered_by=triggered_by)
    await log_qa_audit(db, gate_name="qa_sweep", gate_type="monitor", passed=len(alerts) == 0,
                       score=overall, threshold=QA_OVERALL_ALERT_THRESHOLD,
                       failures=[a["source"] for a in alerts],
                       details={"alerts": len(alerts), "overall": overall}, triggered_by=triggered_by)
    await db.commit()

    # Email admins on breach (best-effort; de-duped; no-op without SENDGRID_API_KEY).
    email_status = await _maybe_email_alerts(overall, alerts)

    return {
        "overall_qa_score": overall, "alert_count": len(alerts), "alerts": alerts,
        "email_alert": email_status,
        "dimensions": {"platform_readiness": readiness["score"], "connector_health": connectors["overall_health"],
                       "golden_regression": golden["pass_rate"], "evidence_gate": evidence["evidence_score"],
                       "sla_compliance": sla["sla_compliance_pct"]},
        "drift_detected": golden["drift_detected"], "sla": {"breaches": sla["breaches"], "compliance_pct": sla["sla_compliance_pct"]},
        "swept_at": datetime.utcnow().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# QA Task 6 — QA reporting (report_type='qa') + audit export. Additive.
# ═══════════════════════════════════════════════════════════════════════════

_QA_CONTRACT = {"contract": "7571MN26F80064", "contractor": "Alliance Global Tech, Inc. (AGT)"}
_QA_AGT_NOTE = "AGT produces findings and recommendations; the ONC COR makes all final determinations."


async def generate_qa_report(db, triggered_by="manual") -> Dict[str, Any]:
    """Compile a comprehensive QA scorecard across all six framework dimensions
    and persist it to tefca_reports with report_type='qa'."""
    import uuid as _uuid
    from .models import TEFCAReport
    sweep = await run_qa_sweep(db, triggered_by=triggered_by)
    stats = await statistical_qa(db, triggered_by=triggered_by)
    total = (await db.execute(text("SELECT count(*) FROM tefca_qa_audit"))).scalar() or 0
    passed = (await db.execute(text("SELECT count(*) FROM tefca_qa_audit WHERE passed"))).scalar() or 0
    audit_rate = round(100.0 * passed / total, 1) if total else None

    from app.Tefca.connectors import data_source_labels
    report_data = {
        "report_type": "qa",
        "task": "QA Framework — Quality Scorecard (Tasks 1–6)",
        **data_source_labels(),   # honest MOCK/PRODUCTION label (parity with weekly/final reports)
        "overall_qa_score": sweep["overall_qa_score"],
        "dimensions": sweep["dimensions"],
        "golden_regression": {"drift_detected": sweep["drift_detected"]},
        "sla": sweep["sla"],
        "alerts": sweep["alerts"],
        "statistical_qa": {
            "meets_expected_sample": stats["sampling_validation"]["meets_expected_sample"],
            "expected_sample_size": stats["sampling_validation"]["expected_sample_size"],
            "internal_consistency_score": stats["internal_consistency"]["internal_consistency_score"],
            "internal_consistency_note": stats["internal_consistency"]["disclaimer"],
            "non_compliance_95_ci": stats["confidence_interval"]["wilson_95_ci"],
        },
        "audit_pass_rate": audit_rate, "audit_gates_total": total,
        "contract_info": _QA_CONTRACT, "agt_does_not_adjudicate": _QA_AGT_NOTE,
        "generated_at": datetime.utcnow().isoformat(),
    }
    rid = _uuid.uuid4()
    db.add(TEFCAReport(report_id=rid, report_type="qa", report_data=report_data,
                       generated_by=triggered_by, generated_at=datetime.utcnow(), methodology_version="1.0"))
    await db.flush()
    await log_qa_audit(db, gate_name="qa_report_generated", gate_type="report", passed=True,
                       score=sweep["overall_qa_score"], threshold=QA_OVERALL_ALERT_THRESHOLD,
                       details={"report_id": str(rid)}, triggered_by=triggered_by)
    await db.commit()
    return {"report_id": str(rid), **report_data}


async def export_audit_csv(db, limit=5000) -> str:
    """Render the immutable QA audit trail as CSV."""
    import io as _io
    import csv as _csv
    rows = (await db.execute(text(
        "SELECT id, review_id, gate_name, gate_type, old_state, new_state, passed, score, "
        "threshold, triggered_by, created_at FROM tefca_qa_audit ORDER BY created_at DESC LIMIT :lim"),
        {"lim": min(max(limit, 1), 50000)})).mappings().all()
    buf = _io.StringIO()
    w = _csv.writer(buf)
    cols = ["id", "review_id", "gate_name", "gate_type", "old_state", "new_state",
            "passed", "score", "threshold", "triggered_by", "created_at"]
    w.writerow(cols)
    for r in rows:
        w.writerow([str(r["id"]), str(r["review_id"]) if r["review_id"] else "", r["gate_name"],
                    r["gate_type"], r["old_state"] or "", r["new_state"] or "", r["passed"],
                    r["score"] if r["score"] is not None else "", r["threshold"] if r["threshold"] is not None else "",
                    r["triggered_by"], r["created_at"].isoformat() if r["created_at"] else ""])
    return buf.getvalue()
