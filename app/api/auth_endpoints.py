"""
Auth Extra Endpoints — Token Refresh + SAML Config
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import refresh_access_token, SAML_CONFIG

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh")
async def refresh_token(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange refresh token for new access + refresh tokens (rotation)."""
    tokens = await refresh_access_token(data.refresh_token, db)
    return tokens


@router.get("/saml/config")
async def get_saml_config():
    """SAML/SSO config placeholder — Enterprise tier only."""
    return SAML_CONFIG
