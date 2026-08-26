"""
The ingestion-run lifecycle grant must stay column-scoped.

WHY IT EXISTS
Ingesting the real August 21 ONC delivery into dev blocked here: the quality
engine evaluated all 23,566 records and produced 36,916 issues, then the whole
transaction rolled back on `permission denied for table rce_ingestion_runs`.
The run row is INSERTed as RUNNING and must later record that it finished; the
table is Area 1, so the app role had SELECT and INSERT only.

WHAT THESE TESTS PROTECT
That the remedy stayed narrow. The risk in fixing a permission error is
over-granting — reaching for table-level UPDATE because it makes the error go
away. These assert the grant covers exactly the four lifecycle columns the code
actually mutates, and that the provenance columns stay unwritable.

The distinction matters: `run_status` and the counts describe what a run DID.
`source_intake_id`, `rule_set_version`, `rule_config_hash`, `field_map_version`,
`started_at` and `executed_by` describe what it WAS. A run that could rewrite
the second set could make its own findings unfalsifiable, so those are written
once at INSERT and never again.

No Government data is touched here; the source record content lives in a
different table entirely.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

pytestmark = pytest.mark.regression

MIGRATION = (pathlib.Path(__file__).resolve().parents[1]
             / "alembic" / "versions" / "20260830_run_lifecycle_grant.py")

#: Columns that must remain unwritable after INSERT.
PROVENANCE_COLUMNS = (
    "source_intake_id", "rule_set_version", "rule_config_hash",
    "field_map_version", "started_at", "executed_by", "id",
)


def _migration_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_lifecycle_grant", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_grant_covers_exactly_the_columns_the_code_mutates():
    """The grant and the quality engine must agree, or one of them is wrong."""
    from app.tefca_registry.rce import quality_engine

    import textwrap

    # dedent the whole function and parse it intact. Slicing off the `def` line
    # breaks on a multi-line signature, which this one has.
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(quality_engine.run_quality_engine)))

    mutated = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "run"):
                    mutated.add(target.attr)

    granted = set(_migration_module().LIFECYCLE_COLUMNS)
    assert mutated == granted, (
        f"quality engine mutates {sorted(mutated)} but the migration grants "
        f"{sorted(granted)} — they must match exactly")


def test_no_provenance_column_is_granted():
    """What a run WAS must not be rewritable by the run itself."""
    granted = set(_migration_module().LIFECYCLE_COLUMNS)
    overlap = granted & set(PROVENANCE_COLUMNS)
    assert not overlap, f"provenance columns granted UPDATE: {sorted(overlap)}"


def test_migration_never_grants_table_wide_or_destructive_privileges():
    """The obvious wrong fix, pinned shut."""
    text = MIGRATION.read_text(encoding="utf-8")
    upgrade = text[text.index("def upgrade"):text.index("def downgrade")]

    assert "GRANT UPDATE (" in upgrade, "the grant must be column-scoped"
    for forbidden in ("GRANT UPDATE ON", "GRANT ALL", "GRANT DELETE",
                      "GRANT TRUNCATE", "OWNER TO", "GRANT docuaction_owner"):
        assert forbidden not in upgrade, f"migration contains {forbidden!r}"


def test_downgrade_revokes_exactly_what_upgrade_granted():
    text = MIGRATION.read_text(encoding="utf-8")
    downgrade = text[text.index("def downgrade"):]
    assert "REVOKE UPDATE (" in downgrade
    assert "LIFECYCLE_COLUMNS" in downgrade, (
        "downgrade must revoke the same column list, not a hand-copied one")


def test_migration_fails_closed_when_the_role_owns_the_table():
    """An owner can already update everything; granting it proves nothing.

    That exact failure — a correct-looking ACL enforcing nothing — was measured
    on this codebase before, which is why 20260828 fails closed and why this
    one does too.
    """
    mod = _migration_module()
    assert hasattr(mod, "RunLifecycleTargetError")
    src = inspect.getsource(mod._role)
    assert "RunLifecycleTargetError" in src
    assert "_owner_of(TABLE) == role" in src


def test_it_chains_from_the_current_head():
    mod = _migration_module()
    assert mod.down_revision == "20260829_report_artifacts"
    assert mod.revision == "20260830_run_lifecycle"


def test_government_source_tables_are_untouched_by_this_migration():
    """The grant must not reach the tables that hold delivered ONC content."""
    text = MIGRATION.read_text(encoding="utf-8")
    upgrade = text[text.index("def upgrade"):text.index("def downgrade")]
    for table in ("rce_source_records", "rce_source_intakes",
                  "rce_rule_execution_history"):
        assert table not in upgrade, (
            f"{table} holds Government source content and must not appear in "
            f"this grant")
