"""
TEFCA-specific operational transparency endpoints.

The Learning Center API itself is programme-agnostic and lives in
`app/core/learning/routes.py`. What is here is the part that is genuinely about
this contract: the status of the COR methodology decisions, and the guidance for
the four Government discrepancy categories.

WRITTEN FOR OPERATORS, NOT ENGINEERS
────────────────────────────────────
No table names, class names, migration ids or internal layer vocabulary appear
in these payloads. A programme manager reading the methodology status should see
what is undecided and what it affects, not the schema it is stored in. A test
asserts the absence.

NOTHING HERE DECIDES ANYTHING
─────────────────────────────
D1-D9 are reported as they stand. Every one is currently open, and none is
presented as decided, because no written COR response exists for any of them.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/methodology", tags=["TEFCA Methodology"])


@router.get("/status", summary="Status of the COR methodology decisions (D1-D9)")
async def methodology_status(user=Depends(require_role("viewer"))):
    """What is decided, what is not, and what each unresolved item affects."""
    from app.Tefca.learning_methodology import decision_status

    return decision_status()


@router.get("/categories",
            summary="The four Government discrepancy categories, explained")
async def discrepancy_categories(user=Depends(require_role("viewer"))):
    """Contractual meaning, evidence, AGT implementation and what to verify.

    The labels come from the same constants the reports use, so a category can
    never be worded one way in a lesson and another way in a deliverable.
    """
    from app.Tefca.learning_methodology import (SOLICITATION, TAXONOMY_CLAUSE,
                                                category_guidance)

    return {
        "categories": category_guidance(),
        "label_authority": {
            "classification": "GOVERNMENT_REQUIREMENT",
            "source": SOLICITATION,
            "note": ("The four category names are the Government's, quoted from "
                     "the solicitation, where the same sentence appears three "
                     "times."),
        },
        "mapping_authority": {
            "classification": "AGT_IMPLEMENTATION",
            "source": TAXONOMY_CLAUSE,
            "note": ("The rules deciding which category applies are AGT's, "
                     "submitted under D2 and awaiting COR acceptance. B1-B4 is "
                     "AGT shorthand and is not a TEFCA, ONC, ASTP, RCE or "
                     "Sequoia classification."),
        },
    }


@router.get("/categories/{category}", summary="One discrepancy category")
async def discrepancy_category(category: str,
                               user=Depends(require_role("viewer"))):
    from app.Tefca.learning_methodology import category_guidance

    wanted = category.strip().lower()
    for entry in category_guidance():
        if entry["category"] == wanted or entry["agt_shorthand"].lower() == wanted:
            return entry
    raise HTTPException(
        404, f"{category!r} is not one of the four Government discrepancy "
             f"categories.")
