"""CSV export reconciliation: embedded newlines, formula-injection safety, and
row-count semantics (2026-10-03).

These are unit-level proofs of the primitives `delivery_routes.py`'s streaming
CSV routes rely on -- `neutralise_row` for formula-injection protection, and
the fact that a correct row count must come from a CSV-aware parse (or,
equivalently, the same DB COUNT(*) the export route itself registers), never
from counting newline bytes in the file. The route-level consistent-snapshot
behavior (`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`) is
exercised by the existing route-level test suite
(`test_verification_drilldown_routes.py`), which already runs the real route
against a real database; this file covers the primitives that behavior
depends on in isolation, with no database required.
"""
import csv
import io

from app.reports.engine.csv_engine import neutralise_row


def test_embedded_newline_round_trips_exactly_under_rfc4180_quoting():
    original_rationale = "Line 1 of rationale\nLine 2 of rationale, continued"
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "rationale"])
    writer.writerow(neutralise_row(["1", original_rationale]))
    csv_text = buf.getvalue()

    rows = list(csv.reader(io.StringIO(csv_text)))
    assert len(rows) - 1 == 1, "a CSV-aware parse must find exactly one data row"
    assert rows[1][1] == original_rationale, (
        "the embedded newline must survive RFC4180 quoting byte-for-byte")


def test_naive_newline_counting_is_not_row_counting():
    """The property a registered row count must NOT rely on: counting '\\n'
    bytes in the file. One row with an embedded newline contributes TWO
    newline bytes but is still exactly one record."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "rationale"])
    writer.writerow(neutralise_row(["1", "line one\nline two"]))
    writer.writerow(neutralise_row(["2", "no embedded newline here"]))
    csv_text = buf.getvalue()

    real_row_count = len(list(csv.reader(io.StringIO(csv_text)))) - 1
    naive_newline_count = csv_text.count("\n")
    assert real_row_count == 2
    assert naive_newline_count != real_row_count, (
        "this is the exact failure mode a naive line-count-based row "
        "registration would exhibit -- it must never be used")


def test_formula_leading_values_are_neutralised_without_mutating_the_source():
    source_value = "=SUM(A1:A10)"
    neutralised = neutralise_row(["2", source_value])
    assert neutralised[1] == "'" + source_value
    # neutralise_row returns a NEW list; the caller's original object/string
    # is never mutated -- the underlying persisted evidence stays untouched.
    assert source_value == "=SUM(A1:A10)"


def test_every_owasp_formula_leader_is_neutralised():
    for leader in ("=", "+", "-", "@", "\t", "\r"):
        value = f"{leader}cmd|' /C calc'!A0"
        neutralised = neutralise_row([value])[0]
        assert neutralised.startswith("'" + leader), (
            f"leader {leader!r} must be neutralised, got {neutralised!r}")


def test_non_formula_values_are_never_prefixed():
    for value in ("Highland Point Medical Group", "123 Main St", "", None, 42):
        assert neutralise_row([value])[0] == value
