"""
TEFCA registry — Phase 1B.

The normalized TEFCA entity registry: a fresh, more granular data model
(entities + identifiers + relationships + versions + endpoints + verification +
findings + import batches + audit log).

These tables use the ``tefca_reg_*`` / ``tefca_entity_*`` / ``tefca_verification_*``
naming and are SEPARATE from the legacy ``app.Tefca`` tables (which are left
untouched). The main entity table is ``tefca_reg_entities`` to avoid colliding
with the legacy ``tefca_entities``.

They live on the shared application Base (``app.core.database.Base``). Importing
this package registers all 10 tables (side effect).
"""

from app.tefca_registry import models  # noqa: F401  (registration side effect)
