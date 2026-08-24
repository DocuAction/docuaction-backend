"""
Learning Center API. CORE — serves any registered programme.

WHY THIS IS PROGRAM-AGNOSTIC
────────────────────────────
Nothing in this file imports TEFCA. It resolves a programme from the path and
serves whatever that programme registered. A second programme gets navigation,
search, role filtering, content classification and contextual help by
registering a `LearningRegistry` — not by editing this file, and not by
importing anything from the first programme.

ROLE FILTERING
──────────────
The caller does not choose their role; it is read from the authenticated user
and mapped onto the guidance ladder. Search applies the same filter as
navigation, so search cannot surface content the sidebar hides — the usual way
role-scoped content leaks.

WHY GUIDANCE IS AUTHENTICATED AT ALL
────────────────────────────────────
The lessons quote development counts and describe internal controls. They are
not secret, but they are not public either, and the floor is the same `viewer`
floor the rest of the review surface uses.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.learning.framework import PROGRAMS, LearningRegistry, Role
from app.core.security import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["Learning Center"])

#: Application role → guidance audience. Anything unrecognised gets the
#: least-privileged view rather than the fullest one.
_ROLE_MAP = {
    "admin": Role.ADMIN,
    "program_manager": Role.PROGRAM_MANAGER,
    "qalead": Role.QA,
    "qa_lead": Role.QA,
    "reviewer": Role.ANALYST,
    "analyst": Role.ANALYST,
}


def _role_of(user: Any) -> Role:
    raw = (getattr(user, "role", None) or "").strip().lower()
    return _ROLE_MAP.get(raw, Role.ANY)


def _registry(program: str) -> LearningRegistry:
    registry = PROGRAMS.get(program)
    if registry is None:
        raise HTTPException(
            404, f"No learning content is registered for programme {program!r}. "
                 f"Registered: {', '.join(PROGRAMS.keys()) or 'none'}")
    return registry


@router.get("/programs", summary="Programmes with learning content")
async def list_programs(user=Depends(require_role("viewer"))):
    return {"programs": [{
        "program": key,
        "title": PROGRAMS.get(key).program_title,
        "last_updated": PROGRAMS.get(key).last_updated,
        "modules": len(PROGRAMS.get(key).modules),
    } for key in PROGRAMS.keys()]}


@router.get("/{program}", summary="A programme's learning content for this user")
async def program_content(program: str, user=Depends(require_role("viewer"))):
    """Navigation, modules, glossary and help, filtered to the caller's role."""
    return _registry(program).to_dict(role=_role_of(user))


@router.get("/{program}/search", summary="Search a programme's content")
async def search(program: str,
                 q: str = Query(..., min_length=2, max_length=120),
                 limit: int = Query(25, ge=1, le=100),
                 user=Depends(require_role("viewer"))):
    registry = _registry(program)
    results = registry.search(q, role=_role_of(user), limit=limit)
    return {"program": registry.program, "query": q,
            "count": len(results), "results": results}


@router.get("/{program}/navigation", summary="Sidebar navigation")
async def navigation(program: str, user=Depends(require_role("viewer"))):
    registry = _registry(program)
    role = _role_of(user)
    return {
        "program": registry.program,
        "sections": list(registry.navigation),
        "modules": [{"slug": m.slug, "title": m.title, "objective": m.objective,
                     "lessons": [{"slug": l.slug, "title": l.title}
                                 for l in m.lessons]}
                    for m in registry.modules_for(role)],
        "role": role.value,
        "last_updated": registry.last_updated,
    }


@router.get("/{program}/modules/{slug}", summary="One module")
async def module(program: str, slug: str,
                 include_answers: bool = Query(
                     False, description="Knowledge-check answers"),
                 user=Depends(require_role("viewer"))):
    registry = _registry(program)
    found = registry.module(slug)
    if found is None:
        raise HTTPException(404, f"No module {slug!r} in {registry.program}")
    role = _role_of(user)
    if not (Role.ANY in found.audience or role in found.audience):
        # 404 rather than 403: whether a module exists is itself scoped, and a
        # 403 confirms it does.
        raise HTTPException(404, f"No module {slug!r} in {registry.program}")
    return found.to_dict(include_answers=include_answers)


@router.get("/{program}/modules/{slug}/{lesson_slug}", summary="One lesson")
async def lesson(program: str, slug: str, lesson_slug: str,
                 user=Depends(require_role("viewer"))):
    """Deep-link target for contextual help."""
    registry = _registry(program)
    found = registry.module(slug)
    role = _role_of(user)
    if found is None or not (Role.ANY in found.audience or role in found.audience):
        raise HTTPException(404, f"No module {slug!r} in {registry.program}")
    for item in found.lessons:
        if item.slug == lesson_slug:
            return {"program": registry.program, "module_slug": found.slug,
                    "module_title": found.title, **item.to_dict()}
    raise HTTPException(404, f"No lesson {lesson_slug!r} in module {slug!r}")


@router.get("/{program}/help/{key:path}", summary="Contextual help for one screen")
async def contextual_help(program: str, key: str,
                          user=Depends(require_role("viewer"))):
    """Help for a specific surface, with a deep link to the relevant lesson.

    Keys are dotted (`evidence.address_conflict`), hence `:path`.
    """
    registry = _registry(program)
    topic = registry.help_for(key)
    if topic is None:
        raise HTTPException(
            404, f"No contextual help for {key!r}. "
                 f"Available: {', '.join(registry.help_keys())}")
    role = _role_of(user)
    if not (Role.ANY in topic.audience or role in topic.audience):
        raise HTTPException(404, f"No contextual help for {key!r}")
    payload: Dict[str, Any] = topic.to_dict()
    payload["program"] = registry.program
    if topic.module_slug:
        payload["deep_link_url"] = (
            f"/api/learning/{registry.program}/modules/{topic.module_slug}"
            + (f"/{topic.lesson_slug}" if topic.lesson_slug else ""))
    return payload


@router.get("/{program}/glossary", summary="Programme glossary")
async def glossary(program: str, term: Optional[str] = Query(None),
                   user=Depends(require_role("viewer"))):
    registry = _registry(program)
    if term:
        found = registry.glossary.get(term)
        if found is None:
            raise HTTPException(404, f"{term!r} is not in the glossary")
        return found.to_dict()
    return registry.glossary.to_dict()


@router.get("/{program}/prohibited", summary="Conclusions operators must not draw")
async def prohibited(program: str, user=Depends(require_role("viewer"))):
    """The consolidated list.

    Worth serving on its own: the conclusions people reach wrongly are exactly
    the ones nobody wrote down as forbidden, and a reviewer wants to read them
    together rather than hunt through lessons.
    """
    registry = _registry(program)
    items = [p.to_dict() for p in registry.all_prohibited()]
    return {"program": registry.program, "count": len(items),
            "prohibited_conclusions": items}
