"""Seed the synthetic issue-history cases A-E into a LOCAL database and dump the
REAL service responses (viewer and reviewer audiences) as JSON.

Usage (local throwaway database only; refuses any non-loopback host):
    DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5499/test_a6 \
    python scripts/dump_issue_history_cases.py <output_dir>
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def main(out_dir: str) -> None:
    from app.core.config import settings

    url = os.environ.get("DATABASE_URL", "")
    if "127.0.0.1" not in url and "localhost" not in url:
        raise SystemExit("refusing: DATABASE_URL is not a loopback database")
    settings.ENABLE_RECORD_CHECK_RESULTS = True
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import issue_history as svc
    from issue_history_cases_2026_10_08 import FEEDS, OIDS, SEEDERS

    os.makedirs(out_dir, exist_ok=True)
    plan = [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D1"), ("D", "D2"), ("E", "E")]
    async with async_session_maker() as db:
        for case in SEEDERS:
            await SEEDERS[case](db)
        for case, key in plan:
            for audience in ("viewer", "reviewer"):
                conf = SimpleNamespace(ISSUE_HISTORY_FEEDS_VIEWER=FEEDS[case],
                                       ISSUE_HISTORY_FEEDS_REVIEWER="")
                resp = await svc.get_issue_history(
                    db, OIDS[key], reviewer_or_above=(audience == "reviewer"),
                    settings=conf)
                name = f"case-{key.lower()}-{audience}.json"
                with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                    json.dump(resp, fh, indent=2, default=str)
                print(name, len(resp["deliveries"]), "deliveries")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
