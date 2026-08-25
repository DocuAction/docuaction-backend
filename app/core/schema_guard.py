"""
Fail-closed control on schema mutation at application startup.

WHAT THIS PREVENTS
`app/main.py` runs `Base.metadata.create_all()` plus 27 `ALTER TABLE ... ADD
COLUMN IF NOT EXISTS` statements every time the application boots. On a database
the ORM already matches, that is a harmless no-op — which is why it has survived
this long. On a database that is BEHIND the model, it silently creates whatever
is missing.

That second case is the problem, and it is not hypothetical. The production
database is missing fifteen tables, including all seven `rce_*` tables. Those are
the Area 1 immutability tables. In PostgreSQL the role that creates a table owns
it, and an owner can always UPDATE and DELETE its own rows regardless of any
grant. Production currently connects as the server administrator, so a container
start would create the Area 1 tables owned by an administrator — making
immutability inert from the moment those tables came into existence, on the
tables that will later hold Government data.

That exact defect was already found and fixed once, on dev, where the ACL looked
correct and UPDATE succeeded anyway because `pgadmin` owned the tables. Letting a
container start recreate it in production would be repeating a known mistake in
the one environment where it matters.

WHY A FLAG RATHER THAN DELETING THE CODE
The startup repair is genuinely useful in development, where the schema drifts
constantly and a missing column makes every `User` query 500. Removing it would
trade a production risk for a daily development obstacle, and someone would put
it back. Gating it keeps the convenience where it is wanted and removes the
capability where it is dangerous.

In production, schema changes must arrive through an explicitly authorized
Alembic run whose statements were reviewed before they executed — not as a side
effect of a process restart nobody scheduled.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("docuaction.schema_guard")

#: Environment variable controlling startup schema mutation.
STARTUP_SCHEMA_FLAG = "STARTUP_SCHEMA_MUTATION_ENABLED"

_TRUTHY = {"1", "true", "yes", "on", "enabled"}
_FALSY = {"0", "false", "no", "off", "disabled"}


def _is_production() -> bool:
    return (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").strip().lower() in {
        "production", "prod"}


def schema_mutation_allowed() -> bool:
    """Whether startup may create or alter database objects.

    Unset means DENIED in production, for the same reason the PPEF bulk gate
    works that way: unset is the state a fresh deployment or a restored
    configuration begins in, so absence must not be the permissive answer. An
    unrecognised value is treated as unset — a typo must never grant a
    capability.
    """
    raw = (os.getenv(STARTUP_SCHEMA_FLAG) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return not _is_production()


def schema_mutation_refusal_reason() -> str:
    """Why startup will not touch the schema, in operator terms."""
    where = "production" if _is_production() else "this environment"
    return (
        f"startup schema mutation is DISABLED in {where}: {STARTUP_SCHEMA_FLAG} is "
        f"not enabled. create_all() and the startup ALTER TABLE statements were "
        f"skipped. Schema changes must be applied by an authorized Alembic run "
        f"before the application starts — a process restart must never be able to "
        f"create a table, because the creating role owns it and an owner can "
        f"always modify its own rows."
    )


def log_schema_mutation_skipped() -> None:
    logger.warning("%s", schema_mutation_refusal_reason())
