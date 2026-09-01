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


#: Variables App Service sets on every instance, which no developer machine has.
#: Their presence is how this process knows it is DEPLOYED rather than running on
#: someone's laptop.
PLATFORM_MARKERS = ("WEBSITE_SITE_NAME", "WEBSITE_INSTANCE_ID")

#: Environments that are explicitly not production. Staging is deliberately NOT
#: here: a deployed staging host must not create tables at startup either.
NON_PRODUCTION = {"development", "dev", "test", "testing", "local"}


def _is_deployed() -> bool:
    """Is this process running on the hosting platform rather than a laptop?"""
    return any(os.getenv(marker) for marker in PLATFORM_MARKERS)


def _is_production() -> bool:
    """Whether this process must be treated as production.

    THE GAP THIS CLOSES
    ───────────────────
    This used to answer the narrow question "does ENVIRONMENT say production?",
    so an UNSET variable meant not-production, which meant startup schema
    mutation was ALLOWED. Unset is precisely the state a restored configuration,
    a new deployment slot or a mis-copied app setting begins in — so the most
    likely way to lose the variable was also the way to grant the capability it
    guards. On production that would let a container restart create the Area 1
    tables, owned by the connecting role, which makes immutability inert on the
    tables that hold Government data. That is the defect this module exists to
    prevent, reached by a different door.

    An explicit value is believed in both directions. Silence is read in the
    light of WHERE the process is running: on a developer machine an unset
    environment is ordinary and the startup repair stays available, which is the
    convenience this guard deliberately preserved. On a deployed host it is a
    configuration that has gone missing, and the safe reading of a missing
    production marker is that this IS production.

    An unrecognised value is treated the same way as unset — a typo must never
    grant a capability.
    """
    raw = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").strip().lower()
    if raw in {"production", "prod"}:
        return True
    if raw in NON_PRODUCTION:
        return False
    return _is_deployed()


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
    if (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").strip():
        where = "production" if _is_production() else "this environment"
    else:
        where = ("this deployed host, whose ENVIRONMENT is not set and which is "
                 "therefore treated as production")
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
