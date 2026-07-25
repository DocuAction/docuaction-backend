"""
Pydantic request/response schemas for the TEFCA registry API (Phase 2A).

Read endpoints return plain dicts (FastAPI's jsonable_encoder handles UUID/date/
datetime), so only request bodies and a couple of light envelopes are modeled
here for documentation and validation.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class VerifyOptions(BaseModel):
    """Options for a verification run."""
    include_external: bool = Field(
        default=False,
        description="Run external authoritative-source checks (NPPES/LEIE/SAM/PECOS). "
                    "Off by default — seed identifiers are synthetic, so live sources "
                    "would false-flag every entity.",
    )
    trigger_type: str = Field(
        default="manual",
        description="manual | import | scheduled | re_verification",
    )


class BulkVerifyRequest(VerifyOptions):
    """Bulk verification across a filtered set (empty filter = all entities)."""
    entity_level: Optional[str] = Field(default=None, description="qhin|participant|sub_participant|child")
    limit: int = Field(default=1000, ge=1, le=5000)
