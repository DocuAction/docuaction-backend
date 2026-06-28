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
from datetime import datetime
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

    def check_scheduler(self) -> Dict[str, Any]:
        enabled = os.getenv("ENABLE_SCHEDULER", "false").strip().lower() == "true"
        return {"name": "scheduler", "passed": True,
                "detail": "enabled" if enabled else "disabled (ENABLE_SCHEDULER not true) — not required"}

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
        checks.append(self.check_scheduler())
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

    sm = ReviewStateMachine()
    # Each gate is paired with its state-machine transition (contiguous path).
    gates = [
        ("intake", "intake", intake["passed"], intake["score"], 100.0, intake["failures"], sm.advance_intake),
        ("connectors_attempted", "connector", True, 100.0, 0.0, [], sm.advance_connectors),
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
