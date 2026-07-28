"""
TEFCA registry verification engine (Phase 2A).

Runs data-quality checks over the registry and writes verification jobs, checks,
findings, and audit rows. Phase 2A implements the INTERNAL checks (identity +
hierarchy) which need no external network calls and surface the structural
defects seeded in Phase 1D. External authoritative-source checks
(NPPES/LEIE/SAM/PECOS) are gated behind ``include_external`` and are a follow-on;
seed identifiers are synthetic, so live sources would false-flag every entity.

Idempotent: job / check / finding / audit rows use deterministic ``uuid5`` ids
(latest-run-wins), so repeated verification does not pile up duplicates.
"""
from __future__ import annotations

import datetime as dt
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry import models as reg
from app.tefca_registry.queries import HIERARCHY_TYPES

_VNS = uuid.UUID("7f2a9c14-6b0d-5e42-a133-8c9e1d4b7a05")


def _vid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_VNS, f"{kind}:{key}")


def _npi_valid(npi: str) -> bool:
    """Validate a 10-digit NPI via the Luhn algorithm over '80840' + first 9 digits."""
    if not npi or len(npi) != 10 or not npi.isdigit():
        return False
    s = "80840" + npi[:9]
    total = 0
    for i, ch in enumerate(reversed(s)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (total + int(npi[9])) % 10 == 0


# ── global context ────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self):
        self.entities: dict = {}
        self.idents_by_entity: dict = {}
        self.npi_owners: dict = {}
        self.hcid_owners: dict = {}
        self.tefcaid_owners: dict = {}
        self.active_parents: dict = {}     # child -> [(parent_id, rtype)]
        self.active_children: dict = {}    # parent -> [(child_id, rtype)]
        self.involved: dict = {}           # entity -> count of ANY relationship rows
        self.parent_rows_any: dict = {}    # child -> [rel] any status (hierarchy types)
        self.cycle_nodes: set = set()


async def _load_context(session: AsyncSession) -> _Ctx:
    ctx = _Ctx()
    entities = (await session.execute(select(reg.TefcaRegEntity))).scalars().all()
    ctx.entities = {e.id: e for e in entities}

    idents = (await session.execute(select(reg.TefcaEntityIdentifier))).scalars().all()
    for i in idents:
        ctx.idents_by_entity.setdefault(i.entity_id, []).append(i)
        if i.identifier_status == "active":
            if i.identifier_type == "npi":
                ctx.npi_owners.setdefault(i.identifier_value, set()).add(i.entity_id)
            elif i.identifier_type == "hcid":
                ctx.hcid_owners.setdefault(i.identifier_value, set()).add(i.entity_id)
            elif i.identifier_type == "tefcaid":
                ctx.tefcaid_owners.setdefault(i.identifier_value, set()).add(i.entity_id)

    rels = (await session.execute(select(reg.TefcaEntityRelationship))).scalars().all()
    adj = {}  # active directed edges (any type) for cycle detection
    for r_ in rels:
        ctx.involved[r_.parent_entity_id] = ctx.involved.get(r_.parent_entity_id, 0) + 1
        ctx.involved[r_.child_entity_id] = ctx.involved.get(r_.child_entity_id, 0) + 1
        if r_.relationship_type in HIERARCHY_TYPES:
            ctx.parent_rows_any.setdefault(r_.child_entity_id, []).append(r_)
        if r_.status == "active":
            adj.setdefault(r_.parent_entity_id, []).append(r_.child_entity_id)
            if r_.relationship_type in HIERARCHY_TYPES:
                ctx.active_parents.setdefault(r_.child_entity_id, []).append(
                    (r_.parent_entity_id, r_.relationship_type))
                ctx.active_children.setdefault(r_.parent_entity_id, []).append(
                    (r_.child_entity_id, r_.relationship_type))
    ctx.cycle_nodes = _tarjan_cycle_nodes(adj)
    return ctx


def _tarjan_cycle_nodes(adj: dict) -> set:
    """Return the set of nodes participating in a cycle (SCC of size >1)."""
    index = {}
    low = {}
    on_stack = {}
    stack = []
    counter = [0]
    cyclic = set()
    nodes = set(adj.keys()) | {c for cs in adj.values() for c in cs}

    def strongconnect(v):
        # iterative Tarjan to avoid recursion limits
        work = [(v, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack[node] = True
            recursed = False
            succs = adj.get(node, [])
            for i in range(pi, len(succs)):
                w = succs[i]
                if w not in index:
                    work[-1] = (node, i + 1)
                    work.append((w, 0))
                    recursed = True
                    break
                elif on_stack.get(w):
                    low[node] = min(low[node], index[w])
            if recursed:
                continue
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    cyclic.update(comp)
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])

    for n in nodes:
        if n not in index:
            strongconnect(n)
    return cyclic


# ── per-entity checks ─────────────────────────────────────────────────────────

def _finding(ftype, severity, title, description, evidence=None):
    return {"finding_type": ftype, "severity": severity, "title": title,
            "description": description, "evidence": evidence or {}}


def _check_identity(e, ctx) -> list[dict]:
    out = []
    idents = ctx.idents_by_entity.get(e.id, [])
    by_type = {}
    for i in idents:
        by_type.setdefault(i.identifier_type, []).append(i)

    tefcaids = by_type.get("tefcaid", [])
    hcids = by_type.get("hcid", [])
    npis = by_type.get("npi", [])
    ccns = by_type.get("ccn", [])

    if not tefcaids:
        out.append(_finding("identifier_missing", "critical", "Missing mandatory TEFCAID",
                            "Entity has no TEFCAID identifier."))
    if not hcids:
        out.append(_finding("identifier_missing", "critical", "Missing mandatory HCID",
                            "Entity has no HCID identifier."))

    # retired TEFCAID on an active entity
    for t in tefcaids:
        if t.identifier_status == "retired" and e.is_active:
            out.append(_finding("identifier_retired", "high", "Retired TEFCAID on active entity",
                                f"TEFCAID {t.identifier_value} is retired but the entity is active.",
                                {"identifier_value": t.identifier_value}))

    # NPI checks
    active_npis = [n for n in npis if n.identifier_status == "active"]
    purposes = (e.exchange_purposes or {}).get("purposes", []) if isinstance(e.exchange_purposes, dict) else []
    if (e.entity_type == "provider" and "treatment" in purposes and not npis):
        out.append(_finding("identifier_missing", "medium", "Missing NPI on treatment entity",
                            "Treatment-purpose provider has no NPI identifier."))
    for n in npis:
        if not _npi_valid(n.identifier_value):
            out.append(_finding("npi_invalid", "high", "Invalid NPI",
                                f"NPI {n.identifier_value} fails the Luhn check digit.",
                                {"npi": n.identifier_value}))
    if len(active_npis) > 1:
        out.append(_finding("identifier_conflict", "high", "Multiple active NPIs",
                            f"Entity has {len(active_npis)} active NPIs.",
                            {"npis": [n.identifier_value for n in active_npis]}))

    # expired CCN
    today = dt.date.today()
    for c in ccns:
        if c.identifier_status == "expired" or (c.end_date and c.end_date < today):
            out.append(_finding("enrollment_expired", "medium", "Expired CCN",
                                f"CCN {c.identifier_value} is expired.",
                                {"ccn": c.identifier_value, "end_date": str(c.end_date)}))

    # duplicate identifiers (global)
    for n in active_npis:
        owners = ctx.npi_owners.get(n.identifier_value, set())
        if len(owners) > 1:
            out.append(_finding("npi_duplicate", "high", "Duplicate NPI",
                                f"NPI {n.identifier_value} is shared by {len(owners)} entities.",
                                {"npi": n.identifier_value, "entity_ids": [str(x) for x in owners]}))
    for h in hcids:
        if h.identifier_status == "active" and len(ctx.hcid_owners.get(h.identifier_value, set())) > 1:
            out.append(_finding("hcid_duplicate", "high", "Duplicate HCID",
                                f"HCID {h.identifier_value} is shared by multiple entities.",
                                {"hcid": h.identifier_value}))
    for t in tefcaids:
        if t.identifier_status == "active" and len(ctx.tefcaid_owners.get(t.identifier_value, set())) > 1:
            out.append(_finding("identifier_conflict", "high", "Duplicate TEFCAID",
                                f"TEFCAID {t.identifier_value} is shared by multiple entities.",
                                {"tefcaid": t.identifier_value,
                                 "entity_ids": [str(x) for x in ctx.tefcaid_owners[t.identifier_value]]}))
    return out


def _check_hierarchy(e, ctx) -> list[dict]:
    out = []
    level = e.entity_level
    active_parents = ctx.active_parents.get(e.id, [])
    any_parent_rows = ctx.parent_rows_any.get(e.id, [])
    active_children = ctx.active_children.get(e.id, [])

    # circular
    if e.id in ctx.cycle_nodes:
        out.append(_finding("circular_relationship", "critical", "Circular relationship",
                            "Entity participates in a relationship cycle."))

    # inactive / soft-deleted parent with active children
    if active_children and (e.operational_status == "inactive" or not e.is_active):
        out.append(_finding("inactive_parent", "high", "Inactive parent with active children",
                            f"Entity is {'soft-deleted' if not e.is_active else 'operationally inactive'} "
                            f"but has {len(active_children)} active child relationship(s)."))

    if level == "qhin":
        if not active_children:
            out.append(_finding("broken_hierarchy", "medium", "QHIN has zero participants",
                                "QHIN has no active participant relationships."))
        return out

    # participant / sub_participant / child hierarchy integrity
    if ctx.involved.get(e.id, 0) == 0:
        out.append(_finding("orphan_entity", "medium", "Entity has zero relationships",
                            "Entity has no relationships of any kind."))
        return out

    if not active_parents:
        if any_parent_rows:
            out.append(_finding("orphan_entity", "high", "No active parent relationship",
                                "Entity's parent relationship(s) are all inactive/historical."))
        else:
            out.append(_finding("orphan_entity", "high", "Orphan entity",
                                "Entity has no parent relationship."))
        return out

    if len(active_parents) > 1:
        out.append(_finding("broken_hierarchy", "high", "Multiple active parents",
                            f"Entity has {len(active_parents)} active parent relationships."))

    expected = {"participant": "qhin", "sub_participant": "participant", "child": "sub_participant"}
    want = expected.get(level)
    for parent_id, _rtype in active_parents:
        parent = ctx.entities.get(parent_id)
        if parent and want and parent.entity_level != want:
            out.append(_finding("broken_hierarchy", "high", "Incorrect parent level",
                                f"{level} is parented by a {parent.entity_level} "
                                f"(expected {want}).",
                                {"parent_id": str(parent_id),
                                 "parent_level": parent.entity_level}))
    return out


def _entity_status(findings) -> str:
    sev = {f["severity"] for f in findings}
    if sev & {"critical", "high"}:
        return "exception"
    if findings:
        return "in_review"
    return "verified"


# ── run ───────────────────────────────────────────────────────────────────────

async def _upsert(session, model, rows):
    if not rows:
        return
    table = model.__table__
    stmt = pg_insert(table).values(rows)
    update_cols = {c.name: getattr(stmt.excluded, c.name)
                   for c in table.columns if c.name not in ("id", "created_at")}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    await session.execute(stmt)


async def verify_entities(session: AsyncSession, entity_ids, *, include_external=False,
                          trigger_type="manual", actor_id=None, actor_email=None) -> dict:
    ctx = await _load_context(session)
    now = dt.datetime.utcnow()

    job_rows, check_rows, finding_rows, audit_rows = [], [], [], []
    status_updates = {}
    sev_totals = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    total_findings = 0

    for eid in entity_ids:
        e = ctx.entities.get(eid)
        if not e:
            continue
        t0 = time.monotonic()
        findings = _check_identity(e, ctx) + _check_hierarchy(e, ctx)

        job_id = _vid("job", str(eid))
        check_id = _vid("check", f"{eid}:manual")
        duration = int((time.monotonic() - t0) * 1000)

        # one internal 'manual' check summarizing the rule evaluation
        check_rows.append(dict(
            id=check_id, job_id=job_id, source="manual",
            identifier_used=None, identifier_type=None,
            result="fail" if findings else "pass",
            evidence_hash=None,
            response_data={"internal_rules": True, "findings": len(findings)},
            discrepancies=[f["title"] for f in findings] or None,
            checked_at=now,
        ))
        if include_external:
            # Plumbing only in Phase 2A — external sources not called (synthetic data).
            check_rows.append(dict(
                id=_vid("check", f"{eid}:nppes"), job_id=job_id, source="nppes",
                identifier_used=None, identifier_type="npi", result="skipped",
                evidence_hash=None,
                response_data={"note": "external verification not enabled in Phase 2A"},
                discrepancies=None, checked_at=now,
            ))

        for idx, f in enumerate(findings):
            fid = _vid("finding", f"{eid}:{f['finding_type']}:{f['title']}")
            finding_rows.append(dict(
                id=fid, entity_id=eid, verification_check_id=check_id,
                finding_type=f["finding_type"], severity=f["severity"],
                title=f["title"], description=f["description"],
                evidence=f["evidence"], status="open",
                resolved_by=None, resolved_at=None, resolution_notes=None,
            ))
            sev_totals[f["severity"]] = sev_totals.get(f["severity"], 0) + 1
            total_findings += 1
            audit_rows.append(dict(
                id=_vid("audit", f"finding_created:{fid}"), entity_id=eid,
                action="finding_created", actor_id=actor_id, actor_email=actor_email,
                metadata={"finding_type": f["finding_type"], "severity": f["severity"],
                          "title": f["title"]}, ip_address=None,
            ))

        new_status = _entity_status(findings)
        status_updates[eid] = new_status
        job_rows.append(dict(
            id=job_id, entity_id=eid, entity_version_id=None,
            status="completed", trigger_type=trigger_type,
            initiated_by=actor_id, started_at=now, completed_at=now,
            duration_ms=duration,
            summary={"findings": len(findings), "internal_only": not include_external,
                     "result_status": new_status},
        ))
        audit_rows.append(dict(
            id=_vid("audit", f"verification_completed:{eid}"), entity_id=eid,
            action="verification_completed", actor_id=actor_id, actor_email=actor_email,
            metadata={"findings": len(findings), "status": new_status}, ip_address=None,
        ))

    # persist (jobs -> checks -> findings; then status; then audit)
    await _upsert(session, reg.TefcaVerificationJob, job_rows)
    await _upsert(session, reg.TefcaVerificationCheck, check_rows)
    await _upsert(session, reg.TefcaEntityFinding, finding_rows)
    for eid, st in status_updates.items():
        await session.execute(
            update(reg.TefcaRegEntity).where(reg.TefcaRegEntity.id == eid)
            .values(verification_status=st))
    await _upsert(session, reg.TefcaRegAuditLog, audit_rows)
    await session.commit()

    return {
        "entities_verified": len(status_updates),
        "jobs": len(job_rows),
        "findings_created": total_findings,
        "findings_by_severity": {k: v for k, v in sev_totals.items() if v},
        "external_included": include_external,
    }


async def verify_one(session: AsyncSession, entity_id, **kw) -> dict:
    return await verify_entities(session, [entity_id], **kw)
