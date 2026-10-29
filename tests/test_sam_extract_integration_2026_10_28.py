"""SAM public exclusions extract -- flag-gated connector (2026-10-28/29).

Default OFF: every test here either (a) proves the flag-off default never
fetches anything, or (b) drives the connector against an in-memory,
synthetic/real-but-already-public cache or a mocked HTTP layer whose
response shapes were VERIFIED against the real GSA endpoints on 2026-10-29
(listfiles answers `_embedded.customS3ObjectSummaryList[].displayKey`;
the signed download endpoint 303s to a signed S3 URL). No live network
call runs in this file.

The positive-control NPI below (1841517737) is a REAL row already present
in the preserved public GSA extract at
qa-evidence/2026-10-28-october-readiness/sam_extract_preserved/ -- picked
from that file, not fabricated, per the directive. It is already public,
federally-published exclusion data; only the bare NPI digits are used here,
no name or location.
"""
from __future__ import annotations

import datetime as dt
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


def _current_filename() -> str:
    """Today's julian-coded extract name, so mocked listings are never stale."""
    today = dt.date.today()
    return f"SAM_Exclusions_Public_Extract_V2_{today.year % 100:02d}{today.timetuple().tm_yday:03d}.ZIP"


def _listfiles_payload(*filenames) -> dict:
    """The VERIFIED listfiles shape (real endpoint, 2026-10-29)."""
    return {"_embedded": {"customS3ObjectSummaryList": [
        {"displayKey": f, "dateModified": "Oct 28,2026",
         "bucketName": "falextracts", "privacy": "Public"}
        for f in filenames
    ]}}


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


def _cache(**over):
    """A realistically-populated cache: a real load always records the
    filename, its decoded publication date, the listing's dateModified and
    the HTTP Last-Modified -- tests override only what they test."""
    base = {
        "loaded_at": time.time(), "row_count": 1,
        "by_npi": {"1841517737": [{"Name": "", "NPI": "1841517737"}]},
        "by_name": {},
        "filename": _current_filename(),
        "publication_date": dt.date.today(),
        "date_modified": "Oct 28,2026",
        "http_last_modified": "Wed, 28 Oct 2026 09:39:36 GMT",
        "sha256": "deadbeef", "source_url": "https://sam.gov/...",
    }
    base.update(over)
    return base


def _empty_cache():
    return {"loaded_at": 0.0, "row_count": 0, "by_npi": {}, "by_name": {},
            "filename": None, "publication_date": None, "date_modified": None,
            "http_last_modified": None, "sha256": None, "source_url": None}


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
async def test_flag_off_also_blocks_name_lookup_and_screen(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.delenv(s.ENV_FLAG, raising=False)
    monkeypatch.setattr(httpx, "AsyncClient",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch when flag is off")))

    assert (await s.SAMExtractConnector().lookup_by_name("Synthetic Test Org")).success is False
    assert (await s.SAMExtractConnector().screen("1841517737", "Synthetic Test Org")).success is False


# ── listfiles parsing: VERIFIED shape only, never guessed ───────────────────

def test_publication_date_decodes_the_julian_filename_code():
    from app.Tefca.sam_extract import publication_date_from_filename

    assert publication_date_from_filename(
        "SAM_Exclusions_Public_Extract_V2_26301.ZIP") == dt.date(2026, 10, 28)
    # Day 001 and a leap-year day both decode.
    assert publication_date_from_filename(
        "SAM_Exclusions_Public_Extract_V2_27001.ZIP") == dt.date(2027, 1, 1)
    assert publication_date_from_filename(
        "SAM_Exclusions_Public_Extract_V2_28366.ZIP") == dt.date(2028, 12, 31)
    # Not decodable -> None, never a guess.
    assert publication_date_from_filename("SAM_Exclusions_Public_Extract_V2_XYZ.ZIP") is None
    assert publication_date_from_filename("") is None


def test_listfiles_parser_accepts_only_the_verified_real_shape():
    from app.Tefca.sam_extract import parse_listfiles_payload

    real_shape = _listfiles_payload("SAM_Exclusions_Public_Extract_V2_26301.ZIP",
                                     "SAM_Exclusions_Public_Extract_V2_26300.ZIP")
    entries = parse_listfiles_payload(real_shape)
    assert [e["filename"] for e in entries] == [
        "SAM_Exclusions_Public_Extract_V2_26301.ZIP",
        "SAM_Exclusions_Public_Extract_V2_26300.ZIP"]
    assert entries[0]["publication_date"] == dt.date(2026, 10, 28)
    assert entries[0]["date_modified"] == "Oct 28,2026"

    # The shapes the first draft GUESSED must now be refused, not tolerated:
    assert parse_listfiles_payload({"files": [{"name": "SAM_Exclusions_Public_Extract_V2_26301.ZIP"}]}) == []
    assert parse_listfiles_payload({"data": [{"fileName": "SAM_Exclusions_Public_Extract_V2_26301.ZIP"}]}) == []
    assert parse_listfiles_payload([{"name": "x.ZIP"}]) == []
    assert parse_listfiles_payload({}) == []
    assert parse_listfiles_payload(None) == []


@pytest.mark.asyncio
async def test_newest_file_is_chosen_by_julian_date_not_list_order(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _empty_cache())

    newest = _current_filename()
    listing = _listfiles_payload("SAM_Exclusions_Public_Extract_V2_26299.ZIP",
                                  newest,
                                  "SAM_Exclusions_Public_Extract_V2_26300.ZIP")
    good_zip = _Resp(200, content=_zip_of(GOOD_HEADER + "\n" + GOOD_ROW + "\n"))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(_Resp(200, json_payload=listing), good_zip))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is True
    assert s._SAM_CACHE["filename"] == newest
    assert s._SAM_CACHE["publication_date"] == dt.date.today()


# ── positive / negative controls (in-memory cache, flag on) ─────────────────

@pytest.mark.asyncio
async def test_positive_control_real_excluded_npi_is_a_candidate_not_an_exclusion(monkeypatch):
    """1841517737 is a real row in the preserved extract. A hit must be
    `candidate_match`, and the result must never carry an `excluded` key --
    that word is reserved for LEIE/the live SAM API's own exclusion finding.
    """
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _cache())

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is True
    assert r.data["candidate_match"] is True
    assert r.data["candidate_count"] == 1
    assert "excluded" not in r.data
    assert r.data["extract_sha256"] == "deadbeef"
    assert r.data["list_rows_searched"] == 1
    # The edition is three separate facts, all present, never conflated.
    assert r.data["publication_date"] == dt.date.today().isoformat()
    assert r.data["listing_date_modified"] == "Oct 28,2026"
    assert r.data["http_last_modified"] == "Wed, 28 Oct 2026 09:39:36 GMT"
    assert r.data["extract_filename"] == _current_filename()


@pytest.mark.asyncio
async def test_negative_control_known_good_npi_is_no_match(monkeypatch):
    """1891430989 -- the known-good, actively-enrolled NPI already confirmed
    real via PECOS elsewhere in this engagement -- must resolve to no match.
    """
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _cache())

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


# ── screen(): union of NPI and name, never NPI-first-else-name ───────────────

@pytest.mark.asyncio
async def test_screen_finds_npi_less_exclusion_row_for_an_npi_bearing_entity(monkeypatch):
    """The directive's exact scenario: the entity HAS an NPI, but its exclusion
    row in the extract has NO NPI (the NPI column is optional in GSA's data).
    An NPI-only lookup misses it; screen() must still surface it by name."""
    from app.Tefca import sam_extract as s
    from app.Tefca.connectors import normalize_org_name

    monkeypatch.setenv(s.ENV_FLAG, "true")
    row_without_npi = {"Name": "Synthetic Test Org, Inc.", "NPI": ""}
    _reset_cache(monkeypatch, _cache(
        by_npi={},  # nothing findable by NPI
        by_name={normalize_org_name("Synthetic Test Org, Inc."): [row_without_npi]},
    ))

    r = await s.SAMExtractConnector().screen("1891430989", "Synthetic Test Org Inc")
    assert r.success is True
    assert r.data["candidate_match"] is True
    assert r.data["match_methods"] == ["npi", "normalized_business_name"]
    assert r.data["match_methods_matched"] == ["normalized_business_name"]


@pytest.mark.asyncio
async def test_screen_dedupes_a_row_found_by_both_methods(monkeypatch):
    from app.Tefca import sam_extract as s
    from app.Tefca.connectors import normalize_org_name

    monkeypatch.setenv(s.ENV_FLAG, "true")
    row = {"Name": "Synthetic Test Org", "NPI": "1841517737"}
    _reset_cache(monkeypatch, _cache(
        by_npi={"1841517737": [row]},
        by_name={normalize_org_name("Synthetic Test Org"): [row]},
    ))

    r = await s.SAMExtractConnector().screen("1841517737", "Synthetic Test Org")
    assert r.success is True
    assert r.data["candidate_count"] == 1  # one row, not double-counted
    assert set(r.data["match_methods_matched"]) == {"npi", "normalized_business_name"}


@pytest.mark.asyncio
async def test_screen_with_no_usable_identifier_refuses(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    r = await s.SAMExtractConnector().screen(None, "")
    assert r.success is False
    assert "no_usable_identifier" in r.error


# ── missing identifier / stale-source handling ───────────────────────────────

@pytest.mark.asyncio
async def test_lookup_by_name_with_blank_org_name_is_a_missing_identifier_not_a_lookup(monkeypatch):
    """Mirrors npi_rejection_reason's missing-vs-malformed distinction: no name
    to look up is a normal state, and must never fall through to
    normalize_org_name("") asking the cache for an empty key."""
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    r = await s.SAMExtractConnector().lookup_by_name("")
    assert r.success is False
    assert "no_org_name_submitted" in r.error


@pytest.mark.asyncio
async def test_stale_publication_date_fails_closed_rather_than_screening_as_current(monkeypatch):
    """A cache that re-downloads successfully every day but is secretly
    serving the same old edition would pass the TTL freshness check forever.
    The SOURCE's own publication date (from the filename's julian code) --
    not just the cache's load time -- must be checked, and a stale edition
    must disclose as UNAVAILABLE, never as a silent clean screen."""
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _cache(
        publication_date=dt.date.today() - dt.timedelta(days=9)))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False
    assert "stale_source" in r.error


@pytest.mark.asyncio
async def test_missing_publication_date_also_fails_closed(monkeypatch):
    """A cache somehow populated without a decodable publication date can't
    have its edition freshness confirmed, so it must refuse rather than
    assume current."""
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _cache(publication_date=None))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False
    assert "stale_source" in r.error


# ── malformed-body rejection (fetch + index path) ────────────────────────────

@pytest.mark.asyncio
async def test_malformed_zip_body_is_refused_not_indexed_as_clean(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _empty_cache())

    listfiles_resp = _Resp(200, json_payload=_listfiles_payload(_current_filename()))
    not_a_zip = _Resp(200, content=b"<html>not a zip</html>")
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(listfiles_resp, not_a_zip))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False
    assert s._SAM_CACHE["row_count"] == 0  # previously-good index, if any, must be untouched


@pytest.mark.asyncio
async def test_wrong_schema_csv_is_refused(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _empty_cache())

    bad_csv = "COMPLETELY,WRONG,HEADER\n1,2,3\n"
    listfiles_resp = _Resp(200, json_payload=_listfiles_payload(_current_filename()))
    wrong_schema = _Resp(200, content=_zip_of(bad_csv))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(listfiles_resp, wrong_schema))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False
    assert s._SAM_CACHE["row_count"] == 0


@pytest.mark.asyncio
async def test_truncated_row_is_refused_not_partially_indexed(monkeypatch):
    """Completeness: a body cut off mid-row (fewer fields than the header)
    must refuse the WHOLE body -- partially indexing a truncated download
    would under-screen silently."""
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _empty_cache())

    truncated_csv = GOOD_HEADER + "\n" + GOOD_ROW + "\n,,,SYNTH"  # second row cut off
    listfiles_resp = _Resp(200, json_payload=_listfiles_payload(_current_filename()))
    truncated = _Resp(200, content=_zip_of(truncated_csv))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(listfiles_resp, truncated))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False
    assert s._SAM_CACHE["row_count"] == 0


@pytest.mark.asyncio
async def test_unexpected_listfiles_shape_is_refused_not_guessed(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _empty_cache())

    guessed_shape = _Resp(200, json_payload={"files": [{"name": _current_filename()}]})
    good_zip = _Resp(200, content=_zip_of(GOOD_HEADER + "\n" + GOOD_ROW + "\n"))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(guessed_shape, good_zip))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is False


@pytest.mark.asyncio
async def test_good_zip_body_loads_and_answers_positive_control(monkeypatch):
    from app.Tefca import sam_extract as s

    monkeypatch.setenv(s.ENV_FLAG, "true")
    _reset_cache(monkeypatch, _empty_cache())

    good_csv = GOOD_HEADER + "\n" + GOOD_ROW + "\n"
    listfiles_resp = _Resp(200, json_payload=_listfiles_payload(_current_filename()))
    good_zip = _Resp(200, content=_zip_of(good_csv))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(listfiles_resp, good_zip))

    r = await s.SAMExtractConnector().lookup_by_npi("1841517737")
    assert r.success is True
    assert r.data["candidate_match"] is True
    assert r.data["list_rows_searched"] == 1
    # The loader recorded all three edition facts, separately.
    assert r.data["extract_filename"] == _current_filename()
    assert r.data["publication_date"] == dt.date.today().isoformat()
    assert r.data["listing_date_modified"] == "Oct 28,2026"
    assert r.data["http_last_modified"] == "Wed, 28 Oct 2026 09:39:36 GMT"
