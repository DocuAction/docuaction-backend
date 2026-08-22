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
