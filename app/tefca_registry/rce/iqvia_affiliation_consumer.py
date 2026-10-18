"""IQVIA HCP_AFFIL consumption, DEFAULT OFF, advisory only.

WHAT THIS IS
------------
The first consumer of `iqvia_affiliation_observation`. Until now the rows were
staged and nothing read them (`iqvia_match._matching_capability` refuses the
AFFILIATION source). This module adds the smallest honest read path:

  1. resolve an organisation identifier pair (ORG_NPI / ORG_CCN_ID) to registry
     entity CANDIDATES, with normalization and an explicit NPI-versus-CCN
     conflict outcome;
  2. list the relationships a staged HCP key participates in, with relationship
     kind kept apart from identity;
  3. state, truthfully, whether IQVIA data was used (`coverage_statement`).

WHAT THIS IS NOT
----------------
* Not yet wired as a verification source, and it activates nothing. HHS/ONC supplied the IQVIA data and
  directed its use as the fifth source (supplemental to S1-S4: identity and relationship corroboration, never
  exclusion clearance). Nothing here changes a bucket, a determination, a report row, B1/B4 policy or the
  `source_policy` entry (IQVIA stays PROPOSED_INACTIVE). Activation is separate steps: QA review of the
  snapshot, then the controlled source-policy activation. Staging alone is not verified source coverage.
* Never confirmed. Every outcome is `AUTOMATED_CANDIDATE_NOT_CONFIRMED` and
  `requires_analyst_review` is always True. There is no AUTO_APPROVED outcome.
* Writes nothing: no EntitySourceMatch row, no relationship row (the design
  rule "an affiliation row never becomes a tefca_entity_relationships row"
  stands), no snapshot status change.
* Not reachable unless `ENABLE_IQVIA_AFFILIATION_CONSUMPTION` is true AND
  `ENABLE_IQVIA_SOURCES` is true AND the caller is above the reviewer floor.
  Reading a snapshot that is not APPROVED/SUPERSEDED is refused
  (`SnapshotNotEligible`) except for the explicit, never-routed
  `diagnostic_allow_pending` switch used by offline diagnostics.

Licensed values never appear in log lines or exception text here; helpers
return reason CODES, and masked forms (`mask`) when a value must be shown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from sqlalchemy import Text, func, literal_column, select, tuple_

from app.services.npi_validator import validate_npi
from app.tefca_registry import models as reg
from app.tefca_registry.rce import snapshot_models as sm

FLAG = "ENABLE_IQVIA_AFFILIATION_CONSUMPTION"

# ── outcomes ─────────────────────────────────────────────────────────────────
ORG_NO_IDENTIFIER = "NO_ORG_IDENTIFIER"            # row carries neither ORG_NPI nor ORG_CCN_ID
ORG_INVALID_IDENTIFIER = "INVALID_IDENTIFIER"      # only identifier(s) present failed validation
ORG_NOT_IN_REGISTRY = "NOT_IN_REGISTRY"            # valid identifier(s); no registry entity holds them
ORG_CANDIDATE = "CANDIDATE"                        # exactly one entity; NOT a confirmed match
ORG_AMBIGUOUS = "AMBIGUOUS"                        # more than one entity
ORG_CONFLICT = "CONFLICT"                          # NPI and CCN point to different entities

DETERMINATION = "AUTOMATED_CANDIDATE_NOT_CONFIRMED"

KIND_PROVIDER = "PROVIDER"   # AFFL_TYP_ID: e.g. attending/admitting
KIND_CONTACT = "CONTACT"     # TITL_TYP_ID: a person's role at an organisation
KIND_UNTYPED = "UNTYPED"


class SnapshotNotEligible(ValueError):
    """The snapshot is not APPROVED/SUPERSEDED (or is the wrong source)."""


# ── normalization ────────────────────────────────────────────────────────────
_DIGITS = re.compile(r"^[0-9]+$")
_CCN_SHAPE = re.compile(r"^[0-9]{2}[0-9A-Z][0-9]{3}$")  # CMS: 6 chars, 3rd may be a letter (e.g. 01T001)


def mask(value: Any) -> str:
    v = "" if value is None else str(value)
    return f"{v[:2]}***(len{len(v)})" if v else "<empty>"


def normalize_npi(value: Any) -> Dict[str, Any]:
    """Trim; accept a spreadsheet-style trailing '.0'; require 10 digits and a
    valid Luhn check digit (the existing `validate_npi`). Returns
    {"value": str|None, "present": bool, "valid": bool, "reason": code|None}."""
    raw = "" if value is None else str(value).strip()
    if not raw:
        return {"value": None, "present": False, "valid": False, "reason": None}
    if raw.endswith(".0") and _DIGITS.match(raw[:-2] or "x"):
        raw = raw[:-2]
    ok, _msg = validate_npi(raw)  # message deliberately discarded: it may echo digits
    if not ok:
        return {"value": None, "present": True, "valid": False, "reason": "NPI_FAILED_VALIDATION"}
    return {"value": raw, "present": True, "valid": True, "reason": None}


def normalize_ccn(value: Any) -> Dict[str, Any]:
    """Trim, upper-case, drop spaces and hyphens. A digits-only value of
    exactly 5 characters is left-padded by ONE zero (a CCN's leading state
    zero is routinely lost in CSV/Excel round trips); shorter values are NOT
    padded, because inventing several zeros could create a false hit.
    Result must match the 6-character CMS shape."""
    raw = "" if value is None else str(value).strip().upper()
    raw = raw.replace(" ", "").replace("-", "")
    if raw.endswith(".0") and _DIGITS.match(raw[:-2] or "x"):
        raw = raw[:-2]
    if not raw:
        return {"value": None, "present": False, "valid": False, "reason": None, "padded": False}
    padded = False
    if _DIGITS.match(raw) and len(raw) == 5:
        raw, padded = "0" + raw, True
    if not _CCN_SHAPE.match(raw):
        return {"value": None, "present": True, "valid": False, "reason": "CCN_BAD_SHAPE", "padded": False}
    return {"value": raw, "present": True, "valid": True, "reason": None, "padded": padded}


def ccn_lookup_variants(normalized_ccn: str) -> List[str]:
    """Registry values may have lost the leading zero; query both spellings."""
    out = [normalized_ccn]
    if normalized_ccn.startswith("0") and _DIGITS.match(normalized_ccn):
        out.append(normalized_ccn[1:])
    return out


# ── organisation resolution (pure) ───────────────────────────────────────────
@dataclass
class OrgResolution:
    outcome: str
    entity_ids: List[str] = field(default_factory=list)
    basis: Optional[str] = None            # "npi" | "ccn" | "npi+ccn"
    notes: List[str] = field(default_factory=list)
    npi_masked: str = "<empty>"
    ccn_masked: str = "<empty>"
    determination: str = DETERMINATION
    requires_analyst_review: bool = True   # invariant: never False
    affects_verification: bool = False     # invariant: never True

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def resolve_organisation_from_sets(*, org_npi: Any, org_ccn: Any,
                                   npi_entities: Iterable[str] = (),
                                   ccn_entities: Iterable[str] = ()) -> OrgResolution:
    """Decide from already-fetched registry hits. `npi_entities` are the entity
    ids holding the normalized NPI, `ccn_entities` those holding the CCN (either
    spelling). No I/O, so every branch is directly testable.

    Truth table (valid identifiers only; invalid ones add a note):
      none present                      -> NO_ORG_IDENTIFIER
      present but all invalid           -> INVALID_IDENTIFIER
      no registry hit                   -> NOT_IN_REGISTRY ("not in the registry",
                                           which is NOT "invalid" and NOT "verified clear")
      one hit set, one entity           -> CANDIDATE
      one hit set, >1 entity            -> AMBIGUOUS
      both sets, same single entity     -> CANDIDATE (basis npi+ccn)
      both sets, disjoint               -> CONFLICT
      both sets, overlapping, not equal -> AMBIGUOUS
    An identifier that hits while the other does not is a CANDIDATE with a note:
    absence of one identifier from the registry is not contradiction."""
    n, c = normalize_npi(org_npi), normalize_ccn(org_ccn)
    res = OrgResolution(outcome=ORG_NO_IDENTIFIER, npi_masked=mask(org_npi), ccn_masked=mask(org_ccn))
    if not n["present"] and not c["present"]:
        return res
    if n["present"] and not n["valid"]:
        res.notes.append(n["reason"])
    if c["present"] and not c["valid"]:
        res.notes.append(c["reason"])
    if c.get("padded"):
        res.notes.append("CCN_LEADING_ZERO_RESTORED")
    if not n["valid"] and not c["valid"]:
        res.outcome = ORG_INVALID_IDENTIFIER
        return res

    sn: Set[str] = set(map(str, npi_entities)) if n["valid"] else set()
    sc: Set[str] = set(map(str, ccn_entities)) if c["valid"] else set()
    res.notes.append("NPPES_ENTITY_TYPE_NOT_EVIDENCED")  # Type 2 not asserted by this module

    if n["valid"] and c["valid"] and sn and sc:
        res.entity_ids = sorted(sn | sc)
        if sn.isdisjoint(sc):
            res.outcome, res.basis = ORG_CONFLICT, "npi+ccn"
            res.notes.append("NPI_AND_CCN_POINT_TO_DIFFERENT_ENTITIES")
        elif sn == sc and len(sn) == 1:
            res.outcome, res.basis = ORG_CANDIDATE, "npi+ccn"
        else:
            res.outcome, res.basis = ORG_AMBIGUOUS, "npi+ccn"
        return res

    hits, basis = (sn, "npi") if sn else (sc, "ccn")
    if not hits:
        res.outcome = ORG_NOT_IN_REGISTRY
        return res
    res.entity_ids, res.basis = sorted(hits), basis
    if n["valid"] and c["valid"]:
        res.notes.append("OTHER_IDENTIFIER_NOT_IN_REGISTRY")
    res.outcome = ORG_CANDIDATE if len(hits) == 1 else ORG_AMBIGUOUS
    return res


# ── relationship versus identity (pure) ──────────────────────────────────────
def classify_relationship(affiliation_type: Optional[str]) -> Dict[str, Optional[str]]:
    """Mirror of the importer's `_affiliation_kind` encoding: 'UNTYPED',
    'CONTACT:<TITL_TYP_ID>', else a provider AFFL_TYP_ID."""
    t = (affiliation_type or "").strip()
    if not t or t == "UNTYPED":
        return {"kind": KIND_UNTYPED, "type_id": None}
    if t.startswith("CONTACT:"):
        return {"kind": KIND_CONTACT, "type_id": t[len("CONTACT:"):] or None}
    return {"kind": KIND_PROVIDER, "type_id": t}


def summarise_relationships(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """`rows`: dicts with hcp_record_key, hco_record_key, affiliation_type,
    payload. Counts RELATIONSHIPS and distinct keys separately and reports a
    person-identity conflict instead of merging. Several rows never imply
    several identities, and one HCP key carrying two NPIs is flagged, not
    collapsed."""
    by_kind: Dict[str, int] = {KIND_PROVIDER: 0, KIND_CONTACT: 0, KIND_UNTYPED: 0}
    hcp_keys: Set[str] = set()
    hco_keys: Set[str] = set()
    pairs: Dict[Any, Set[str]] = {}
    npis: Dict[str, Set[str]] = {}
    for r in rows:
        by_kind[classify_relationship(r.get("affiliation_type"))["kind"]] += 1
        hcp, hco = r["hcp_record_key"], r["hco_record_key"]
        hcp_keys.add(hcp)
        hco_keys.add(hco)
        pairs.setdefault((hcp, hco), set()).add(r.get("affiliation_type") or "UNTYPED")
        p = normalize_npi((r.get("payload") or {}).get("NPI"))
        if p["valid"]:
            npis.setdefault(hcp, set()).add(p["value"])
    return {
        "relationship_rows": len(rows),
        "distinct_hcp_keys": len(hcp_keys),
        "distinct_hco_keys": len(hco_keys),
        "by_kind": by_kind,
        "pairs_with_multiple_types": sum(1 for v in pairs.values() if len(v) > 1),
        "hcp_keys_with_conflicting_npi": sorted(mask(k) for k, v in npis.items() if len(v) > 1),
        "identity_inference": "none: relationship rows are not identities; no merge was performed",
    }


# ── honest coverage statement (pure) ─────────────────────────────────────────
def coverage_statement(*, flag_enabled: bool, snapshots: Sequence[Dict[str, Any]],
                       consumed: bool = False) -> Dict[str, Any]:
    """`snapshots`: dicts with source_system, status, record_count (no row data).
    States: not_used | staged_not_used | approved_not_consumed | consumed_candidates_only.
    Never 'verified'. IQVIA is not in the contracted verification set."""
    aff = [s for s in snapshots if s.get("source_system") == sm.SOURCE_IQVIA_AFFILIATION]
    effective = [s for s in aff if s.get("status") in sm.SNAPSHOT_EFFECTIVE]
    if not aff:
        state, text = "not_used", "No IQVIA affiliation data is staged. No IQVIA data was used."
    elif not effective:
        state = "staged_not_used"
        text = ("IQVIA affiliation data is staged (not approved). It was not used to verify any entity "
                "and is not a verification source.")
    elif not (flag_enabled and consumed):
        state = "approved_not_consumed"
        text = "An IQVIA affiliation snapshot is approved but no check consumed it. No entity was verified from it."
    else:
        state = "consumed_candidates_only"
        text = ("IQVIA affiliation data produced analyst-review candidates only. These are automated, "
                "unconfirmed, and do not verify any entity.")
    return {"source": "IQVIA_AFFILIATION", "state": state, "statement": text,
            "verification_source": False, "snapshots": [
                {"id": str(s.get("id")), "status": s.get("status"), "record_count": s.get("record_count")}
                for s in aff]}


# ── database access (read-only) ──────────────────────────────────────────────
async def _eligible_observation_snapshot_id(db, snapshot_id, *, diagnostic_allow_pending: bool):
    snap = await db.get(sm.SourceSnapshot, snapshot_id)
    if snap is None:
        raise SnapshotNotEligible("no such snapshot")
    if snap.source_system != sm.SOURCE_IQVIA_AFFILIATION:
        raise SnapshotNotEligible("not an affiliation snapshot")
    if snap.status not in sm.SNAPSHOT_EFFECTIVE and not (
            diagnostic_allow_pending and snap.status == sm.SNAPSHOT_PENDING):
        raise SnapshotNotEligible(f"snapshot is {snap.status}; only an approved snapshot may be read")
    root = snap
    while root.supersedes_snapshot_id is not None:  # rows live under the registration-time id
        root = await db.get(sm.SourceSnapshot, root.supersedes_snapshot_id)
    return snap, root.id


async def registry_entities_with(db, identifier_type: str, values: Sequence[str]) -> List[str]:
    rows = (await db.execute(select(reg.TefcaEntityIdentifier.entity_id).where(
        reg.TefcaEntityIdentifier.identifier_type == identifier_type,
        reg.TefcaEntityIdentifier.identifier_value.in_(list(values))))).scalars().all()
    return sorted({str(r) for r in rows})


async def resolve_organisation(db, *, org_npi: Any, org_ccn: Any) -> OrgResolution:
    n, c = normalize_npi(org_npi), normalize_ccn(org_ccn)
    npi_hits = await registry_entities_with(db, "npi", [n["value"]]) if n["valid"] else []
    ccn_hits = (await registry_entities_with(db, "ccn", ccn_lookup_variants(c["value"]))) if c["valid"] else []
    return resolve_organisation_from_sets(org_npi=org_npi, org_ccn=org_ccn,
                                          npi_entities=npi_hits, ccn_entities=ccn_hits)


async def relationships_for_hcp(db, *, snapshot_id, hcp_record_key: str, limit: int = 200,
                                diagnostic_allow_pending: bool = False) -> Dict[str, Any]:
    """Indexed lookup (leading columns of uq_iqvia_affiliation_snapshot_key:
    snapshot, hcp). Returns summary + provenance; org identifiers are masked."""
    snap, root_id = await _eligible_observation_snapshot_id(
        db, snapshot_id, diagnostic_allow_pending=diagnostic_allow_pending)
    obs = sm.IqviaAffiliationObservation
    rows = (await db.execute(select(obs).where(
        obs.source_snapshot_id == root_id, obs.hcp_record_key == hcp_record_key)
        .order_by(obs.hco_record_key, obs.affiliation_type).limit(max(1, min(limit, 500))))).scalars().all()
    plain = [{"hcp_record_key": r.hcp_record_key, "hco_record_key": r.hco_record_key,
              "affiliation_type": r.affiliation_type, "payload": r.payload} for r in rows]
    out = summarise_relationships(plain)
    out["found_in_extract"] = bool(plain)   # False means NOT FOUND IN THIS EXTRACT, nothing more
    out["provenance"] = {"snapshot_id": str(snap.id), "source_system": snap.source_system,
                         "status": snap.status, "record_count": snap.record_count,
                         "sha256_prefix": (snap.sha256 or "")[:12]}
    out["determination"] = DETERMINATION
    out["requires_analyst_review"] = True
    out["affects_verification"] = False
    if snap.status not in sm.SNAPSHOT_EFFECTIVE:
        out["diagnostic_only"] = "NOT_ELIGIBLE_FOR_VERIFICATION_SNAPSHOT_NOT_APPROVED"
    return out


# -- organisation-first lookup (read-only; see docs/architecture/iqvia_org_first_design.md) -------------
PAGE_DEFAULT = 100
PAGE_MAX = 500          # hard cap; a caller asking for more is told it was capped
MAX_ORGS_LISTED = 25    # organisations returned with their own summaries; the rest are counted, not listed

CONFLICT_NPI_CCN_DIFFERENT_ORGS = "ORG_NPI_AND_ORG_CCN_IDENTIFY_DIFFERENT_ORGANISATIONS"
CONFLICT_HCO_MULTIPLE_NPI = "HCO_KEY_CARRIES_MULTIPLE_ORG_NPI"
CONFLICT_HCO_MULTIPLE_CCN = "HCO_KEY_CARRIES_MULTIPLE_ORG_CCN"
CONFLICT_IDENTIFIER_SHARED = "IDENTIFIER_SHARED_BY_MULTIPLE_HCO_KEYS"


def _org_identifier_expr(key: str):
    """nullif(btrim(payload ->> '<key>'), '') with the key and the empty string INLINED as SQL literals.

    This must stay textually identical to the partial expression indexes of migration
    20261009_iqvia_affil_org_indexes (ix_iqvia_affil_snapshot_org_npi / _org_ccn). If the key were a
    bind parameter (SQLAlchemy's `payload["ORG_NPI"].astext` renders `payload ->> $2`), a cached
    GENERIC plan could not match the index and would fall back to a sequential scan (measured:
    docs/architecture/iqvia_org_first_design.md, 'Verified indexed path (local)'). `key` is only ever
    one of the two constants below, never caller input."""
    assert key in ("ORG_NPI", "ORG_CCN_ID")
    obs = sm.IqviaAffiliationObservation
    return func.nullif(func.btrim(obs.payload.op("->>", return_type=Text)(literal_column(f"'{key}'"))),
                       literal_column("''"))


def _org_npi_expr():
    return _org_identifier_expr("ORG_NPI")


def _org_ccn_expr():
    return _org_identifier_expr("ORG_CCN_ID")


def _provenance(snap) -> Dict[str, Any]:
    return {"snapshot_id": str(snap.id), "source_system": snap.source_system, "status": snap.status,
            "record_count": snap.record_count, "sha256_prefix": (snap.sha256 or "")[:12]}


async def _hco_keys_for(db, root_id, expr, values: Sequence[str]) -> List[str]:
    obs = sm.IqviaAffiliationObservation
    rows = (await db.execute(select(obs.hco_record_key).where(
        obs.source_snapshot_id == root_id, expr.in_(list(values))).distinct())).scalars().all()
    return sorted(rows)


async def organisation_relationships(db, *, snapshot_id, org_npi: Any = None, org_ccn: Any = None,
                                     hco_record_key: Optional[str] = None, limit: int = PAGE_DEFAULT,
                                     cursor: Optional[Dict[str, str]] = None,
                                     diagnostic_allow_pending: bool = False) -> Dict[str, Any]:
    """Organisation-first read: find the organisation(s) in the staged snapshot by ORG_NPI / ORG_CCN_ID (or an
    HCO key), report identifier CONFLICTS, and return ONE PAGE of their relationships with explicit
    truncation. Every relationship row is preserved; nothing is merged or inferred, and no person-level data
    beyond the HCP key and relationship kind is returned. Advisory only (see module docstring).

    Pagination is keyset: `cursor` is the {hco, hcp, type} of the last row of the previous page. A page is at
    most PAGE_MAX rows. `truncated` is true whenever more rows exist beyond this page, and
    `total_relationships` is the exact count for the matched organisations. The query shape is deliberately
    two-step (rows of the matched organisations first, ordering second): a one-step ORDER BY (hcp, type) LIMIT
    makes the planner walk the whole unique index and filter, which measured over 2 minutes against 8.2M
    synthetic rows."""
    snap, root_id = await _eligible_observation_snapshot_id(
        db, snapshot_id, diagnostic_allow_pending=diagnostic_allow_pending)
    obs = sm.IqviaAffiliationObservation
    n, c = normalize_npi(org_npi), normalize_ccn(org_ccn)
    notes: List[str] = []
    if org_npi not in (None, "") and not n["valid"]:
        notes.append(n["reason"])
    if org_ccn not in (None, "") and not c["valid"]:
        notes.append(c["reason"])
    base = {"provenance": _provenance(snap), "determination": DETERMINATION,
            "requires_analyst_review": True, "affects_verification": False}
    if snap.status not in sm.SNAPSHOT_EFFECTIVE:
        base["diagnostic_only"] = "NOT_ELIGIBLE_FOR_VERIFICATION_SNAPSHOT_NOT_APPROVED"
    if hco_record_key is None and not n["valid"] and not c["valid"]:
        return {**base, "found_in_extract": False, "organisations": [], "organisations_omitted": 0,
                "conflicts": [], "notes": notes + ["NO_USABLE_IDENTIFIER"], "registry_candidate": None,
                "relationships": [], "total_relationships": 0, "returned": 0, "page_limit": 0,
                "truncated": False, "next_cursor": None}

    by_npi = await _hco_keys_for(db, root_id, _org_npi_expr(), [n["value"]]) if n["valid"] else []
    by_ccn = (await _hco_keys_for(db, root_id, _org_ccn_expr(), ccn_lookup_variants(c["value"]))) if c["valid"] else []
    keys = sorted(set(by_npi) | set(by_ccn) | ({hco_record_key} if hco_record_key else set()))

    conflicts: List[Dict[str, Any]] = []
    if by_npi and by_ccn and set(by_npi).isdisjoint(by_ccn):
        conflicts.append({"code": CONFLICT_NPI_CCN_DIFFERENT_ORGS,
                          "hco_keys_by_npi": len(by_npi), "hco_keys_by_ccn": len(by_ccn)})
    if len(by_npi) > 1:
        conflicts.append({"code": CONFLICT_IDENTIFIER_SHARED, "identifier": "ORG_NPI", "hco_keys": len(by_npi)})
    if len(by_ccn) > 1:
        conflicts.append({"code": CONFLICT_IDENTIFIER_SHARED, "identifier": "ORG_CCN_ID", "hco_keys": len(by_ccn)})

    orgs: List[Dict[str, Any]] = []
    orgs_omitted, total = 0, 0
    if keys:
        per = (await db.execute(select(
            obs.hco_record_key, func.count().label("rows"),
            func.count(func.distinct(obs.hcp_record_key)).label("hcps"),
            func.count(func.distinct(_org_npi_expr())).label("n_npi"),
            func.count(func.distinct(_org_ccn_expr())).label("n_ccn"))
            .where(obs.source_snapshot_id == root_id, obs.hco_record_key.in_(keys))
            .group_by(obs.hco_record_key).order_by(obs.hco_record_key))).all()
        for hco, rows_, hcps, n_npi, n_ccn in per[:MAX_ORGS_LISTED]:
            orgs.append({"hco_record_key": hco, "relationship_rows": rows_, "distinct_hcp_keys": hcps,
                         "distinct_org_npi": n_npi, "distinct_org_ccn": n_ccn})
            if n_npi > 1:
                conflicts.append({"code": CONFLICT_HCO_MULTIPLE_NPI, "hco_record_key": hco, "distinct_org_npi": n_npi})
            if n_ccn > 1:
                conflicts.append({"code": CONFLICT_HCO_MULTIPLE_CCN, "hco_record_key": hco, "distinct_org_ccn": n_ccn})
        orgs_omitted = max(0, len(per) - MAX_ORGS_LISTED)
        total = sum(r[1] for r in per)

    wanted = int(limit or PAGE_DEFAULT)
    page = max(1, min(wanted, PAGE_MAX))
    if wanted > PAGE_MAX:
        notes.append("LIMIT_CAPPED_AT_%d" % PAGE_MAX)
    rel_rows: List[Dict[str, Any]] = []
    truncated, next_cursor = False, None
    if keys:
        hit = (select(obs.hco_record_key, obs.hcp_record_key, obs.affiliation_type)
               .where(obs.source_snapshot_id == root_id, obs.hco_record_key.in_(keys))
               .cte("hit").prefix_with("MATERIALIZED"))
        q = select(hit.c.hco_record_key, hit.c.hcp_record_key, hit.c.affiliation_type)
        if cursor:
            q = q.where(tuple_(hit.c.hco_record_key, hit.c.hcp_record_key, hit.c.affiliation_type) >
                        tuple_(cursor["hco"], cursor["hcp"], cursor["type"]))
        q = q.order_by(hit.c.hco_record_key, hit.c.hcp_record_key, hit.c.affiliation_type).limit(page + 1)
        got = (await db.execute(q)).all()
        truncated = len(got) > page
        for hco, hcp, typ in got[:page]:
            rel = classify_relationship(typ)
            rel_rows.append({"hco_record_key": hco, "hcp_record_key": hcp, "affiliation_type": typ,
                             "kind": rel["kind"], "type_id": rel["type_id"]})
        if truncated and rel_rows:
            last = rel_rows[-1]
            next_cursor = {"hco": last["hco_record_key"], "hcp": last["hcp_record_key"],
                           "type": last["affiliation_type"]}

    registry = ((await resolve_organisation(db, org_npi=org_npi, org_ccn=org_ccn)).as_dict()
                if (n["valid"] or c["valid"]) else None)
    return {**base, "found_in_extract": bool(keys), "organisations": orgs, "organisations_omitted": orgs_omitted,
            "conflicts": conflicts, "notes": notes, "registry_candidate": registry, "relationships": rel_rows,
            "total_relationships": total, "returned": len(rel_rows), "page_limit": page,
            "truncated": truncated, "next_cursor": next_cursor}
