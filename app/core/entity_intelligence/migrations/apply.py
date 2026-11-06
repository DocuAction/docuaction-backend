"""Create the entity-intelligence tables on an ISOLATED database.

Refuses any URL that is not clearly local unless --i-know-this-is-isolated is
passed, so it cannot be pointed at the shared QA database by accident.

    python -m app.core.entity_intelligence.migrations.apply --database-url sqlite:///ei_local.db
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import urlparse

from sqlalchemy import create_engine

from app.core.entity_intelligence.models import EI_TABLES, EntityIntelligenceBase

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def is_isolated(url: str) -> bool:
    if url.startswith("sqlite"):
        return True
    host = (urlparse(url.replace("+asyncpg", "").replace("+psycopg", "")).hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".local")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", required=True)
    p.add_argument("--i-know-this-is-isolated", action="store_true")
    a = p.parse_args(argv)
    if not is_isolated(a.database_url) and not a.i_know_this_is_isolated:
        print("refusing: URL is not local. Pass --i-know-this-is-isolated only for an ephemeral/CI database.")
        return 2
    url = a.database_url.replace("+asyncpg", "")
    engine = create_engine(url)
    EntityIntelligenceBase.metadata.create_all(engine)
    print("created:", ", ".join(EI_TABLES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
