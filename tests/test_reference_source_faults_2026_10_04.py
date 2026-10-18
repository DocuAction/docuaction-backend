"""Seeded SOURCE faults must never produce a verification pass (Part B).

Deterministic fake HTTP responses; no network, no real key, no real
identifier. Each case is a fault the source itself can produce:

    OIG LEIE   HTTP 200 + HTML error page        was: indexed as an empty list,
               HTTP 200 + renamed header              every lookup "not excluded"
               HTTP 200 + truncated final row          for 24h
               HTTP 200 + header only
               HTTP 503 (outage)
    SAM.gov    HTTP 200 + error JSON body        was: "zero records" = clean
               HTTP 200 + unknown schema
               HTTP 429, timeout, HTTP 503
    NPPES      HTTP 200 + Errors body            already guarded; pinned here

The hard gate for every case: the result is UNAVAILABLE (success False),
never an affirmative "not excluded" / "not found".
"""
from __future__ import annotations

import httpx
import pytest

GOOD_HEADER = ("LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,"
               "CITY,STATE,ZIP,EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE")
GOOD_ROW = (",,,SYNTHETIC EXCLUDED ORG LLC,,,,1234567893,,1 SYNTHETIC WAY,TESTVILLE,TX,"
            "75000,1128a1,20200101,00000000,00000000,")


class _Resp:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code, self.text, self._payload = status_code, text, payload
        self.headers = {}
        self.content = text.encode() if text else b""

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def _leie_client(resp):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return resp
    return _Client


def _reset_leie(monkeypatch, resp, *, preload=None):
    from app.Tefca import connectors as c

    monkeypatch.setattr(c, "_LEIE_CACHE", dict(preload or {
        "loaded_at": 0.0, "by_npi": {}, "by_name": {}, "row_count": 0}))
    monkeypatch.setattr(c, "_LEIE_LOAD_LOCK", None)
    monkeypatch.setattr(c.httpx, "AsyncClient", _leie_client(resp))


# ── OIG LEIE ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("label,body", [
    ("html_error_page", "<html><body>\nService temporarily unavailable\n</body></html>\n"),
    ("renamed_header", GOOD_HEADER.replace("NPI", "NPI_NUMBER") + "\n" + GOOD_ROW + "\n"),
    ("truncated_final_row", GOOD_HEADER + "\n" + GOOD_ROW + "\n,,,SYNTHETIC CUT OFF ORG,,,"),
    ("header_only", GOOD_HEADER + "\n"),
    ("empty_body", ""),
])
async def test_leie_http_200_fault_body_is_unavailable_never_not_excluded(monkeypatch, label, body):
    from app.Tefca import connectors as c

    _reset_leie(monkeypatch, _Resp(200, body))
    assert await c._load_leie_csv() is False, label
    assert c._LEIE_CACHE["row_count"] == 0
    assert c._LEIE_CACHE["last_refusal"]["reason"]

    by_npi = await c.OIGLEIEConnector().lookup_by_npi("1234567893")
    by_name = await c.OIGLEIEConnector().lookup_by_name(last="", org="Synthetic Excluded Org LLC")
    for r in (by_npi, by_name):
        assert r.success is False, f"{label}: a source fault produced an affirmative answer"
        assert not (r.data or {}).get("excluded")


@pytest.mark.asyncio
async def test_leie_outage_is_unavailable(monkeypatch):
    from app.Tefca import connectors as c

    _reset_leie(monkeypatch, _Resp(503, "upstream outage"))
    r = await c.OIGLEIEConnector().lookup_by_npi("1234567893")
    assert r.success is False


@pytest.mark.asyncio
async def test_leie_good_body_loads_and_finds_the_seeded_exclusion(monkeypatch):
    from app.Tefca import connectors as c

    _reset_leie(monkeypatch, _Resp(200, GOOD_HEADER + "\n" + GOOD_ROW + "\n"))
    r = await c.OIGLEIEConnector().lookup_by_npi("1234567893")
    assert r.success is True and r.data["excluded"] is True
    assert c._LEIE_CACHE["schema_version"] == c.LEIE_SCHEMA_VERSION
    named = await c.OIGLEIEConnector().lookup_by_name(last="", org="Synthetic Excluded Org, L.L.C.")
    assert named.data["excluded"] is True     # normalized candidate: seeded signal not lost


@pytest.mark.asyncio
async def test_a_refused_reload_does_not_replace_a_good_index(monkeypatch):
    """A fault body must not overwrite the last good list with an empty one."""
    from app.Tefca import connectors as c

    good = {"loaded_at": 1.0, "row_count": 1, "by_name": {},
            "by_npi": {"1234567893": [{"reinstatement_date": "00000000",
                                       "exclusion_date": "20200101",
                                       "exclusion_type": "1128a1"}]}}
    _reset_leie(monkeypatch, _Resp(200, "<html>error</html>"), preload=good)
    assert await c._load_leie_csv() is False
    assert c._LEIE_CACHE["row_count"] == 1 and "1234567893" in c._LEIE_CACHE["by_npi"]


# ── SAM.gov ──────────────────────────────────────────────────────────────────

def _sam(monkeypatch, responder):
    from app.Tefca import connectors as c

    async def fake_get(url, params, headers, timeout=None, source="UNSPECIFIED"):
        return responder(url, source)
    monkeypatch.setattr(c, "_get_with_retry", fake_get)
    conn = c.SAMGovConnector()
    conn.api_key = "synthetic-test-key-not-real"
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize("label,payload", [
    ("error_object", {"error": {"code": "OVER_RATE_LIMIT", "message": "limit exceeded"}}),
    ("message_only", {"message": "Internal error"}),
    ("unknown_schema", {"data": {"items": []}}),
])
async def test_sam_http_200_error_body_is_never_a_clean_screen(monkeypatch, label, payload):
    conn = _sam(monkeypatch, lambda url, source: _Resp(200, payload=payload))

    exc = await conn.check_exclusions(legal_name="Synthetic Org")
    assert exc.success is False, f"{label}: an error body read as 'not excluded'"
    name = await conn.lookup_by_name("Synthetic Org")
    assert name.success is False, f"{label}: an error body read as 'not registered'"
    uei = await conn.lookup_by_uei("SYNTHETICUEI1")
    assert uei.success is False

    combined = await conn.verify(legal_name="Synthetic Org")
    assert combined.success is False       # both legs faulted: nothing is known


@pytest.mark.asyncio
async def test_sam_exclusion_leg_fault_with_good_registration_is_unknown_not_clear(monkeypatch):
    """Registration answers, the exclusions leg returns an error body: the
    combined result must say excluded_known=False (the evidence layer then
    assembles UNAVAILABLE, never PASS)."""
    def responder(url, source):
        if source == "SAM_GOV_EXCLUSIONS":
            return _Resp(200, payload={"error": "throttled"})
        return _Resp(200, payload={"totalRecords": 0, "entityData": []})
    conn = _sam(monkeypatch, responder)
    r = await conn.verify(legal_name="Synthetic Org")
    assert r.success is True
    assert r.data["excluded_known"] is False and r.data["exclusions_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["http_429", "timeout", "http_503"])
async def test_sam_429_timeout_and_outage_are_unavailable(monkeypatch, fault):
    from app.Tefca import connectors as c

    async def fake_get(url, params, headers, timeout=None, source="UNSPECIFIED"):
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        raise c.RetryableHTTPError(f"HTTP {429 if fault == 'http_429' else 503} from {url}")
    monkeypatch.setattr(c, "_get_with_retry", fake_get)
    conn = c.SAMGovConnector()
    conn.api_key = "synthetic-test-key-not-real"
    r = await conn.verify(legal_name="Synthetic Org")
    assert r.success is False


@pytest.mark.asyncio
async def test_sam_genuine_empty_result_is_still_an_answer(monkeypatch):
    """The structural check must not turn a real 'no records' into a fault."""
    def responder(url, source):
        if source == "SAM_GOV_EXCLUSIONS":
            return _Resp(200, payload={"totalRecords": 0, "excludedEntity": []})
        return _Resp(200, payload={"totalRecords": 0, "entityData": []})
    conn = _sam(monkeypatch, responder)
    r = await conn.verify(legal_name="Synthetic Org")
    assert r.success is True and r.data["excluded"] is False and r.data["excluded_known"] is True


@pytest.mark.asyncio
async def test_sam_seeded_exclusion_is_found(monkeypatch):
    def responder(url, source):
        if source == "SAM_GOV_EXCLUSIONS":
            return _Resp(200, payload={"totalRecords": 1, "excludedEntity": [
                {"exclusionIdentification": {"exclusionName": "SYNTHETIC ORG"}}]})
        return _Resp(200, payload={"totalRecords": 0, "entityData": []})
    conn = _sam(monkeypatch, responder)
    r = await conn.verify(uei="SYNTHETICUEI1")
    assert r.data["excluded"] is True and r.data["excluded_known"] is True


# ── NPPES (already guarded; pinned so it stays that way) ─────────────────────

def test_nppes_http_200_errors_body_is_not_a_lookup_result():
    from app.Tefca.connectors import _nppes_structural_check

    assert _nppes_structural_check({"Errors": [{"description": "x"}]})
    assert _nppes_structural_check({"result_count": 0}) is not None
    assert _nppes_structural_check({"result_count": 0, "results": []}) is None
