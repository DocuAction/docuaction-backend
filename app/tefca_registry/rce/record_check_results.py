"""Versioned per-record check-result map: vocabulary, writer validation, reader.

PURE. No database access lives here, so the closed vocabulary, the writer's
validation and the reader's refusal rules are unit-testable without a database
and cannot drift from the engine that imports them.

MAP VERSION 1
-------------
One row per (run, record) in `rce_record_check_results`:

    map_version  smallint   1
    rule_count   smallint   number of keys the writer intended (truncation check)
    outcomes     jsonb      {"<rule_id>": "<code>", ...}

The code set is CLOSED and fixed here:

    F  finding raised      the rule returned at least one finding
    P  pass                the rule applied and returned nothing
    N  not applicable      the rule's applicability predicate was false
    S  skipped             the rule was configured not to run for this record
    E  error               the rule (or its predicate) raised on this record
    U  unqualified         the rule has no applicability declaration

"No finding" alone proves nothing: only P is evidence that a rule applied and
found nothing. A rule that raised is E and can never look like a pass.

U IS IMPLICIT IN STORAGE
A rule with no applicability declaration is `U` for every record of every run,
by construction, and the run's history row says so (`requires_hash` is NULL).
Writing 30-odd identical "U" entries per record would multiply the table size
for no information, so the engine stores ONLY the declared (record-scope)
rules, and the reader supplies `U` for a record-scope rule whose history row
has no declaration. An explicit "U" is still a valid code (the writer accepts
it and the reader decodes it), so the closed set is unchanged.

Rule versions and declaration hashes are not repeated per row. They come from
the run's `rce_rule_execution_history` rows (rule_version, requires_hash,
scope, coverage), so a stored map can always be interpreted without the current
code. A rule whose history row has `scope = 'RUN'` is population-level and is
recorded at run level only, never in the per-record map.

The READER never guesses. An unknown `map_version`, an unknown code, a key set
that disagrees with the run's rule list, or a `rule_count` mismatch yields
`RESULT_SCHEMA_UNSUPPORTED`.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

MAP_VERSION = 1
SUPPORTED_MAP_VERSIONS = frozenset({1})

CODE_FINDING = "F"
CODE_PASS = "P"
CODE_NOT_APPLICABLE = "N"
CODE_SKIPPED = "S"
CODE_ERROR = "E"
CODE_UNQUALIFIED = "U"

#: Closed code set for map_version 1.
CODES_V1 = frozenset({CODE_FINDING, CODE_PASS, CODE_NOT_APPLICABLE, CODE_SKIPPED,
                      CODE_ERROR, CODE_UNQUALIFIED})

CODE_NAMES: Dict[str, str] = {
    "F": "FAIL", "P": "PASS", "N": "NOT_APPLICABLE", "S": "SKIPPED",
    "E": "ERROR", "U": "UNQUALIFIED",
}

SCOPE_RECORD = "RECORD"
SCOPE_RUN = "RUN"

REASON_UNSUPPORTED = "RESULT_SCHEMA_UNSUPPORTED"


class ResultMapInvalid(ValueError):
    """The writer refused a map: unknown code, foreign rule id, or wrong count."""


def validate_for_write(outcomes: Dict[str, str], rule_count: int,
                       run_rule_ids: Iterable[str],
                       map_version: int = MAP_VERSION) -> None:
    """Refuse to persist a map that the reader would later have to distrust.

    * the map_version must be one this code writes
    * every code must be in the closed set
    * every rule id must belong to THIS run's rule list
    * rule_count must equal the number of keys actually present
    """
    if map_version not in SUPPORTED_MAP_VERSIONS:
        raise ResultMapInvalid(f"unsupported map_version {map_version!r}")
    allowed = (run_rule_ids if isinstance(run_rule_ids, (set, frozenset))
               else set(run_rule_ids))
    unknown_codes = sorted({str(c) for c in outcomes.values() if c not in CODES_V1})
    if unknown_codes:
        raise ResultMapInvalid(f"codes outside the closed set: {unknown_codes}")
    foreign = sorted(set(outcomes) - allowed)
    if foreign:
        raise ResultMapInvalid(f"rule ids not in this run: {foreign}")
    if rule_count != len(outcomes):
        raise ResultMapInvalid(
            f"rule_count {rule_count} does not match {len(outcomes)} outcome keys")


def record_scope_rule_ids(history_rows: Iterable[Dict[str, Any]]) -> frozenset:
    """Every rule of the run that is not population-scope."""
    return frozenset(r["rule_id"] for r in history_rows
                     if (r.get("scope") or SCOPE_RECORD) != SCOPE_RUN)


def declared_rule_ids(history_rows: Iterable[Dict[str, Any]]) -> frozenset:
    """Record-scope rules that carried a declaration in this run. Only these
    MUST appear in a stored map; the rest are `U` by construction."""
    return frozenset(r["rule_id"] for r in history_rows
                     if (r.get("scope") or SCOPE_RECORD) != SCOPE_RUN
                     and r.get("requires_hash") is not None)


def read_map(map_version: Optional[int], rule_count: Optional[int],
             outcomes: Optional[Dict[str, Any]],
             history_rows: Iterable[Dict[str, Any]]
             ) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Decode one stored map. Returns (codes, None) or (None, reason).

    `history_rows` are the run's rule-execution rows as dicts with at least
    `rule_id`, `scope` and `requires_hash`. The returned dict covers every
    record-scope rule of the run: stored codes as written, `U` for an
    undeclared rule that was not stored. Never raises, never repairs a
    malformed map: an unknown version, an unknown code, a key that is not a
    record-scope rule of this run, a declared rule that is missing, a code other
    than `U` for an undeclared rule, or a `rule_count` that disagrees with the
    stored keys is RESULT_SCHEMA_UNSUPPORTED.
    """
    if map_version not in SUPPORTED_MAP_VERSIONS:
        return None, REASON_UNSUPPORTED
    if not isinstance(outcomes, dict):
        return None, REASON_UNSUPPORTED
    if any(not isinstance(k, str) for k in outcomes):
        return None, REASON_UNSUPPORTED
    if any(c not in CODES_V1 for c in outcomes.values()):
        return None, REASON_UNSUPPORTED
    if rule_count != len(outcomes):
        return None, REASON_UNSUPPORTED
    rows = list(history_rows)
    record_ids = record_scope_rule_ids(rows)
    declared = declared_rule_ids(rows)
    keys = set(outcomes)
    if not keys <= record_ids or not declared <= keys:
        return None, REASON_UNSUPPORTED
    if any(outcomes[k] != CODE_UNQUALIFIED for k in keys - declared):
        return None, REASON_UNSUPPORTED
    codes = {rid: CODE_UNQUALIFIED for rid in record_ids}
    codes.update(outcomes)
    return codes, None


def outcome_code(rule, ctx, findings, errored: bool) -> str:
    """The one-letter outcome for one rule on one record (engine helper).

    `findings` is what `rule.evaluate` returned (already `[]` when it raised);
    `errored` says whether evaluate raised. The predicate is consulted only for
    a DECLARED rule, and a predicate that raises is an error, not a pass.
    """
    if not rule.declared:
        return CODE_UNQUALIFIED
    if errored:
        return CODE_ERROR
    try:
        applicable = bool(rule.applies(ctx))
    except Exception:  # noqa: BLE001 - recorded as E, never as P
        return CODE_ERROR
    if findings:
        # A finding on a record the predicate calls not-applicable is a
        # declaration bug (the consistency test forbids it); record the fact
        # that a finding was raised rather than hiding it.
        return CODE_FINDING
    return CODE_PASS if applicable else CODE_NOT_APPLICABLE
