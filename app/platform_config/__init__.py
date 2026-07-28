"""
Platform configuration layer — Phase 1A.

The 12 ``platform_*`` configuration tables that form the foundation of the
multi-tenant platform (tenants, agencies, programs, modules, workspaces, pages,
features, data sources, themes, jurisdictions, import formats, identifier types).

These are created BEFORE any TEFCA tables. They live on the shared application
Base (``app.core.database.Base``) so they register with the same metadata that
``main.py``'s startup ``create_all`` and the Alembic platform migration operate
on. Importing this package registers all 12 tables (side effect).
"""

from app.platform_config import models  # noqa: F401  (registration side effect)
