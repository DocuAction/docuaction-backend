"""Does the bulk run need the live SAM API for every record? A synthetic batch, traced and counted (2026-11-26).

Owner: "Confirm the November bulk run uses the daily SAM public exclusions extract rather than requiring a live API
call for every record. Trace the actual classification path and demonstrate extract screening, edition attribution
and mandatory-source handling on a small synthetic batch. Report any live API calls still made and their quota
impact. ... Keep registration status separate."

CHARACTERIZATION: these tests pin what the code does on origin/main e9064c2. Nothing here changes behaviour.

The batch is seven promoted, active, non-Treatment organisations run through the REAL bulk writer, evidence
assembly and connectors under the DEV-active configuration (`configuration`, from the mandatory-SAM cases file):
three submit an NPI and are clean, one submits an NPI the extract lists, one submits an NPI and the extract lists
its NAME on a row with no NPI, two submit no NPI (one of them listed in the extract by name). Every HTTP request a
connector makes goes through one instrumented helper that records source, endpoint and the organisation asked
about. Nothing reaches a network.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.Tefca import connectors, sam_extract
from app.Tefca.models import TEFCADimensionEvidence
from app.reports.data import october_review_loader as ld
from app.tefca_registry import models as reg
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import source_policy as sp
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401 - fixtures
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)
from test_mandatory_sam_policy_cases_2026_11_26 import _World, _policy_text, configuration  # noqa: F401

pytestmark = pytest.mark.asyncio

ARC = "9.99.782.1"
#: record -> submitted NPI ("" = none) and how the synthetic extract lists it.
BATCH = [
    ("npi_clean_1", "1234567893", None),
    ("npi_clean_2", "1245319599", None),
    ("npi_clean_3", "1982916078", None),
    ("npi_extract_candidate_by_npi", "1881659506", "npi"),
    ("npi_extract_candidate_by_name", "1790785996", "name"),
    ("name_only_clean", "", None),
    ("name_only_extract_candidate_by_name", "", "name"),
]
V3 = "/entity-information/v3/entities"
V4 = "/entity-information/v4/exclusions"
NO_KEY_CLASS = "NO_KEY"
EXTRACT_FILE = "SAM_Exclusions_Public_Extract_V2_SYNTHETIC.ZIP"


@pytest.fixture
async def batch(configuration, monkeypatch):
    db = configuration.db
    rows = make_rows(len(BATCH), arc=ARC)
    for row, (label, npi, _listed) in zip(rows, BATCH):
        row.update({"NPI": npi, "purposesofuse": "T-PYMNT", "active": "1", "name": f"{SYN} TRACE {label}"})
    intake_id, _ = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)

    from app.Tefca.entity_resolution import resolve_entity

    async def _resolve_all(refs):
        return {ref: await resolve_entity(db, ref) for ref in dict.fromkeys(refs)}
    monkeypatch.setattr(ap, "_resolve_all", _resolve_all)
    world = _World().install(monkeypatch)
    world.names = {npi: row["name"] for row, (_l, npi, _x) in zip(rows, BATCH) if npi}
    # The synthetic extract: one row carrying an NPI, two rows found only by their normalised name (no NPI).
    by_npi, by_name = {}, {}
    for row, (label, npi, listed) in zip(rows, BATCH):
        entry = {"Classification": "Firm", "Name": row["name"], "NPI": npi if listed == "npi" else "",
                 "Exclusion Type": "Ineligible", "Excluding Agency": "HHS", "Record Status": "Active"}
        if listed == "npi":
            by_npi[npi] = [entry]
        elif listed == "name":
            by_name[connectors.normalize_org_name(row["name"])] = [entry]
    sam_extract._SAM_CACHE["by_npi"], sam_extract._SAM_CACHE["by_name"] = by_npi, by_name

    # THE INSTRUMENT: every request any connector makes, with its endpoint and the organisation it asks about.
    requests = []
    inner = connectors._get_with_retry

    async def _counted(url, params=None, headers=None, timeout=None, source="UNSPECIFIED"):
        parsed = httpx.URL(url)
        params = dict(params or {})
        requests.append(SimpleNamespace(
            host=parsed.host, path=parsed.path, source=source, timeout=timeout,
            about=params.get("legalBusinessName") or params.get("q") or params.get("number"),
            params=sorted(k for k in params if k != "api_key")))
        return await inner(url, params=params, headers=headers, timeout=timeout, source=source)
    monkeypatch.setattr(connectors, "_get_with_retry", _counted)
    labels = [label for label, _n, _x in BATCH]
    return SimpleNamespace(db=db, intake_id=intake_id, rows=rows, refs=[r["id"] for r in rows], world=world,
                           requests=requests, labels=labels, configuration=configuration,
                           names={label: row["name"] for label, row in zip(labels, rows)})


async def _run(b):
    result = await ap.verify_and_classify(b.db, b.refs, intake_id=b.intake_id, actor=SYN)
    assert result["verified"] == len(BATCH), result
    out = {}
    for label, ref in zip(b.labels, b.refs):
        outcome = next(o for o in result["outcomes"] if o["entity_ref"] == ref)
        review = (await b.db.execute(select(reg.ReviewRecord).where(
            reg.ReviewRecord.review_id == outcome["review_id"]))).scalars().one()
        sources = review.verification_results["classifier_input"]["sources"]
        evidence = {row.source: row for row in (await b.db.execute(
            select(TEFCADimensionEvidence).where(
                TEFCADimensionEvidence.entity_id == outcome["entity_id"],
                TEFCADimensionEvidence.evidence_dimension == "EXCLUSION_REVOCATION"))).scalars().all()}
        name = b.names[label]
        api = Counter(r.path for r in b.requests if r.host == "api.sam.gov" and r.about == name)
        out[label] = SimpleNamespace(
            api_v3=api.get(V3, 0), api_v4=api.get(V4, 0),
            sam_gov=(sources.get("sam_gov") or {}).get("status"),
            sam_extract=(sources.get("sam_extract") or {}).get("status"),
            bucket=review.classification_bucket, rule=review.classification_rule,
            rule_version=review.classification_rule_version, rationale=review.classification_rationale,
            extract_row=evidence.get("SAM_EXTRACT_SUPPLEMENTARY"), api_row=evidence.get("SAM_GOV"),
            sources=sources,
            d3_rationale=next((d.get("rationale") for d in review.verification_results.get("dimensions") or []
                               if isinstance(d, dict) and d.get("dimension") == "EXCLUSION_REVOCATION"), None))
    return out


B1 = ("B1", "RULE-001", 4)
POLICY_B3 = ("B3", "SOURCE-POLICY-M4", 1)
UNMATCHED_B3 = ("B3", "DEFAULT-UNMATCHED", 0)
WITH_NPI = [label for label, npi, _x in BATCH if npi]
NAME_ONLY = [label for label, npi, _x in BATCH if not npi]


def _today():
    return datetime.now(timezone.utc).date().isoformat()


async def test_api_not_routing__the_extract_screens_every_record_and_the_api_is_still_asked_for_each(batch):
    """THE CURRENT STATE: api.sam.gov serves no route. Every record is screened against the loaded extract and
    that screen - not the API - decides the mandatory exclusion requirement. The API is nevertheless asked for
    EVERY record: two requests when an NPI was submitted, three when not. Nothing remembers that it just failed."""
    b = batch
    out = await _run(b)

    # 1. LIVE API REQUESTS. No UEI is ever delivered, so both legs search by NAME. A record with no NPI repeats
    #    the SAME v3 registration search a second time (`sam_name`, evidence_service.gather_sources).
    for label in WITH_NPI:
        assert (out[label].api_v3, out[label].api_v4) == (1, 1), label
    for label in NAME_ONLY:
        assert (out[label].api_v3, out[label].api_v4) == (2, 1), label
    sam = [r for r in b.requests if r.host == "api.sam.gov"]
    assert len(sam) == 2 * len(WITH_NPI) + 3 * len(NAME_ONLY) == 16
    assert {(r.path, tuple(r.params)) for r in sam} == {
        (V3, ("includeSections", "legalBusinessName", "page", "size")),       # registration, by legal name
        (V4, ("page", "q", "size"))}                                          # exclusions, free text = the name
    assert {r.timeout for r in sam} == {None}             # the helper's default applies: SOURCE_TIMEOUT_SECONDS
    assert connectors.SOURCE_TIMEOUT_SECONDS == 30.0 and connectors.RETRY_ATTEMPTS == 3
    # The ONLY other outbound requests are NPPES, one per submitted NPI. The extract costs none per record.
    assert Counter(r.host for r in b.requests) == {"api.sam.gov": 16, "npiregistry.cms.hhs.gov": len(WITH_NPI)}

    # 2. THE API RESULT IS RECORDED AS WHAT IT IS: unavailable, class NOT_ROUTING - never clear, never a finding.
    for label, f in out.items():
        assert f.sam_gov == "unavailable"
        assert f.api_row.disposition == "UNAVAILABLE"
        assert f.api_row.normalized_values["failure_class"] == "NOT_ROUTING"
        assert f.api_row.normalized_values["outcome"] == "INCOMPLETE"
        assert ld._evidence_cell("sam", f.api_row) == f"UNAVAILABLE | retrieved {_today()}"

    # 3. THE EXTRACT SCREEN, per record, with its edition on the evidence row and in the report cell.
    expected_screen = {
        "npi_clean_1": ("PASS", "clear", ["npi", "normalized_business_name"], []),
        "npi_clean_2": ("PASS", "clear", ["npi", "normalized_business_name"], []),
        "npi_clean_3": ("PASS", "clear", ["npi", "normalized_business_name"], []),
        "npi_extract_candidate_by_npi": ("REVIEW", "possible_match", ["npi", "normalized_business_name"], ["npi"]),
        "npi_extract_candidate_by_name": ("REVIEW", "possible_match", ["npi", "normalized_business_name"],
                                          ["normalized_business_name"]),
        "name_only_clean": ("PASS", "clear", ["normalized_business_name"], []),
        "name_only_extract_candidate_by_name": ("REVIEW", "possible_match", ["normalized_business_name"],
                                                ["normalized_business_name"]),
    }
    for (label, npi, _listed) in BATCH:
        f, row = out[label], out[label].extract_row
        disposition, state, tried, matched = expected_screen[label]
        original = row.original_values
        assert (row.source, row.disposition, f.sam_extract) == ("SAM_EXTRACT_SUPPLEMENTARY", disposition, state)
        assert row.dataset_version_anchor == "GSA-Public-Extract-V2" == sam_extract.API_VERSION
        assert row.query_identifier == (f"npi={npi}" if npi else f"org={b.names[label]}")
        assert (original["match_methods"], original["match_methods_matched"]) == (tried, matched)
        assert (original["extract_filename"], original["publication_date"], original["list_rows_searched"],
                original["source_dataset"]) == (EXTRACT_FILE, _today(), 160_000, "SAM_Exclusions_Public_Extract_V2")
        assert original["candidate_count"] == (1 if disposition == "REVIEW" else 0)
        cell = ld._sam_extract_cell(row)
        head = ("CANDIDATE MATCH (1) - NOT a confirmed exclusion; analyst determination required"
                if disposition == "REVIEW" else "No candidate match in the extract")
        assert cell == (f"{head} | edition {_today()} ({EXTRACT_FILE}) | sha256 000000000000 | checked {_today()} | "
                        f"supplementary to, not a substitute for, the live SAM.gov API check")

    # 4. MANDATORY-SOURCE HANDLING AND THE RESULT.
    policies = await sp.load_active_policies(b.db)
    for label in ("npi_clean_1", "npi_clean_2", "npi_clean_3"):
        # API did not answer + extract screened clear = the requirement is met: B1 stands, the policy says nothing.
        assert (out[label].bucket, out[label].rule, out[label].rule_version) == B1
        assert sp.apply_source_policy("B1", "RULE-001", out[label].sources, {"has_submitted_npi": True},
                                      policies) == ("B1", "RULE-001", None, None)
    for label in ("npi_extract_candidate_by_npi", "npi_extract_candidate_by_name"):
        assert (out[label].bucket, out[label].rule, out[label].rule_version) == POLICY_B3
        assert out[label].rationale == _policy_text("EXCLUSION_POSSIBLE_MATCH_PENDING", b.configuration)
    for label in NAME_ONLY:
        # No NPI: NPPES, CMS enrollment and CMS revocation cannot be asked, so no B1/B2 rule matches and the
        # record is B3 by the unmatched default BEFORE the policy is consulted (it acts on B1/B2 only). That is
        # so for the clean record and for the one the extract lists: the candidate is on the evidence row and
        # in the report cell, and is NOT named in the stored rationale.
        f = out[label]
        assert (f.bucket, f.rule, f.rule_version) == UNMATCHED_B3
        assert (f.sources["nppes"]["status"], f.sources["pecos"]["status"],
                f.sources["cms_revocation"]["status"]) == ("unavailable", "unavailable", "unavailable")
        assert "possible" not in f.rationale.lower() and "candidate" not in f.rationale.lower()


async def test_api_answering__the_same_number_of_requests_and_registration_decides_nothing(batch):
    """IF THE API WORKED: the request count per record is the same (2 with an NPI, 3 without). The registration
    answer (v3) is stored on the SAM evidence row and reaches no rule and no policy: the buckets are what they
    were with the API down.

    A record with NO NPI is still `sam_gov: unavailable` with the API working: its SAM evidence item is built from
    the extra registration-only name search (`sam_name`), not from the combined check whose exclusion leg DID
    answer, and a registration-only miss is never an exclusion answer. So for such a record only the extract can
    answer the exclusion question, API or no API."""
    b = batch
    b.world.sam_api = "clear"
    out = await _run(b)
    for label in WITH_NPI:
        assert (out[label].api_v3, out[label].api_v4) == (1, 1), label
    for label in NAME_ONLY:
        assert (out[label].api_v3, out[label].api_v4) == (2, 1), label
    assert sum(1 for r in b.requests if r.host == "api.sam.gov") == 16
    for label, f in out.items():
        registration = f.api_row.original_values
        assert (registration["found"], registration["registration_status"], registration["registration_current"],
                registration["matched_by"]) == (True, "Active", True, "name")
        if label in WITH_NPI:
            assert f.sam_gov == "clear", label
            assert f.api_row.normalized_values["screening_leg"] == "exclusion_and_registration"
            assert f.api_row.rule_applied == "SAM_DEBARMENT_CHECK"
            assert ld._evidence_cell("sam", f.api_row) == f"NOT_FOUND -> clear | retrieved {_today()}"
        else:
            assert f.sam_gov == "unavailable", label
            assert f.api_row.normalized_values["screening_leg"] == "registration_only"
            assert (f.api_row.disposition, f.api_row.rule_applied) == ("NOT_FOUND", "SAM_ORG_LEVEL_CHECK_NO_UEI")
            assert "(SAM registration-only search; the exclusion question was NOT screened)" in ld._evidence_cell(
                "sam", f.api_row)
        # The classifier input carries ONE `sam_gov` state and no registration field at all.
        assert sorted(k for k in f.sources if "sam" in k) == ["sam_extract", "sam_gov"]
        assert "registration" not in str(f.sources).lower()
    assert [(out[x].bucket, out[x].rule, out[x].rule_version) for x in b.labels] == [
        B1, B1, B1, POLICY_B3, POLICY_B3, UNMATCHED_B3, UNMATCHED_B3]


async def test_extract_stale__fails_closed_and_the_api_being_down_then_blocks_b1(batch):
    """A loaded extract whose edition is more than two days old is not screened against: the connector's own
    staleness rule returns "stale_source", the state is `unavailable`, and with the API not answering either the
    mandatory requirement is NOT met - every record that would have been B1 is B3."""
    b = batch
    stale = datetime.now(timezone.utc).date() - timedelta(days=5)
    sam_extract._SAM_CACHE["publication_date"] = stale
    out = await _run(b)
    for label, f in out.items():
        assert f.sam_extract == "unavailable" and f.extract_row.disposition == "UNAVAILABLE"
        assert f.extract_row.note.startswith(
            f"SAM_EXTRACT unavailable: stale_source: extract edition {stale.isoformat()} ")
        assert ld._sam_extract_cell(f.extract_row).startswith(
            "Extract screen unavailable - not performed: SAM_EXTRACT unavailable: stale_source: ")
    for label in WITH_NPI:
        assert (out[label].bucket, out[label].rule, out[label].rule_version) == POLICY_B3
        assert out[label].rationale == _policy_text("SOURCE_NOT_COMPLETED_SAM_GOV", b.configuration)
    for label in NAME_ONLY:
        assert (out[label].bucket, out[label].rule, out[label].rule_version) == UNMATCHED_B3


def test_no_rule_and_no_policy_asks_for_sam_registration():
    """Registration is a separate question and nothing classifies on it: the composed DEV rule set reads `sam_gov`
    only as a DISQUALIFIER (excluded / debarred / not_found / failed / possible_match keep a record out of B1/B2;
    excluded / debarred make B4; possible_match makes B3), never as a requirement, and the one policy row names
    `sam_gov` with the acceptable answers verified / clear."""
    from app.tefca_registry import rule_configuration as rcfg
    from test_mandatory_sam_policy_cases_2026_11_26 import COMPONENTS
    rules = rcfg.compose(set(COMPONENTS))
    sam_conditions = sorted({(r["rule_code"], clause, c["status"])
                             for r in rules for clause, items in r["conditions"].items()
                             for c in items if isinstance(c, dict) and c.get("source") == "sam_gov"})
    disqualifiers = ["debarred", "excluded", "not_found", "possible_match"]
    assert sam_conditions == sorted(
        [("RULE-001", "none_of", x) for x in disqualifiers]
        + [("RULE-002", "none_of", x) for x in disqualifiers + ["failed"]]
        + [("RULE-003", "none_of", x) for x in disqualifiers]
        + [("RULE-005", "any_of", "debarred"), ("RULE-005", "any_of", "excluded"),
           ("RULE-008", "any_of", "possible_match")])
    assert not any(c.get("source") == "sam_gov" for r in rules for c in r["conditions"].get("all_of", [])
                   if isinstance(c, dict))                      # no rule REQUIRES a SAM answer of any kind
    assert "registration" not in str([r["conditions"] for r in rules]).lower()
    assert sp.ACCEPTABLE_STATES == {"verified", "clear"}
    assert [p["source"] for p in sp.PROPOSED_POLICY_M1] == ["sam_gov"]


class _Client:
    """Stands in for the shared HTTP client: answers every GET with one status and counts the attempts."""

    def __init__(self, status):
        self.status, self.attempts = status, 0

    async def get(self, url, params=None, headers=None, timeout=None):
        self.attempts += 1
        return httpx.Response(self.status, request=httpx.Request("GET", url), content=b"")


@pytest.mark.parametrize("status,attempts_per_leg,reason_starts", [
    (404, 1, "SAM_GOV unavailable: SAM_GOV unavailable: HTTP 404 with an empty body"),   # today: not retried
    (429, 3, "SAM_GOV unavailable: "),                                 # rate limited: retried, then unavailable
    (503, 3, "SAM_GOV unavailable: "),
])
async def test_retries_per_leg__a_404_is_one_request_a_429_or_5xx_is_three(monkeypatch, status, attempts_per_leg,
                                                                            reason_starts):
    """The retry rule of `connectors._get_with_retry`, measured: an empty 404 costs ONE request per leg; a 429 or
    a 5xx is tried RETRY_ATTEMPTS (3) times per leg. A failure of the registration leg does not stop the
    exclusion leg (`SAMGovConnector.verify` always asks both). Nothing carries a failure over to the next call."""
    import tenacity
    client = _Client(status)
    monkeypatch.setenv("SAM_GOV_API_KEY", "synthetic-key-not-a-credential")
    monkeypatch.setenv("TEFCA_RATE_LIMIT_SAM_GOV_RPS", "1000")
    monkeypatch.setenv("TEFCA_RATE_LIMIT_SAM_GOV_EXCLUSIONS_RPS", "1000")
    monkeypatch.setattr(connectors, "_RATE_LIMITERS", {})
    monkeypatch.setattr(connectors, "_shared_http_client", lambda: client)
    monkeypatch.setattr(connectors, "wait_exponential", lambda **_k: tenacity.wait_none())     # no real sleeping
    connector = connectors.SAMGovConnector()
    for call in (1, 2, 3):                                  # three records in a row: each pays the full cost again
        result = await connector.verify(legal_name=f"{SYN} TRACE retry {call}")
        assert result.success is False and result.error.startswith(reason_starts), result.error
        assert client.attempts == call * 2 * attempts_per_leg


async def test_with_no_api_key_configured_no_sam_api_request_is_made_and_the_outcomes_are_the_same(batch, monkeypatch):
    """The one existing setting that stops the per-record API attempts: with SAM_GOV_API_KEY unset the connector
    returns "unavailable" before any request. The extract still screens every record and every bucket is what it
    was with the API not routing. What changes is the recorded reason for the API being unavailable."""
    b = batch
    monkeypatch.delenv("SAM_GOV_API_KEY")
    out = await _run(b)
    assert [r for r in b.requests if r.host == "api.sam.gov"] == []
    for label, f in out.items():
        assert f.sam_gov == "unavailable" and f.api_row.disposition == "UNAVAILABLE"
        assert "SAM_GOV_API_KEY not set" in f.api_row.note
        assert f.api_row.normalized_values["failure_class"] == NO_KEY_CLASS
    assert [(out[x].bucket, out[x].rule, out[x].rule_version) for x in b.labels] == [
        B1, B1, B1, POLICY_B3, POLICY_B3, UNMATCHED_B3, UNMATCHED_B3]
    assert [out[x].sam_extract for x in b.labels] == [
        "clear", "clear", "clear", "possible_match", "possible_match", "clear", "possible_match"]
