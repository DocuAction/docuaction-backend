"""Program Manager and Analyst workflow surface.

Three screens, one router:

  QHIN WORK ORGANISATION   how the review population is actually shaped
  ASSIGNMENT               who looks at what — workload only
  VERIFICATION WORKSPACE   everything about one case, assembled once

WHAT IS NOT HERE
────────────────
No review cycle creation, no sampling, no determination, no QA action. Every one
of those already has a route in `review_routes.py` and this module deliberately
does not offer a second way to do them. The sampling methodology in particular
is `qhin_sampling.finalize_plan` and is reached through the existing
`POST /samples` — nothing here recalculates it, previews an alternative to it,
or exposes a knob that would let a caller draw a different one.

WHAT THIS ROUTER ADDS is the read surface those screens were missing, plus one
write: bulk workload distribution, which routes every individual assignment
through the existing audited `case_assignment.assign`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/workflow", tags=["TEFCA ARC Workflow"])


def _client_ip(request: Request):
    from app.core.client_ip import get_client_ip
    return get_client_ip(request)


# ── QHIN work organisation ───────────────────────────────────────────────────

@router.get("/deliveries/{intake_id}/qhins",
            summary="Review population organised by QHIN")
async def qhin_organisation(
    intake_id: str,
    sample_id: Optional[uuid.UUID] = Query(
        None, description="Narrow the review columns to one drawn sample. The "
                          "population column always reflects the whole delivery."),
    include_held: bool = Query(
        False, description="Include HELD records in the population. Their "
                           "eligibility is an open ONC question and they are "
                           "excluded by default."),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Per-QHIN population, review volume, assignment and QA progress.

    This is the screen that makes 25,000 rows manageable: the PM works QHIN by
    QHIN rather than row by row. Records whose QHIN cannot be resolved from a
    canonical edge are reported under `unresolved` with the reason — never
    placed under a plausible QHIN.
    """
    from app.tefca_registry.qhin_workload import WorkloadRefused, qhin_rollup

    try:
        return await qhin_rollup(db, intake_id, sample_id=sample_id,
                                 include_held=include_held)
    except WorkloadRefused as exc:
        raise HTTPException(404, str(exc))


@router.get("/deliveries/{intake_id}/qhins/{qhin_entity_id}",
            summary="One QHIN's review population, entity by entity")
async def qhin_population(
    intake_id: str,
    qhin_entity_id: uuid.UUID,
    sample_id: Optional[uuid.UUID] = Query(None),
    include_held: bool = Query(False),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """The list a Program Manager assigns from.

    Carries what an assignment decision needs — entity, level, parent, holder,
    state — and nothing more. Evidence and determinations belong to the
    analyst's workspace.
    """
    from app.tefca_registry.qhin_workload import qhin_detail

    return await qhin_detail(db, intake_id, qhin_entity_id,
                             sample_id=sample_id, include_held=include_held,
                             limit=limit, offset=offset)


@router.get("/deliveries/{intake_id}/workload",
            summary="Analyst workload across a delivery")
async def analyst_workload(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Case counts per holder. Uses the existing workload query unchanged.

    VIEWER, matching `/priority-requests/workload`, which answers the same
    question for the priority queue at the same level. A read gated above
    viewer is invisible to roles the product can actually assign — the shape
    `test_no_tefca_read_endpoint_sits_above_the_viewer_floor` exists to
    prevent — and this returns operational counts keyed by user id, not entity
    data or PII.
    """
    from app.tefca_registry.case_assignment import workload_by_analyst

    return await workload_by_analyst(db, intake_id=intake_id)


# ── assignment ───────────────────────────────────────────────────────────────

class DistributionRequest(BaseModel):
    review_ids: List[str] = Field(
        default_factory=list, max_length=2000,
        description="The cases to distribute. Cases already held by someone "
                    "are skipped, not reassigned.")
    analyst_user_ids: List[uuid.UUID] = Field(
        default_factory=list, max_length=200,
        description="Who to distribute across, round-robin.")
    preview: bool = Field(
        True, description="Return the plan without applying it. Default true — "
                          "a bulk assignment should be seen before it happens.")


@router.post("/distribute", summary="Distribute unassigned cases across analysts")
async def distribute(
    body: DistributionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("manager")),
):
    """Even workload distribution. MAKES NO COMPLIANCE DECISION.

    It decides who LOOKS at a case, never what the case concludes. There is no
    scoring, no weighting by classification and no routing by finding — any of
    those would be the system forming a view about an entity before an analyst
    has, and that view would then be the one the analyst argues against.

    Defaults to `preview: true`. Applying goes through the existing
    `case_assignment.assign` for every single case, so each one carries the same
    authorisation check, refusal rules and audit row a manual assignment does.
    """
    from app.tefca_registry.qhin_workload import (WorkloadRefused,
                                                  apply_distribution,
                                                  plan_distribution)

    if not body.analyst_user_ids:
        raise HTTPException(422, "No analysts were given to distribute across.")
    if not body.review_ids:
        raise HTTPException(422, "No cases were given to distribute.")

    try:
        plan = await plan_distribution(db, body.review_ids,
                                       body.analyst_user_ids)
    except WorkloadRefused as exc:
        raise HTTPException(422, str(exc))

    if body.preview:
        return {
            "preview": True,
            "planned": len(plan),
            "skipped_already_held": len(body.review_ids) - len(plan),
            "assignments": [{"review_id": r, "assigned_to": str(u)}
                            for r, u in plan],
            "note": ("Nothing has been assigned. Re-send with preview=false to "
                     "apply. Cases already held by someone are skipped rather "
                     "than reassigned — taking a case out of an analyst's "
                     "hands mid-review is a separate, individually audited "
                     "act."),
        }

    result = await apply_distribution(db, plan, user=user,
                                      ip_address=_client_ip(request))
    result["preview"] = False
    result["skipped_already_held"] = len(body.review_ids) - len(plan)
    return result


# ── analyst verification workspace ───────────────────────────────────────────

@router.get("/reviews/{review_id}/workspace",
            summary="Everything one analyst needs for one case")
async def verification_workspace(
    review_id: str,
    include_raw: bool = Query(
        False, description="Include the literal delivered line from Area 1."),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """The seven sections of the verification workspace, in one response.

    SOURCE is read-only and is never mixed with curated values. Verification
    results are observations in the canonical vocabulary — a source that did not
    answer stays SOURCE_UNAVAILABLE and is never rendered as NO_MATCH. USPS and
    website evidence sit beside the ONC values and never replace them.

    VIEWER, WITH IDENTIFIERS MASKED BELOW THE PII FLOOR
    ──────────────────────────────────────────────────
    Gating this read above viewer would make it invisible to a role the product
    can assign, which is the defect
    `test_no_tefca_read_endpoint_sits_above_the_viewer_floor` exists to catch.
    But the workspace carries NPI and address, and the viewer role exists in
    part so that a viewer sees no PII anywhere (LOGIN-013).

    Both hold at once by masking rather than denying — exactly what every other
    viewer-level TEFCA read already does. The EXISTING `_can_see_pii` and
    `_mask_identifier` are used, not a second implementation, and the response
    carries `pii_masked` so a caller can tell a masked value from an absent one.

    Holding the case is required to ACT on it. That is enforced where the acts
    are, by `case_assignment.require_owner`, not by hiding the case here.
    """
    from app.tefca_registry.workspace import WorkspaceRefused, workspace

    try:
        result = await workspace(db, review_id, include_raw=include_raw)
    except WorkspaceRefused as exc:
        raise HTTPException(404, str(exc))
    return _mask_workspace(result, user)


# ── my reviews ───────────────────────────────────────────────────────────────

@router.get("/my-reviews", summary="The analyst's own queue, with counts")
async def my_reviews(
    intake_id: Optional[str] = Query(None),
    qhin_entity_id: Optional[uuid.UUID] = Query(None),
    state: Optional[str] = Query(
        None, description="AVAILABLE|CLAIMED|SUBMITTED_FOR_QA|RETURNED|"
                          "ESCALATED|APPROVED"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """What this analyst holds, summarised the way the screen shows it.

    VIEWER, because the answer is scoped to the CALLER: `my_work` filters on the
    caller's own user id, so a viewer who holds no cases receives an empty
    queue rather than someone else's. Gating it higher would hide a role's own
    empty queue from it and trip the viewer-floor rule for no protection.

    Built on the existing `case_assignment.my_work`, which already carries each
    case's derived state, so "what do I hold" has ONE answer and this route does
    not recompute it. The counts are taken from the same rows the list is built
    from — a screen whose header disagrees with its list is worse than one with
    no header — and they are counted BEFORE the status filter is applied, so
    filtering to "Returned" does not make every other count read zero.
    """
    from app.tefca_registry.case_assignment import (APPROVED, CLAIMED,
                                                    ESCALATED, RETURNED,
                                                    SUBMITTED_FOR_QA, my_work)

    items = await my_work(db, user=user, intake_id=intake_id, limit=limit)

    counts = {
        "assigned": len(items),
        "not_started": sum(1 for c in items if c.get("state") == CLAIMED),
        "submitted_to_qa": sum(1 for c in items
                               if c.get("state") == SUBMITTED_FOR_QA),
        "returned": sum(1 for c in items if c.get("state") == RETURNED),
        "escalated": sum(1 for c in items if c.get("state") == ESCALATED),
        "completed": sum(1 for c in items if c.get("state") == APPROVED),
    }
    # "In progress" is not a stored state and is not invented as one. A case the
    # analyst holds is either untouched or has a determination on it, and that
    # difference lives in the decision events. It is reported as the remainder
    # rather than as a status the system does not actually keep.
    counts["in_progress"] = max(
        counts["assigned"] - counts["not_started"] - counts["submitted_to_qa"]
        - counts["returned"] - counts["escalated"] - counts["completed"], 0)

    if qhin_entity_id is not None:
        keep = await _entities_under_qhin(
            db, qhin_entity_id,
            [c["entity_id"] for c in items if c.get("entity_id")])
        items = [c for c in items if c.get("entity_id") in keep]
    if state:
        items = [c for c in items if c.get("state") == state]

    return {
        "counts": counts,
        "items": items,
        "count": len(items),
        "filters_applied": {"intake_id": intake_id, "state": state,
                            "qhin_entity_id": (str(qhin_entity_id)
                                               if qhin_entity_id else None)},
        "note": ("Counts describe everything this analyst holds and are not "
                 "narrowed by the filters; the list is. No Government upload "
                 "controls and no population editing appear on this surface — "
                 "an analyst reviews the population, they do not establish it."),
    }


async def _entities_under_qhin(db, qhin_entity_id, entity_ids: List[str]):
    """Which of these entities sit under this QHIN, by canonical edge only."""
    from sqlalchemy import select

    from app.tefca_registry import models as reg

    if not entity_ids:
        return set()
    rows = (await db.execute(
        select(reg.TefcaEntityRelationship.child_entity_id)
        .where(reg.TefcaEntityRelationship.child_entity_id.in_(entity_ids),
               reg.TefcaEntityRelationship.parent_entity_id == qhin_entity_id,
               reg.TefcaEntityRelationship.relationship_type
               == "managed_by_qhin",
               reg.TefcaEntityRelationship.status == "active"))).all()
    return {str(child) for (child,) in rows}


# ── PII masking for the workspace ────────────────────────────────────────────

#: Delivered field names that carry a personal or organisational identifier.
#: Matched case- and separator-insensitively against the delivered header, so a
#: delivery spelling it `NPI`, `npi` or `provider_npi` is caught either way.
_IDENTIFIER_FIELDS = ("npi", "tefcaid", "hcid", "uei", "ein", "tin", "ssn",
                      "medicareid", "ccn", "providernpi", "organizationnpi")


def _mask_workspace(result: Dict[str, Any], user) -> Dict[str, Any]:
    """Mask identifiers for a caller below the PII floor.

    REUSES `_can_see_pii` AND `_mask_identifier` FROM `app/Tefca/routes.py`.
    Those already define what "may see PII" means and what a masked value looks
    like for every other TEFCA read; a second definition here would be a second
    answer, and the two would diverge the first time either was tuned.

    Masking rather than denying, for the reason the module docstring gives: a
    read gated above viewer is invisible to a role the product can assign. A
    masked value is still evidence that the field is populated, which is what a
    viewer legitimately needs to know.

    `pii_masked` is set on the response either way — True or False — because a
    caller must be able to tell "redacted for your role" from "we do not have
    it". Those are different facts and a blank would collapse them.
    """
    from app.Tefca.routes import _can_see_pii, _mask_identifier

    if _can_see_pii(user):
        result["pii_masked"] = False
        return result

    source = result.get("source") or {}
    if isinstance(source.get("identifiers"), dict):
        source["identifiers"] = {k: _mask_identifier(v)
                                 for k, v in source["identifiers"].items()}
    if isinstance(source.get("delivered_values"), dict):
        source["delivered_values"] = {
            key: (_mask_identifier(value)
                  if _is_identifier(key) and isinstance(value, str) else value)
            for key, value in source["delivered_values"].items()}
    # The literal delivered line cannot be partially masked without corrupting
    # it, so it is withheld entirely rather than handed over altered. A doctored
    # copy of Area 1 evidence would be worse than no copy.
    if "raw_line" in source:
        source["raw_line"] = None
        source["raw_line_withheld"] = (
            "The raw delivered line contains unmasked identifiers and is "
            "available to Reviewer and above.")

    curated = result.get("curated") or {}
    values = curated.get("curated_values")
    if isinstance(values, dict):
        curated["curated_values"] = {
            key: (_mask_identifier(value)
                  if _is_identifier(key) and isinstance(value, str) else value)
            for key, value in values.items()}
    if isinstance(curated.get("corrections"), list):
        for correction in curated["corrections"]:
            if _is_identifier(correction.get("column") or ""):
                correction["source_value"] = _mask_identifier(
                    correction.get("source_value"))
                correction["curated_value"] = _mask_identifier(
                    correction.get("curated_value"))

    result["pii_masked"] = True
    return result


def _is_identifier(field_name: str) -> bool:
    normalized = str(field_name or "").lower().replace("_", "").replace("-", "")
    return normalized in _IDENTIFIER_FIELDS
