"""The Alembic chain must stay drift-safe.

The database this project runs against was built partly by migrations and partly
by `app/main.py` startup's `Base.metadata.create_all()`. Any revision that
assumes an empty schema fails there, and a revision that fails takes the rest of
the chain with it. These tests pin the properties that keep that from happening.

They are static: they read the revision scripts rather than executing them, so
they run without a database and catch a reintroduced bare `op.create_table()` in
review rather than in a deployment.
"""
import ast
import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Revisions written before the drift was understood, plus every revision added
# since. Each one is reachable on a database whose schema create_all() already
# built, so each one has to tolerate finding its objects already there.
GUARDED_REVISIONS = [
    "20260817_audit_log_fields.py",
    "20260819_tefca_dimension_evidence.py",
    "20260819_ppef_snapshots.py",
    "20260820_ppef_ingest_jobs.py",
    "20260822_rce_pipeline.py",
    "20260823_vocabulary_version.py",
    "20260824_evidence_provenance.py",
    "20260825_qa_decision_events.py",
    "20260826_area1_mutation_audit.py",
    "20260827_startup_table_coverage.py",
    "20260828_area1_privilege_correction.py",
]

# DDL that creates something. Calling these unconditionally is the defect.
CREATING_OPS = {"create_table", "create_index", "add_column"}


def _source(filename: str) -> str:
    return (VERSIONS / filename).read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Strip the module docstring and comment lines.

    These revisions explain the defects they fix, quoting the broken SQL. A test
    that greps the raw file would match the explanation and fail on prose.
    """
    tree = ast.parse(text)
    lines = text.splitlines()
    doc = tree.body[0] if tree.body else None
    if (isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant)
            and isinstance(doc.value.value, str)):
        lines = lines[doc.end_lineno:]
    return "\n".join(l for l in lines if not l.lstrip().startswith("#"))


def _tree(filename: str) -> ast.Module:
    return ast.parse(_source(filename))


def _function(filename: str, name: str) -> ast.FunctionDef:
    for node in _tree(filename).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{filename} has no {name}()")


def _load(filename: str):
    """Import a revision module so its constants can be compared by value."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rev_" + filename.replace(".", "_"), VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed_sql(filename: str):
    """Every string this revision hands to op.execute() or sa.text().

    Scanning all string constants would match the prose: these revisions explain
    the SQL they replaced, and one of them logs a warning containing the word
    INSERT. What matters is only what actually reaches the database.
    """
    statements = []
    for node in ast.walk(_tree(filename)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None)
        if name not in ("execute", "text"):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                statements.append(argument.value)
            elif isinstance(argument, ast.JoinedStr):     # f-string
                statements.append("".join(
                    part.value for part in argument.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)))
    return statements


def _assert_no_dml(filename: str) -> None:
    """A structural revision must not send an INSERT, UPDATE or DELETE."""
    for statement in _executed_sql(filename):
        assert not re.search(r"\b(INSERT INTO|DELETE FROM|UPDATE \w+ SET)\b",
                             statement.upper()), (
            f"{filename} executes a DML statement: {statement[:90]!r}")


def _guarded_by_a_conditional(func: ast.FunctionDef, call: ast.Call) -> bool:
    """True when `call` sits inside an `if` somewhere under `func`."""
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            for descendant in ast.walk(node):
                if descendant is call:
                    return True
    return False


@pytest.mark.parametrize("filename", GUARDED_REVISIONS)
def test_every_creating_ddl_call_is_conditional(filename):
    """No revision may call op.create_*/op.add_column unconditionally.

    A bare call aborts on a database where startup's create_all() already made
    the object, and an aborted revision blocks every revision behind it.
    """
    tree = _tree(filename)
    offenders = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "op"
                    and func.attr in CREATING_OPS):
                if not _guarded_by_a_conditional(node, call):
                    offenders.append(f"{node.name}():{func.attr} line {call.lineno}")
    assert not offenders, (
        f"{filename} calls creating DDL without an existence check: {offenders}. "
        f"Route it through the revision's _create_table/_create_index/_add_column "
        f"helpers, or wrap it in an inspector guard.")


@pytest.mark.parametrize("filename", GUARDED_REVISIONS)
def test_every_dropping_ddl_call_is_conditional(filename):
    """downgrade() must tolerate a partially applied schema too.

    The live database has the three audit columns and none of the three audit
    indexes. A downgrade that drops an index it never created fails just as
    hard as an upgrade that creates one twice.
    """
    tree = _tree(filename)
    offenders = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "op"
                    and func.attr in {"drop_table", "drop_index", "drop_column"}):
                if not _guarded_by_a_conditional(node, call):
                    offenders.append(f"{node.name}():{func.attr} line {call.lineno}")
    assert not offenders, (
        f"{filename} drops objects without checking they exist: {offenders}")


@pytest.mark.parametrize("filename", GUARDED_REVISIONS)
def test_guards_tolerate_offline_sql_mode(filename):
    """`alembic upgrade --sql` has no bind, so the guards must check as_sql.

    Without this a guarded revision raises NoInspectionAvailable against the
    MockConnection and no offline script can be produced for review.
    """
    source = _source(filename)
    assert "as_sql" in source, (
        f"{filename} inspects the bind but never checks "
        f"op.get_context().as_sql, so it cannot render in offline --sql mode.")


class TestAuditFieldsRevision:
    """20260817_audit_fields — the revision that could not run at all."""

    FILENAME = "20260817_audit_log_fields.py"

    def test_it_writes_no_row(self):
        """The 251-row backfill belongs to a data-remediation operation.

        Schema migration and audit-history rewriting are different risk classes,
        and coupling them held the three indexes hostage to a records decision.
        """
        tree = _tree(self.FILENAME)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    upper = child.value.upper()
                    assert not re.search(r"\b(UPDATE|INSERT INTO|DELETE FROM)\b", upper), (
                        f"{self.FILENAME}:{node.name}() carries a DML statement. "
                        f"Row changes belong in "
                        f"scripts/remediate_audit_log_classification.py.")
                if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                        and isinstance(child.func.value, ast.Name)
                        and child.func.value.id == "op"
                        and child.func.attr == "execute"):
                    raise AssertionError(
                        f"{self.FILENAME}:{node.name}() calls op.execute(); this "
                        f"revision is structural only.")

    def test_it_does_not_use_the_jsonb_only_key_operator(self):
        """`details ? 'k'` is jsonb-only and audit_logs.details is json.

        That operator is what made the original revision abort with
        `operator does not exist: json ? unknown`.
        """
        assert "details ?" not in _code(_source(self.FILENAME))

    def test_index_creation_does_not_nest_inside_column_creation(self):
        """The live schema has all three columns and none of the three indexes.

        The original code created each index inside its column's `if absent`
        branch, so on exactly that schema no index could ever be created.
        """
        upgrade = _function(self.FILENAME, "upgrade")
        for node in ast.walk(upgrade):
            if not isinstance(node, ast.If):
                continue
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            assert not ("add_column" in body and "create_index" in body), (
                "upgrade() creates a column and an index under one condition; "
                "the two must be checked independently.")

    def test_it_skips_cleanly_when_the_table_is_absent(self):
        """audit_logs belongs to app.core.database.Base, which no revision creates.

        On a database built by `alembic upgrade head` alone the table is simply
        not there, and this revision must not take the chain down over a table
        it does not own.
        """
        source = _source(self.FILENAME)
        assert "_table_present" in source
        assert "app.core.database.Base" in source, (
            "the revision should say why the table can be missing")


class TestRemediationScript:
    """The backfill's new home has to be harder to run than a migration."""

    PATH = Path(__file__).resolve().parents[1] / "scripts" / \
        "remediate_audit_log_classification.py"

    def test_it_exists_and_parses(self):
        ast.parse(self.PATH.read_text(encoding="utf-8"))

    def test_applying_requires_a_named_approver_and_an_expected_row_count(self):
        source = self.PATH.read_text(encoding="utf-8")
        assert "--authorized-by" in source
        assert "--expect-rows" in source
        assert "REFUSED" in source

    def test_it_is_reversible(self):
        source = self.PATH.read_text(encoding="utf-8")
        assert "--revert" in source and "journal" in source, (
            "a rewrite of the audit trail must record what it changed")

    def test_it_reads_json_and_jsonb_alike(self):
        source = self.PATH.read_text(encoding="utf-8")
        assert "details ->> 'correlation_id'" in source
        assert "details ? " not in _code(source), (
            "the jsonb-only key-existence operator must not reappear in the SQL")


def test_the_chain_has_exactly_one_head():
    """Two heads mean `upgrade head` is ambiguous and deployments diverge."""
    down_revisions = {}
    revisions = set()
    for path in VERSIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision\s*=\s*["\']([^"\']+)', source, re.M)
        down = re.search(r'^down_revision\s*=\s*(?:["\']([^"\']+)|None)', source, re.M)
        if not rev:
            continue
        revisions.add(rev.group(1))
        down_revisions[rev.group(1)] = down.group(1) if down and down.group(1) else None
    heads = revisions - {d for d in down_revisions.values() if d}
    assert len(heads) == 1, f"expected a single head, found {sorted(heads)}"
    assert sum(1 for d in down_revisions.values() if d is None) == 1, (
        "more than one base revision")


class TestTefcaEnvironmentImports:
    """env.py must import every module that declares a table this chain owns.

    Metadata is populated by IMPORTING the module that declares the model, not
    by declaring the Base. env.py once imported one of the eight such modules
    and could see 47 of 135 tables, which is why `alembic check` proposed
    dropping most of the database. These tests fail if an import that the TEFCA
    scope depends on falls out again.
    """

    ENV = Path(__file__).resolve().parents[1] / "alembic" / "env.py"

    def test_env_targets_the_base_the_tefca_models_use(self):
        source = self.ENV.read_text(encoding="utf-8")
        assert "from app.core.database import Base as CoreBase" in source
        assert "_scoped_metadata()" in source

    def test_env_imports_every_module_the_tefca_scope_needs(self):
        source = _code(self.ENV.read_text(encoding="utf-8"))
        for module in ("app.models.database",          # users, audit_logs
                       "app.platform_config.models",   # platform_*
                       "app.tefca_registry.models",
                       "app.tefca_registry.rce.models",
                       "app.Tefca.models"):
            assert f"import {module}" in source, (
                f"env.py no longer imports {module}; the tables it declares are "
                f"invisible to autogenerate again")

    def test_env_does_not_import_other_programs_models(self):
        """Importing another product's models is how the 61 got in.

        The scope filter would still exclude them, but an import that has no
        reason to be here invites someone to widen the filter to match.
        """
        source = _code(self.ENV.read_text(encoding="utf-8"))
        for module in ("app.case_management.models", "app.models.migration_models",
                       "app.models.enterprise_models"):
            assert f"import {module}" not in source, (
                f"env.py imports {module}, which belongs to another program chain")
        assert "from app.models import *" not in source, (
            "star-importing app.models pulls in the whole ERP product")

    def test_env_does_not_import_app_main(self):
        """Importing the app registers routers and reads feed configuration.

        A migration run must do neither.
        """
        assert "import app.main" not in _code(self.ENV.read_text(encoding="utf-8"))

    def test_the_authoritative_users_definition_is_the_one_in_scope(self):
        """`users` is declared twice, and the two disagree.

        app/models/__init__.py declares 9 columns; app/models/database.py
        declares 16, which is what the live table has. Targeting only
        app.core.database.Base is what keeps the wrong one out — and it is also
        why Alembic no longer raises `Duplicate table keys across multiple
        MetaData objects: "users"`.
        """
        import importlib
        from app.core.database import Base as CoreBase
        from app.database import Base as AppBase
        import app.models  # noqa: F401
        importlib.import_module("app.models.database")
        assert len(AppBase.metadata.tables["users"].columns) == 9
        assert len(CoreBase.metadata.tables["users"].columns) == 16
        source = self.ENV.read_text(encoding="utf-8")
        assert "import Base as AppBase" not in source, (
            "targeting AppBase reintroduces the users collision")

    def test_merged_metadata_has_no_duplicate_table_keys(self):
        """The merge itself must not reintroduce the error it exists to avoid."""
        import importlib

        from app.core.database import Base as CoreBase
        from app.database import Base as AppBase
        import app.models  # noqa: F401
        for module in ("app.tefca_registry.models", "app.tefca_registry.rce.models",
                       "app.platform_config.models", "app.Tefca.models",
                       "app.case_management.models", "app.models.migration_models",
                       "app.api.templates", "app.api.validation_routes"):
            importlib.import_module(module)
        collisions = set(AppBase.metadata.tables) & set(CoreBase.metadata.tables)
        assert collisions == {"users"}, (
            f"a new table name now collides across the two Bases: "
            f"{sorted(collisions - {'users'})}. env.py resolves 'users' "
            f"explicitly; anything else needs the same treatment or Alembic "
            f"will refuse to run.")

    def test_unmodelled_tables_are_named_with_a_reason(self):
        """Excluding a table from comparison must be a decision, not a habit."""
        source = self.ENV.read_text(encoding="utf-8")
        assert "UNMODELLED_TABLES" in source
        assert "area1_mutation_log" in source
        assert "bulletin_articles" in source
        assert "tefca_qa_audit" in source
        assert "bulletin_recipients" in source
        # the reason each is excluded has to be written down next to it
        assert "written by database triggers" in source
        assert "hand-written `CREATE TABLE IF NOT EXISTS`" in source


class TestArea1PrivilegeCorrection:
    """20260828 must state the Phase 4 design and 20260822 must stay frozen."""

    CORRECTIVE = "20260828_area1_privilege_correction.py"
    HISTORICAL = "20260822_rce_pipeline.py"

    def test_the_historical_revision_still_carries_its_original_grants(self):
        """20260822 is not rewritten. Its blanket revoke is a fact of history.

        Editing it would make one revision id mean two different things
        depending on when a database applied it.
        """
        source = _source(self.HISTORICAL)
        assert 'REVOKE UPDATE, DELETE ON {table} FROM "{role}"' in source
        assert "_apply_immutability_grants" in source

    def test_the_correction_is_a_later_revision(self):
        source = _source(self.CORRECTIVE)
        assert 'revision = "20260828_area1_grants"' in source
        assert 'down_revision = "20260827_startup_coverage"' in source

    def test_it_matches_the_approved_column_level_design(self):
        """The migration and repository.py must not drift apart.

        The migration does not import the repository — a revision that reads
        application code stops being a fixed record. So the two are pinned to
        each other here instead.
        """
        from app.tefca_registry.rce.repository import (
            IMMUTABLE_TABLES, MUTABLE_WORKFLOW_COLUMNS, OWNER_ROLE)
        revision_module = _load(self.CORRECTIVE)
        assert tuple(revision_module.IMMUTABLE_TABLES) == tuple(IMMUTABLE_TABLES), (
            "the corrective migration's table list no longer matches "
            "repository.IMMUTABLE_TABLES")
        assert (tuple(revision_module.MUTABLE_WORKFLOW_COLUMNS)
                == tuple(MUTABLE_WORKFLOW_COLUMNS)), (
            "the corrective migration's writable columns no longer match "
            "repository.MUTABLE_WORKFLOW_COLUMNS")
        assert revision_module.OWNER_ROLE == OWNER_ROLE

    def test_it_revokes_truncate_as_well_as_update_and_delete(self):
        """TRUNCATE is not covered by DELETE and empties Area 1 just as well."""
        assert "REVOKE UPDATE, DELETE, TRUNCATE ON" in _code(_source(self.CORRECTIVE))

    def test_it_grants_update_on_exactly_the_two_workflow_columns(self):
        from app.tefca_registry.rce.repository import MUTABLE_WORKFLOW_COLUMNS
        code = _code(_source(self.CORRECTIVE))
        assert "GRANT UPDATE ({columns}) ON rce_source_records" in code
        assert 'columns = ", ".join(MUTABLE_WORKFLOW_COLUMNS)' in code
        assert len(MUTABLE_WORKFLOW_COLUMNS) == 2

    def test_the_foreign_key_lock_grant_is_conditional_on_ownership(self):
        """Inserting Area 1 records needs a row lock on the intake table.

        PostgreSQL runs that lock as the OWNER of the referenced table, so a
        role that owns rce_source_intakes and has had UPDATE revoked cannot
        insert into rce_source_records at all. The one-column grant that fixes
        it must not be issued once ownership has moved — that would be handing
        out a privilege nothing needs.
        """
        code = _code(_source(self.CORRECTIVE))
        assert 'INTAKE_FK_LOCK_COLUMN = "status"' in code
        assert '_owner_of("rce_source_intakes") != role' in code
        assert "GRANT UPDATE ({INTAKE_FK_LOCK_COLUMN}) ON rce_source_intakes" in code

    def test_it_writes_no_row(self):
        _assert_no_dml(self.CORRECTIVE)

    def test_downgrade_restores_the_previous_revisions_state(self):
        code = _code(_source(self.CORRECTIVE))
        downgrade = code[code.index("def downgrade"):]
        assert 'REVOKE UPDATE, DELETE ON {table} FROM "{role}"' in downgrade
        assert 'GRANT SELECT, INSERT ON {table} TO "{role}"' in downgrade
        assert "REVOKE UPDATE ({columns}) ON rce_source_records" in downgrade


class TestStartupTableCoverage:
    """20260827 must make `alembic upgrade head` produce a usable database."""

    FILENAME = "20260827_startup_table_coverage.py"

    def test_it_covers_the_tefca_gap_and_the_two_core_dependencies(self):
        """Three tables: TEFCA's own gap, plus what TEFCA reads from Core.

        `documents`, `tenants` and `outputs` were in an earlier draft of this
        revision and are deliberately gone — they are Core-owned and TEFCA does
        not import them. Their apparent use was `from docx import Document`.
        """
        source = _source(self.FILENAME)
        for table in ("tefca_import_history", "users", "audit_logs"):
            assert f"_has_table('{table}')" in source, (
                f"{table} is not covered; TEFCA's chain no longer builds a "
                f"database TEFCA can start against")
        for table in ("documents", "tenants", "outputs", "quotes", "cm_patients"):
            assert f"_has_table('{table}')" not in source, (
                f"{table} is not TEFCA's to create — see "
                f"docs/database_domain_architecture.md")

    def test_it_does_not_drop_the_area1_mutation_log(self):
        """Autogenerate proposed it because the table has no ORM model."""
        code = _code(_source(self.FILENAME))
        assert "area1_mutation_log" not in code

    def test_it_writes_no_row(self):
        _assert_no_dml(self.FILENAME)


class TestTefcaChainScope:
    """The TEFCA chain owns TEFCA's schema and nothing else.

    Option D of docs/database_domain_architecture.md makes each program module
    the owner of its own schema. Before that decision, `env.py` targeted every
    model the process could import, and `alembic upgrade head` would have created
    61 tables belonging to ERP, case management, migration tooling and the
    enterprise core inside a database holding federal contract evidence.

    These tests read the real constants out of `alembic/env.py` and recompute the
    owned set from the real models. They do not import env.py — importing it runs
    the migrations.
    """

    ENV = Path(__file__).resolve().parents[1] / "alembic" / "env.py"

    @staticmethod
    def _env_constant(name):
        tree = ast.parse(TestTefcaChainScope.ENV.read_text(encoding="utf-8"))
        for node in tree.body:
            if (isinstance(node, ast.Assign) and node.targets
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == name):
                return ast.literal_eval(node.value)
        raise AssertionError(f"alembic/env.py no longer defines {name}")

    @staticmethod
    def _load_models():
        import importlib
        from app.core.database import Base as CoreBase
        from app.database import Base as AppBase
        import app.models  # noqa: F401
        for module in ("app.tefca_registry.models", "app.tefca_registry.rce.models",
                       "app.platform_config.models", "app.Tefca.models",
                       "app.case_management.models", "app.models.migration_models",
                       "app.api.templates", "app.api.validation_routes"):
            importlib.import_module(module)
        return AppBase, CoreBase

    @classmethod
    def _owned(cls):
        """Recompute what env.py's _scoped_metadata() would own."""
        import app.platform_config.models as platform
        _AppBase, CoreBase = cls._load_models()
        prefixes = tuple(cls._env_constant("TEFCA_MODULE_PREFIXES"))
        owned = set(cls._env_constant("CORE_DEPENDENCIES"))
        owned |= set(platform.PLATFORM_TABLE_ORDER)
        for mapper in CoreBase.registry.mappers:
            klass = mapper.class_
            table = getattr(klass, "__tablename__", None)
            if table and klass.__module__.startswith(prefixes):
                owned.add(table)
        return owned

    @classmethod
    def _tables_of(cls, *modules):
        import importlib
        from app.core.database import Base as CoreBase
        from app.database import Base as AppBase
        cls._load_models()
        names = set()
        for base in (CoreBase, AppBase):
            for mapper in base.registry.mappers:
                klass = mapper.class_
                table = getattr(klass, "__tablename__", None)
                if table and klass.__module__ in modules:
                    names.add(table)
        return names

    def test_scope_excludes_erp_business_tables(self):
        # `users` is declared twice — the stale 9-column copy in app/models/
        # __init__.py and the authoritative 16-column one in app/models/
        # database.py. Subtracting the Core module keeps the fixture to the ERP
        # product rather than flagging a Core dependency as a leak.
        erp = self._tables_of("app.models") - self._tables_of("app.models.database")
        assert len(erp) > 40, (
            f"expected the ERP product's table set, got {len(erp)} — the fixture "
            f"is wrong, not the scope")
        leaked = erp & self._owned()
        assert not leaked, (
            f"the TEFCA chain would manage ERP/business tables: {sorted(leaked)}")

    def test_scope_excludes_bulletin_tables(self):
        """Bulletin persistence is raw startup SQL in a subsystem TEFCA does not own."""
        owned = self._owned()
        leaked = {t for t in owned if t.startswith("bulletin_")}
        assert not leaked, f"the TEFCA chain would manage Bulletin tables: {leaked}"

    def test_scope_excludes_case_management_and_migration_tooling(self):
        other = (self._tables_of("app.case_management.models")
                 | self._tables_of("app.models.migration_models"))
        assert other, "no case-management or migration-tooling models found"
        leaked = other & self._owned()
        assert not leaked, (
            f"the TEFCA chain would manage another program's tables: {sorted(leaked)}")

    def test_scope_excludes_the_enterprise_core(self):
        """contexts -> decisions -> actions -> traceability is Core, not TEFCA."""
        enterprise = self._tables_of("app.models.enterprise_models")
        leaked = enterprise & self._owned()
        assert not leaked, (
            f"the TEFCA chain would manage the enterprise core: {sorted(leaked)}")

    def test_scope_covers_every_tefca_model(self):
        """A TEFCA table the chain does not own is a table nothing creates."""
        tefca = (self._tables_of("app.Tefca.models")
                 | self._tables_of("app.tefca_registry.models")
                 | self._tables_of("app.tefca_registry.rce.models"))
        assert len(tefca) >= 40, f"expected the full TEFCA model set, got {len(tefca)}"
        missing = tefca - self._owned()
        assert not missing, f"TEFCA tables outside the TEFCA chain: {sorted(missing)}"

    def test_core_dependencies_are_only_what_tefca_imports(self):
        """`users` and `audit_logs`, traced from import statements.

        `documents` was `from docx import Document`; `audit_log` was a class-name
        collision. Neither belongs here, and adding one back should need a reason.
        """
        assert self._env_constant("CORE_DEPENDENCIES") == {"users", "audit_logs"}

    def test_no_owned_table_has_a_foreign_key_outside_the_scope(self):
        """A foreign key crossing the boundary means the boundary is wrong."""
        _AppBase, CoreBase = self._load_models()
        owned = self._owned()
        crossings = []
        for name, table in CoreBase.metadata.tables.items():
            if name not in owned:
                continue
            for fk in table.foreign_keys:
                target = (fk._colspec.split(".")[0]
                          if isinstance(fk._colspec, str) else fk.column.table.name)
                if target not in owned:
                    crossings.append(f"{name} -> {target}")
        assert not crossings, (
            f"foreign keys leave the TEFCA scope: {crossings}. Either the target "
            f"belongs in the scope or the source does not belong to TEFCA.")

    def test_comparison_is_restricted_to_owned_tables(self):
        """`alembic check` must mean 'is TEFCA in sync', not 'is anything else here'."""
        source = self.ENV.read_text(encoding="utf-8")
        assert "TEFCA_CHAIN_TABLES" in source
        assert "return name in TEFCA_CHAIN_TABLES" in source

    def test_the_coverage_revision_creates_only_owned_tables(self):
        revision = _source("20260827_startup_table_coverage.py")
        created = set(re.findall(r"op\.create_table\('([^']+)'", revision))
        assert created == {"tefca_import_history", "users", "audit_logs"}, (
            f"the coverage revision creates {sorted(created)}; it should cover "
            f"only TEFCA's own gap plus the two Core dependencies")
        leaked = created - self._owned()
        assert not leaked, f"coverage revision creates unowned tables: {leaked}"

    def test_the_coverage_revision_creates_no_enum_types(self):
        """All 35 enum types belonged to the removed tables."""
        revision = _code(_source("20260827_startup_table_coverage.py"))
        assert "ENUM_TYPES" not in revision
        assert "_create_enum_types" not in revision
