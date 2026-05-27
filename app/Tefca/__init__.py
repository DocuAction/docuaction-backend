"""
DocuAction TEFCA Review Protocol Module
AGT — ONC TEFCA Review Protocol

Usage in main.py (already done via safe_load):
    safe_load("app.Tefca", "tefca-review-protocol")
"""

from .routes import tefca_router
from .connectors import SourceConnectorManager
from .validation_engine import ValidationEngine, EvidenceRecordGenerator
from .mock_data import ALL_MOCK_ENTITIES, MOCK_STATS

# safe_load expects mod.router — this exposes it
router = tefca_router

__all__ = [
    "router",
    "tefca_router",
    "SourceConnectorManager",
    "ValidationEngine",
    "EvidenceRecordGenerator",
    "ALL_MOCK_ENTITIES",
    "MOCK_STATS",
]

__version__ = "1.0.0"
__author__ = "Alliance Global Tech, Inc. (AGT)"
__description__ = "ONC TEFCA Review Protocol — QHIN Participant & Subparticipant Data Accuracy Review"
