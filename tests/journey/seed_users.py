"""Seed synthetic users in the DISPOSABLE integration database and print fresh
tokens as JSON. Run from the integration worktree with its DATABASE_URL.
Re-run to mint new tokens (non-admin tokens last 15 minutes)."""
import asyncio
import json
import sys

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.security import create_token_pair, hash_password
from app.models.database import User

USERS = [
    ("owner", "hub-owner@synthetic-test.docuaction.invalid", "reviewer"),
    ("other", "hub-other@synthetic-test.docuaction.invalid", "reviewer"),
    ("qalead", "hub-qalead@synthetic-test.docuaction.invalid", "qalead"),
    ("pm", "hub-pm@synthetic-test.docuaction.invalid", "program_manager"),
    ("viewer", "hub-viewer@synthetic-test.docuaction.invalid", "viewer"),
]


async def main():
    out = {}
    async with async_session_maker() as db:
        for key, email, role in USERS:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            if user is None:
                user = User(email=email, password_hash=hash_password("Synthetic-Only-1!"),
                            full_name=f"Synthetic {key}", role=role, is_active=True,
                            is_verified=True, status="active", allowed_modules=[])
                db.add(user)
                await db.commit()
                await db.refresh(user)
            pair = create_token_pair(str(user.id), role, email)
            out[key] = {"token": pair["access_token"],
                        "user": {"id": str(user.id), "email": email, "role": role,
                                 "full_name": user.full_name, "allowed_modules": []}}
    json.dump(out, sys.stdout)


asyncio.run(main())
