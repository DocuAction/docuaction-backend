"""Create (or recreate) a disposable local benchmark database and migrate it
to head. Local use only -- refuses anything that is not 127.0.0.1/localhost.

Usage:
    python scripts/perf/fresh_db.py test_lp_fullscale [--drop]

Prints the DATABASE_URL to export for the benchmark.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys

ADMIN_URL = "postgresql://docuaction_owner:docuaction_owner@127.0.0.1:5499/postgres"
APP_URL_TEMPLATE = "postgresql+asyncpg://docuaction_owner:docuaction_owner@127.0.0.1:5499/{db}"
PROTECTED = {"test_iqvia_realfile", "test_report_full_scale", "postgres"}


async def _create(name: str, drop: bool) -> None:
    import asyncpg

    if name in PROTECTED:
        raise SystemExit(f"refusing to touch protected database {name}")
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        exists = await conn.fetchval("select 1 from pg_database where datname=$1", name)
        if exists and drop:
            await conn.execute(f'drop database "{name}"')
            exists = None
        if not exists:
            await conn.execute(f'create database "{name}"')
            print(f"created {name}")
        else:
            print(f"{name} already exists (not dropped)")
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("--drop", action="store_true")
    args = p.parse_args()
    asyncio.run(_create(args.name, args.drop))
    url = APP_URL_TEMPLATE.format(db=args.name)
    env = dict(os.environ, DATABASE_URL=url)
    env.setdefault("SECRET_KEY", "b" * 64)
    env.setdefault("ALLOWED_HOSTS", "*")
    env.setdefault("DB_APP_ROLE", "docuaction_app")
    rc = subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
    if rc != 0:
        raise SystemExit(f"alembic upgrade head failed with {rc}")
    print(f"DATABASE_URL={url}")


if __name__ == "__main__":
    main()
