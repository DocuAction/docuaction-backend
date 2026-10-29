"""SAM public exclusions extract -- minimal connector proposal (2026-10-28).

Prepared for review, NOT activated: every test here either (a) proves the
flag-off default never fetches anything, or (b) drives the connector
against an in-memory, synthetic/real-but-already-public cache -- no live
network call. See app/Tefca/sam_extract.py's module docstring.

The positive-control NPI below (1841517737) is a REAL row already present
in the preserved public GSA extract at
qa-evidence/2026-10-28-october-readiness/sam_extract_preserved/ -- picked
from that file, not fabricated, per the directive. It is already public,
federally-published exclusion data; only the bare NPI digits are used here,
no name or location.
"""
from __future__ import annotations

import io
import time
import zipfile

import httpx
import pytest

GOOD_HEADER = ("Classification,Name,Prefix,First,Middle,Last,Suffix,Address 1,Address 2,"
               "Address 3,Address 4,City,State / Province,Country,Zip Code,Open Data Flag,"
               "Blank (Deprecated),Unique Entity ID,Exclusion Program,Excluding Agency,"
               "CT Code,Exclusion Type,Additional Comments,Active Date,Termination Date,"
               "Record Status,Cross-Reference,SAM Number,CAGE,NPI,Creation_Date")
GOOD_ROW = (",,,SYNTHETIC,TEST,ORG,,,,,,,,USA,,,,,,HHS,,Prohibition/Restriction,,"
            "2020-01-01,Indefinite,Active,,synthetic-sam-number,,1841517737,2020-01-01")


def _zip_of(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("extract.csv", csv_text)
    return buf.getvalue()


class _Resp:
    def __init__(self, status_code=200, content=b"", json_payload=None):
        self.status_code = status_code
        self.content = content
        self.text = content.decode(errors="replace") if isinstance(content, bytes) else content
        self.headers = {"Last-Modified": "Wed, 28 Oct 2026 09:39:36 GMT"}
        self.request = None
        self._json_payload = json_payload

    def json(self):
        if self._json_payload is None:
            raise ValueError("not JSON")
        return self._json_payload


def _client_returning(listfiles_resp, download_resp):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **k):
            if "listfiles" in url:
                return listfiles_resp
            return download_resp
    return _Client


def _reset_cache(monkeypatch, cache):
    from app.Tefca import sam_extract as s
    monkeypatch.setattr(s, "_SAM_CACHE", cache)
    monkeypatch.setattr(s, "_SAM_LOAD_LOCK", None)


# ── flag-off default ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flag_off_by_default_never_fetches_anything(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.delenv(s.ENV_FLAG, raising=False)

    class _ExplodingClient:
        def __init__(self, *a, **k):
            raise AssertionError("no HTTP client should be constructed when the flag is off")

    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False
    assert "not enabled" in r.error.lower() or s.ENV_FLAG in (r.error or "")


@pytest.mark.asyncio
async def test_flag_off_also_blocks_name_lookup(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.delenv(s.ENV_FLAG, raising=False)
    monkeypatch.setattr(httpx, "AsyncClient",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch when flag is off")))

    r = await s.SAMExtractConnector().lookup_by_name("Synthetic Test Org")
    assert r.success is False


# ── schema preflight (pure) ──────────────────────────────────────────────────

def test_schema_problem_accepts_the_real_header():
    from app.Tefca.sam_extract import sam_extract_schema_problem

    assert sam_extract_schema_problem(GOOD_HEADER.split(",")) is None


def test_schema_problem_rejects_missing_columns():
    from app.Tefca.sam_extract import sam_extract_schema_problem

    bad = [c for c in GOOD_HEADER.split(",") if c != "NPI"]
    problem = sam_extract_schema_problem(bad)
    assert problem and "NPI" in problem


def test_schema_problem_rejects_empty_header():
    from app.Tefca.sam_extract import sam_extract_schema_problem

    assert sam_extract_schema_problem([]) is not None


# ── positive / negative controls (in-memory cache, flag on) ─────────────────

@pytest.mark.asyncio
async def test_positive_control_real_excluded_npi_is_a_candidate_not_an_exclusion(monkeypatch):
    """1841517737 is a real row in the preserved extract. A hit must be
    `candidate_match`, and the result must never carry an `excluded` key --
    that word is reserved for LEIE/the live SAM API's own exclusion finding.
    """
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, {
        "loaded_at": time.time(), "row_count": 1,
        "by_npi": {"1841517737": [{"Name": "", "NPI": "1841517737"}]},
        "by_name": {}, "edition_date": "Wed, 28 Oct 2026 09:39:36 GMT",
        "sha256": "deadbeef", "source_url": "https://sam.gov/...",
    })

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is True
    assert r.data["candidate_match"] is True
    assert r.data["candidate_count"] == 1
    assert "excluded" not in r.data
    assert r.data["extract_sha256"] == "deadbeef"
    assert r.data["list_rows_searched"] == 1


@pytest.mark.asyncio
async def test_negative_control_known_good_npi_is_no_match(monkeypatch):
    """1891430989 -- the known-good, actively-enrolled NPI already confirmed
    real via PECOS elsewhere in this engagement -- must resolve to no match.
    """
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, {
        "loaded_at": time.time(), "row_count": 1,
        "by_npi": {"1841517737": [{"Name": "", "NPI": "1841517737"}]},
        "by_name": {}, "edition_date": None, "sha256": "deadbeef", "source_url": None,
    })

    r = await s.SAMExtractConnector().lookup_by_npi("1891430989")
    assert r.success is True
    assert r.data["candidate_match"] is False
    assert r.data["candidate_count"] == 0


@pytest.mark.asyncio
async def test_malformed_npi_is_rejected_before_any_cache_lookup(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    r = await s.SAMExtractConnector().lookup_by_npi("0000000001")  # fails Luhn
    assert r.success is False


# ── malformed-body rejection (fetch + index path) ────────────────────────────

@pytest.mark.asyncio
async def test_malformed_zip_body_is_refused_not_indexed_as_clean(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, {"loaded_at": 0.0, "row_count": 0, "by_npi": {}, "by_name": {},
                               "edition_date": None, "sha256": None, "source_url": None})

    listfiles_resp = _Resp(200, json_payload={"files": [{"name": "SAM_Exclusions_Public_Extract_V2_26301.ZIP"}]})
    not_a_zip = _Resp(200, content=b"<html>not a zip</html>")
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(listfiles_resp, not_a_zip))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False
    assert s._SAM_CACHE["row_count"] == 0  # previously-good index, if any, must be untouched


@pytest.mark.asyncio
async def test_wrong_schema_csv_is_refused(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, {"loaded_at": 0.0, "row_count": 0, "by_npi": {}, "by_name": {},
                               "edition_date": None, "sha256": None, "source_url": None})

    bad_csv = "COMPLETELY,WRONG,HEADER\n1,2,3\n"
    listfiles_resp = _Resp(200, json_payload={"files": [{"name": "SAM_Exclusions_Public_Extract_V2_26301.ZIP"}]})
    wrong_schema = _Resp(200, content=_zip_of(bad_csv))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(listfiles_resp, wrong_schema))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False
    assert s._SAM_CACHE["row_count"] == 0


@pytest.mark.asyncio
async def test_good_zip_body_loads_and_answers_positive_control(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, {"loaded_at": 0.0, "row_count": 0, "by_npi": {}, "by_name": {},
                               "edition_date": None, "sha256": None, "source_url": None})

    good_csv = GOOD_HEADER + "\n" + GOOD_ROW + "\n"
    listfiles_resp = _Resp(200, json_payload={"files": [{"name": "SAM_Exclusions_Public_Extract_V2_26301.ZIP"}]})
    good_zip = _Resp(200, content=_zip_of(good_csv))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(listfiles_resp, good_zip))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is True
    assert r.data["candidate_match"] is True
    assert r.data["list_rows_searched"] == 1
