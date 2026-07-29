"""Bulletin Intelligence — provider adapters (Phase 3).

First adapter in what will become the provider abstraction layer. Perigon is added
here because it was the only major provider with no implementation at all — the other
11 collectors already live in engine.py and are moved out in a later phase.
"""

from app.bulletin_intelligence.providers.perigon import (  # noqa: F401
    PERIGON_ENABLED,
    ingest_perigon,
    perigon_health,
)
