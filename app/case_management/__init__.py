"""
DocuAction AI — Case Management Module
AGT Case Management Platform

Add to main.py:
    safe_load("app.case_management", "case-management")
"""

from .routes import cm_router, router

__all__ = ["cm_router", "router"]

__version__ = "1.0.0"
__module__ = "Case Management"
__description__ = (
    "CCM/TCM/PCM billing-compliant note generation, care planning, "
    "discharge summaries, patient education, SDOH assessment, "
    "and government case management."
)
