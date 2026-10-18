"""SAM.gov exclusion screening: vocabulary, sanitised failure reasons, search provenance, identity grouping.

WHY THIS EXISTS (investigation 2026-10-07)
------------------------------------------
Every SAM check DEV had recorded was "unavailable", and the evidence could not say why, nor separate four very
different situations: the screen did not complete, it completed and found nothing, it found a name that might be the
entity, or it found the entity by an identifier. This module gives those four situations names and records, beside
every SAM evidence item, WHAT was searched, HOW, and (for a failure) WHY, with no credential in any of it.

WHAT IT DOES NOT DO
-------------------
It changes no bucket rule, no disposition mapping and no policy. `disposition_for` reproduces the mapping that
`evidence_assembly._sam_disposition` has always applied (a golden test pins it); the new `outcome` is recorded
ALONGSIDE the disposition, never in its place. B1 and B4 therefore behave exactly as before for the same facts.

REGISTRATION IS NOT EXCLUSION
-----------------------------
SAM Entity Management (v3, "is this entity registered?") and SAM Exclusions (v4 / the daily public extract, "is
this party excluded?") answer different questions. Only the exclusion leg may decide an exclusion outcome.
Registration facts are carried as context and can never make an outcome ambiguous or excluded.
"""
from __future__ import annotations

import hashlib

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.ingestion.security import redact

PROVENANCE_SCHEMA = "sam-screening/1"

# ── The four screening outcomes ──────────────────────────────────────────────
INCOMPLETE = "INCOMPLETE"              # the exclusion check did not complete: NOT a clearance, NOT a finding
NO_HIT = "NO_HIT"                      # the check completed and returned no record for what was searched
POTENTIAL_MATCH = "POTENTIAL_MATCH"    # record(s) found, identity NOT confirmed (name only, or several identities)
IDENTIFIER_MATCH = "IDENTIFIER_MATCH"  # record(s) found by a strong identifier, ONE identity: identifier-matched, PENDING ADJUDICATION
CONFIRMED_MATCH = "CONFIRMED_MATCH"    # reserved for an analyst/QA adjudication. No automated path emits it.
SCREENING_OUTCOMES = (INCOMPLETE, NO_HIT, POTENTIAL_MATCH, IDENTIFIER_MATCH, CONFIRMED_MATCH)

OUTCOME_LABELS = {
    INCOMPLETE: "Check incomplete (not a clearance)",
    NO_HIT: "No hit",
    POTENTIAL_MATCH: "Potential match (identity not established)",
    IDENTIFIER_MATCH: "Identifier-matched, pending adjudication",
    CONFIRMED_MATCH: "Confirmed match (adjudicated)",
}


def matching_scope(extract_date, identifiers_tried):
    """What a result does and does not cover, so a NO_HIT is never read as a blanket clearance."""
    return {
        "as_of_extract_date": extract_date,
        "identifiers_tried": list(identifiers_tried),
        "name_matching": "exact after normalisation; no fuzzy, alias or former-name matching",
        "list": "SAM.gov public exclusions extract only (not LEIE, state lists or other sources)",
        "covers": "records present in that day's extract; says nothing about later changes",
    }

# ── Why a check was incomplete ───────────────────────────────────────────────
NO_KEY = "NO_KEY"
NOT_ROUTING = "NOT_ROUTING"            # api.sam.gov answers an empty 404: the request never reached the API
AUTH_REJECTED = "AUTH_REJECTED"        # 401 / 403
QUOTA = "QUOTA"                        # 429
UPSTREAM_ERROR = "UPSTREAM_ERROR"      # 5xx and other non-200
TRANSPORT = "TRANSPORT"                # timeout / connection
STRUCTURAL = "STRUCTURAL"              # 200 but not a readable result
NO_IDENTIFIER = "NO_IDENTIFIER"        # nothing usable to search on
NOT_QUERIED = "NOT_QUERIED"
FAILURE_CLASSES = (NO_KEY, NOT_ROUTING, AUTH_REJECTED, QUOTA, UPSTREAM_ERROR, TRANSPORT, STRUCTURAL,
                   NO_IDENTIFIER, NOT_QUERIED)

#: Identifiers strong enough to say "this is the same party". A name never is.
STRONG_IDENTIFIERS = ("uei", "npi", "cage")

_WS = re.compile(r"\s+")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize_reason(text: Optional[str], limit: int = 240) -> str:
    """A failure reason that is safe to store and show: credentials/URL secrets redacted, control characters and
    runs of whitespace collapsed, length bounded. Upstream response bodies are never echoed beyond `limit`."""
    cleaned = redact(text or "")
    cleaned = _CTRL.sub(" ", cleaned)
    cleaned = _WS.sub(" ", cleaned).strip()
    return cleaned[:limit]


def classify_failure(text: Optional[str]) -> str:
    """Map a connector error string (existing wording) to a failure class. Pure text, so no connector signature
    has to change and historical strings classify the same way."""
    t = (text or "").lower()
    if not t:
        return NOT_QUERIED
    if "api_key not set" in t or "key not set" in t:
        return NO_KEY
    if "no usable identifier" in t or "no uei" in t or "no legal name" in t or "no uei or legal name" in t:
        return NO_IDENTIFIER
    if "served no route" in t or "upstream routing" in t:
        return NOT_ROUTING
    if "rejected the api key" in t or "http 401" in t or "http 403" in t:
        return AUTH_REJECTED
    if "http 429" in t or "rate limit" in t or "quota" in t:
        return QUOTA
    if "malformed_response" in t or "source_error_body" in t:
        return STRUCTURAL
    if "timeout" in t or "timed out" in t or "connect" in t or "transport" in t:
        return TRANSPORT
    if "was not queried" in t or "not queried" in t:
        return NOT_QUERIED
    return UPSTREAM_ERROR


def failure_record(text: Optional[str]) -> Dict[str, str]:
    """`{failure_class, failure_reason}` for storage: classified from the raw text, then sanitised."""
    return {"failure_class": classify_failure(text), "failure_reason": sanitize_reason(text)}


# ── Outcome classification ───────────────────────────────────────────────────

def classify_outcome(*, answered: bool, record_count: int = 0, distinct_identities: int = 0,
                     matched_by: str = "name") -> Tuple[str, str]:
    """(outcome, reason_code) for ONE completed-or-not exclusion screen.

    - not answered -> INCOMPLETE
    - answered, no record -> NO_HIT
    - record(s), matched by a strong identifier and exactly ONE distinct identity -> IDENTIFIER_MATCH
    - any other record(s) (name only, or more than one distinct identity, or a strong identifier that maps to
      several identities) -> POTENTIAL_MATCH

    Several records for the SAME identity (one party excluded by several agencies) are NOT ambiguity.
    """
    if not answered:
        return INCOMPLETE, "CHECK_DID_NOT_COMPLETE"
    if record_count <= 0:
        return NO_HIT, "NO_RECORD_FOR_QUERY"
    if matched_by in STRONG_IDENTIFIERS and distinct_identities == 1:
        return IDENTIFIER_MATCH, f"SINGLE_IDENTITY_BY_{matched_by.upper()}"
    if distinct_identities > 1:
        return POTENTIAL_MATCH, "MULTIPLE_DISTINCT_IDENTITIES"
    return POTENTIAL_MATCH, "NAME_ONLY_MATCH" if matched_by not in STRONG_IDENTIFIERS else "IDENTIFIER_NOT_CONFIRMED"


def disposition_for(outcome: str, *, by_name: bool, insufficient: bool = False) -> str:
    """The evidence disposition the assembly has ALWAYS assigned for each situation (pinned by a golden test).
    Kept here only so the mapping lives next to the vocabulary; it is NOT a new rule."""
    if outcome == INCOMPLETE:
        return "INSUFFICIENT_EVIDENCE" if insufficient else "UNAVAILABLE"
    if outcome in (POTENTIAL_MATCH, IDENTIFIER_MATCH, CONFIRMED_MATCH):
        return "REVIEW"
    if outcome == NO_HIT:
        return "NOT_FOUND" if by_name else "PASS"
    raise ValueError(f"unknown outcome {outcome!r}")


# ── Search provenance ────────────────────────────────────────────────────────

def build_provenance(*, leg: str, channel: str, endpoint_version: Optional[str], identifier_used: str,
                     query_mode: str, page: Optional[int] = None, size: Optional[int] = None,
                     records_returned: Optional[int] = None, total_records: Optional[int] = None,
                     distinct_identities: Optional[int] = None, truncated: Optional[bool] = None,
                     dataset_anchor: Optional[Dict[str, Any]] = None,
                     failure: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """What was searched and how. NEVER contains the search VALUE (a name, NPI or UEI) or any credential: only the
    kind of identifier used, the mode, the paging and the counts. The value is already on the evidence row as
    `query_identifier`."""
    prov: Dict[str, Any] = {
        "schema": PROVENANCE_SCHEMA,
        "leg": leg,                              # "exclusion" | "registration"
        "channel": channel,                      # "LIVE_API" | "DAILY_EXTRACT"
        "endpoint_version": endpoint_version,
        "identifier_used": identifier_used,      # uei | npi | cage | name
        "query_mode": query_mode,                # exact | name_search
        "page": page, "size": size,
        "records_returned": records_returned, "total_records": total_records,
        "distinct_identities": distinct_identities, "truncated": truncated,
    }
    if dataset_anchor:
        prov["dataset_anchor"] = dict(dataset_anchor)
    if failure:
        prov.update({"failure_class": failure.get("failure_class"),
                     "failure_reason": sanitize_reason(failure.get("failure_reason"))})
    return {k: v for k, v in prov.items() if v is not None}


# ── Identity grouping that keeps every action ────────────────────────────────

@dataclass
class ExclusionAction:
    """One exclusion record. NEVER merged away: an identity listed by three agencies keeps three actions."""
    agency: str = ""
    program: str = ""
    exclusion_type: str = ""
    ct_code: str = ""
    active_date: str = ""
    termination_date: str = ""
    record_status: str = ""
    sam_number: str = ""
    cross_reference: str = ""
    has_additional_comments: bool = False


@dataclass
class ExclusionIdentity:
    identity_key: str
    key_basis: str                                  # uei | npi | cage | name_location
    classification: str = ""
    names: List[str] = field(default_factory=list)
    uei: List[str] = field(default_factory=list)
    npi: List[str] = field(default_factory=list)
    cage: List[str] = field(default_factory=list)
    states: List[str] = field(default_factory=list)
    actions: List[ExclusionAction] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        return len(self.actions)

    @property
    def provisional(self) -> bool:
        """True for name/state/ZIP grouping: a PROVISIONAL grouping of records, not a confirmed identity. Only a
        shared UEI, NPI or CAGE (directly or by chain) groups on a strong identifier, and even that is a grouping
        of SAM records, not a determination about any DocuAction entity."""
        return self.key_basis == "name_location"


def _add(seq: List[str], value: str) -> None:
    value = (value or "").strip()
    if value and value not in seq:
        seq.append(value)


class _UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _stable_key(basis: str, members) -> str:
    """Stable across calls and runs (a group's position in one call is not): the smallest strong identifier, or a
    hash of the weak name/location key. Two identities in different calls are the same only if this matches."""
    if basis != "name_location":
        vals = sorted({(m.get(basis) or "").strip().upper() for m in members if (m.get(basis) or "").strip()})
        return f"{basis}:{vals[0]}"
    m = members[0]
    raw = "|".join([(m.get("classification") or "").strip(), (m.get("name") or "").strip().lower(),
                    (m.get("last") or "").strip().lower(), (m.get("first") or "").strip().lower(),
                    (m.get("state") or "").strip().upper(), (m.get("zip") or "").strip()[:5]])
    return "name_location:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def group_identities(records: Iterable[Dict[str, str]], *, normalize_name) -> List[ExclusionIdentity]:
    """Group exclusion records into identities WITHOUT discarding any record.

    Two records are the same identity if they share a strong identifier (UEI, NPI or CAGE), directly or through a
    chain. Records with NO strong identifier group by normalised name + state + ZIP5 (a weak key, labelled
    `name_location`) and are never merged with an identifier-bearing group on a name alone.
    Every input record becomes an `ExclusionAction` on exactly one identity (a test asserts the counts add up).
    """
    recs = list(records)
    uf = _UnionFind(len(recs))
    seen: Dict[Tuple[str, str], int] = {}
    for i, r in enumerate(recs):
        for kind, col in (("uei", "uei"), ("npi", "npi"), ("cage", "cage")):
            v = (r.get(col) or "").strip().upper()
            if v:
                k = (kind, v)
                if k in seen:
                    uf.union(i, seen[k])
                else:
                    seen[k] = i
    weak: Dict[Tuple[str, str, str], int] = {}
    for i, r in enumerate(recs):
        if any((r.get(c) or "").strip() for c in ("uei", "npi", "cage")):
            continue
        if (r.get("classification") or "").strip() == "Individual" and not (r.get("name") or "").strip():
            # individuals carry First/Last, not Name: key on the person, never on an empty name
            who = re.sub(r"[^a-z0-9 ]+", "", f"{r.get('last') or ''} {r.get('first') or ''}".lower()).strip()
            k = ("person:" + who if who else "", (r.get("state") or "").strip().upper(),
                 (r.get("zip") or "").strip()[:5])
        else:
            k = (normalize_name(r.get("name") or ""), (r.get("state") or "").strip().upper(),
                 (r.get("zip") or "").strip()[:5])
        if not k[0]:
            continue
        if k in weak:
            uf.union(i, weak[k])
        else:
            weak[k] = i
    groups: Dict[int, List[int]] = {}
    for i in range(len(recs)):
        groups.setdefault(uf.find(i), []).append(i)

    out: List[ExclusionIdentity] = []
    for root, members in sorted(groups.items()):
        first = recs[members[0]]
        basis = ("uei" if any((recs[m].get("uei") or "").strip() for m in members) else
                 "npi" if any((recs[m].get("npi") or "").strip() for m in members) else
                 "cage" if any((recs[m].get("cage") or "").strip() for m in members) else "name_location")
        ident = ExclusionIdentity(identity_key=_stable_key(basis, [recs[m] for m in members]), key_basis=basis,
                                  classification=(first.get("classification") or "").strip())
        for m in members:
            r = recs[m]
            _add(ident.names, r.get("name") or "")
            _add(ident.uei, (r.get("uei") or "").upper())
            _add(ident.npi, r.get("npi") or "")
            _add(ident.cage, (r.get("cage") or "").upper())
            _add(ident.states, (r.get("state") or "").upper())
            ident.actions.append(ExclusionAction(
                agency=(r.get("agency") or "").strip(), program=(r.get("program") or "").strip(),
                exclusion_type=(r.get("exclusion_type") or "").strip(), ct_code=(r.get("ct_code") or "").strip(),
                active_date=(r.get("active_date") or "").strip(),
                termination_date=(r.get("termination_date") or "").strip(),
                record_status=(r.get("record_status") or "").strip(),
                sam_number=(r.get("sam_number") or "").strip(),
                cross_reference=(r.get("cross_reference") or "").strip(),
                has_additional_comments=bool((r.get("additional_comments") or "").strip())))
        out.append(ident)
    return out
