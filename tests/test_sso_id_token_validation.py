"""Negative/positive tests for Entra ID-token validation (no network).

Tokens are signed with locally generated RSA keys; the JWKS download and the
Microsoft token endpoint are replaced by in-process fakes.
"""
import base64
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwk, jwt

from app.api import azure_auth_routes as sso
from app.core.database import get_db

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
KID = "test-kid-1"


def _keypair():
    """Return (private PEM, public JWK dict) for a fresh RSA key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    pub = jwk.construct(pub_pem, "RS256").to_dict()
    pub["kid"] = KID
    return pem, pub


GOOD_PEM, GOOD_JWK = _keypair()
OTHER_PEM, _ = _keypair()


def _b64(data) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _claims(**over):
    """Valid claim set; pass name=None to drop a claim."""
    now = int(time.time())
    c = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": CLIENT,
         "tid": TENANT, "sub": "abc", "exp": now + 600, "nbf": now - 5, "iat": now - 5,
         "nonce": "N1", "preferred_username": "User@Example.com", "name": "U"}
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


def _token(claims=None, pem=GOOD_PEM, kid=KID, alg="RS256"):
    return jwt.encode(claims or _claims(), pem, algorithm=alg, headers={"kid": kid})


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(sso, "CLIENT_ID", CLIENT)
    monkeypatch.setattr(sso, "CLIENT_SECRET", "s")
    monkeypatch.setattr(sso, "TENANT_ID", TENANT)
    monkeypatch.setattr(sso, "AUTHORITY", f"https://login.microsoftonline.com/{TENANT}")
    sso._jwks_cache.update({"keys": {}, "fetched_at": 0.0, "last_attempt_at": 0.0, "last_attempt_ok": True})
    calls = {"n": 0}

    async def fake_jwks():
        calls["n"] += 1
        return [GOOD_JWK]
    monkeypatch.setattr(sso, "_fetch_jwks", fake_jwks)
    return calls


async def _rejects(token, nonce="N1", reason=None):
    with pytest.raises(sso.IdTokenError) as ei:
        await sso.validate_id_token(token, nonce)
    if reason:
        assert ei.value.reason == reason
    return ei.value


async def test_valid_token_accepted():
    claims = await sso.validate_id_token(_token(), "N1")
    assert claims["tid"] == TENANT and claims["nonce"] == "N1"


async def test_alg_none_rejected():
    tok = f"{_b64({'alg': 'none', 'kid': KID, 'typ': 'JWT'})}.{_b64(_claims())}."
    await _rejects(tok, reason="bad_alg")


async def test_malformed_token_rejected():
    """Not a JWT at all (no valid header to parse) -> the 'malformed' path,
    distinct from 'bad_alg' (parseable header, wrong alg) and 'no_kid'
    (parseable header, missing kid) below."""
    await _rejects("not-a-jwt-at-all", reason="malformed")
    await _rejects("", reason="malformed")


async def test_header_missing_kid_rejected():
    tok = f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(_claims())}.sig"
    await _rejects(tok, reason="no_kid")


async def test_hs256_algorithm_confusion_rejected():
    tok = jwt.encode(_claims(), "secret", algorithm="HS256", headers={"kid": KID})
    await _rejects(tok, reason="bad_alg")


async def test_wrong_signature_key_rejected():
    await _rejects(_token(pem=OTHER_PEM))


async def test_expired_rejected():
    await _rejects(_token(_claims(exp=int(time.time()) - 3600)))


async def test_not_yet_valid_rejected():
    await _rejects(_token(_claims(nbf=int(time.time()) + 3600)))


async def test_clock_leeway_boundary_accepted_outside_rejected():
    """CLOCK_LEEWAY_SECONDS=120 must actually be wired into the decode call, not
    just documented. A token 60s past exp (inside the leeway) should still be
    accepted; one 180s past exp (outside it) must still be rejected."""
    claims = await sso.validate_id_token(_token(_claims(exp=int(time.time()) - 60)), "N1")
    assert claims["tid"] == TENANT
    await _rejects(_token(_claims(exp=int(time.time()) - 180)))


async def test_wrong_audience_rejected():
    await _rejects(_token(_claims(aud="someone-else")))


async def test_wrong_issuer_rejected():
    await _rejects(_token(_claims(iss="https://login.microsoftonline.com/other/v2.0")))


async def test_wrong_tid_rejected():
    await _rejects(_token(_claims(tid="99999999-9999-9999-9999-999999999999")),
                   reason="tid_mismatch")


@pytest.mark.parametrize("missing", ["exp", "iss", "aud", "sub", "tid"])
async def test_missing_claims_rejected(missing):
    await _rejects(_token(_claims(**{missing: None})))


async def test_tampered_payload_rejected():
    h, p, sg = _token().split(".")
    body = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    body["preferred_username"] = "admin@example.com"
    await _rejects(f"{h}.{_b64(body)}.{sg}")


async def test_nonce_mismatch_and_missing_rejected():
    await _rejects(_token(), nonce="OTHER", reason="nonce_mismatch")
    await _rejects(_token(_claims(nonce=None)), reason="nonce_mismatch")


async def test_no_expected_nonce_skips_the_check():
    """expected_nonce=None means the caller isn't doing nonce-bound login (e.g.
    a non-interactive flow) -- the nonce check must be skipped entirely, not
    compared against the string 'None'."""
    claims = await sso.validate_id_token(_token(), None)
    assert claims["nonce"] == "N1"
    claims = await sso.validate_id_token(_token(_claims(nonce=None)), None)
    assert "nonce" not in claims


async def test_unknown_kid_rejected_after_single_refetch(_cfg):
    await _rejects(_token(kid="rotated-away"), reason="unknown_kid")
    assert _cfg["n"] == 1


async def test_key_rotation_refetches_then_accepts(monkeypatch, _cfg):
    await sso.validate_id_token(_token(), "N1")           # populate cache
    new_pem, new_jwk = _keypair()
    new_jwk["kid"] = "kid-2"

    async def rotated():
        _cfg["n"] += 1
        return [GOOD_JWK, new_jwk]
    monkeypatch.setattr(sso, "_fetch_jwks", rotated)
    sso._jwks_cache["fetched_at"] -= sso.JWKS_REFRESH_COOLDOWN + 1
    ok = await sso.validate_id_token(_token(pem=new_pem, kid="kid-2"), "N1")
    assert ok["tid"] == TENANT
    assert _cfg["n"] == 2


async def test_jwks_cached_across_validations(_cfg):
    await sso.validate_id_token(_token(), "N1")
    await sso.validate_id_token(_token(), "N1")
    assert _cfg["n"] == 1


async def test_jwks_unavailable_is_502(monkeypatch):
    async def boom():
        raise sso.IdTokenError("jwks_unavailable", 502)
    monkeypatch.setattr(sso, "_fetch_jwks", boom)
    err = await _rejects(_token(), reason="jwks_unavailable")
    assert err.status == 502


# ---- JWKS cache policy: failure cooldown, repeated requests, recovery (2026-10-19) ----

async def test_repeated_requests_during_an_outage_do_not_each_retry(monkeypatch):
    """Security-reviewed cache policy: a failed fetch must be rate limited the
    same way a successful one is. Before this, `fetched_at` only advanced on
    success, so every request carrying an unrecognized kid during an outage
    made its own fresh outbound attempt -- a login storm during a real JWKS
    outage would have hammered Microsoft's discovery endpoint once per
    incoming login, unthrottled."""
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise sso.IdTokenError("jwks_unavailable", 502)
    monkeypatch.setattr(sso, "_fetch_jwks", boom)

    await _rejects(_token(), reason="jwks_unavailable")
    await _rejects(_token(), reason="jwks_unavailable")
    await _rejects(_token(), reason="jwks_unavailable")
    assert calls["n"] == 1, "a second and third request inside the failure cooldown must not retry"


async def test_recovery_after_the_failure_cooldown_expires(monkeypatch, _cfg):
    """A later attempt, once the cooldown has elapsed, can still succeed and
    the cache genuinely recovers -- the failure path is a rate limit, not a
    permanent circuit-breaker trip."""
    async def boom():
        raise sso.IdTokenError("jwks_unavailable", 502)
    monkeypatch.setattr(sso, "_fetch_jwks", boom)
    await _rejects(_token(), reason="jwks_unavailable")

    monkeypatch.setattr(sso, "_fetch_jwks", _cfg_fake_jwks_for(_cfg))
    sso._jwks_cache["last_attempt_at"] -= sso.JWKS_FAILURE_COOLDOWN_SECONDS + 1
    claims = await sso.validate_id_token(_token(), "N1")
    assert claims["tid"] == TENANT
    assert sso._jwks_cache["last_attempt_ok"] is True


def _cfg_fake_jwks_for(calls):
    async def fake_jwks():
        calls["n"] += 1
        return [GOOD_JWK]
    return fake_jwks


async def test_an_unrelated_kid_during_the_failure_cooldown_is_still_jwks_unavailable_not_unknown_kid(monkeypatch):
    """While in the failure cooldown, the caller must not get a misleading
    'unknown_kid' (which would read as "your token is wrong") for what is
    actually "we could not check" -- the distinction matters because only
    one of the two is the caller's fault."""
    async def boom():
        raise sso.IdTokenError("jwks_unavailable", 502)
    monkeypatch.setattr(sso, "_fetch_jwks", boom)
    await _rejects(_token(), reason="jwks_unavailable")
    err = await _rejects(_token(kid="some-other-kid"))
    assert err.reason == "jwks_unavailable", "must not surface as unknown_kid while the cause is an outage"


async def test_a_failed_refetch_falls_back_to_the_already_cached_key_not_jwks_unavailable(monkeypatch, _cfg):
    """CodeRabbit (PR #150, Advanced-Tier security pass): a key already
    verified once against Microsoft does not stop being cryptographically
    valid just because a later refresh attempt happens to fail -- a short
    outage must not turn an already-trusted key into a 502 for every sign-in
    using it. Bounded, not indefinite: this only engages while refetch
    attempts are actively failing; the next successful fetch replaces the
    whole key set."""
    await sso.validate_id_token(_token(), "N1")  # populate the cache for KID
    assert KID in sso._jwks_cache["keys"]

    async def boom():
        raise sso.IdTokenError("jwks_unavailable", 502)
    monkeypatch.setattr(sso, "_fetch_jwks", boom)
    sso._jwks_cache["fetched_at"] -= sso.JWKS_TTL_SECONDS + 1  # past the TTL -> forces a real refetch attempt

    claims = await sso.validate_id_token(_token(), "N1")  # same kid as before
    assert claims["tid"] == TENANT
    assert sso._jwks_cache["last_attempt_ok"] is False  # the refetch genuinely failed


async def test_an_unknown_kid_with_no_prior_success_still_fails_closed_during_an_outage(monkeypatch):
    """The stale-key fallback above must never extend to a kid that was never
    actually fetched and verified -- that would be "accept an unverifiable
    token", exactly what it must not do."""
    async def boom():
        raise sso.IdTokenError("jwks_unavailable", 502)
    monkeypatch.setattr(sso, "_fetch_jwks", boom)
    err = await _rejects(_token(kid="never-seen-before"), reason="jwks_unavailable")
    assert err.status == 502


async def test_concurrent_cold_start_requests_fetch_jwks_only_once(_cfg):
    """Concurrent refresh: asyncio is cooperative, not parallel, but
    _fetch_jwks awaits network I/O and yields control -- without the lock,
    two simultaneous logins on a cold cache would each independently decide
    a refetch is needed and both call out to Microsoft."""
    import asyncio as _asyncio

    results = await _asyncio.gather(
        sso.validate_id_token(_token(), "N1"),
        sso.validate_id_token(_token(), "N1"),
    )
    assert all(r["tid"] == TENANT for r in results)
    assert _cfg["n"] == 1, "two concurrent cold-start validations must share one JWKS fetch"


# ---- callback integration: the only browser-supplied inputs are code/state ----

class _FakeResult:
    def __init__(self, user):
        self._u = user

    def scalar_one_or_none(self):
        return self._u


class _FakeDB:
    def __init__(self, user=None):
        self.user, self.added = user, []

    async def execute(self, *_a, **_k):
        return _FakeResult(self.user)

    def add(self, o):
        self.added.append(o)

    async def commit(self):
        pass

    async def refresh(self, o):
        o.id = getattr(o, "id", None) or 1


def _client(monkeypatch, id_token, mode):
    monkeypatch.setattr(sso, "ID_TOKEN_VALIDATION", mode)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return SimpleNamespace(status_code=200, text="",
                                   json=lambda: {"id_token": id_token})
    monkeypatch.setattr(sso.httpx, "AsyncClient", FakeClient)
    db = _FakeDB()
    app = FastAPI()
    app.include_router(sso.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, follow_redirects=False), db


def _state(nonce="N1"):
    return sso._make_state("https://x/api/auth/callback/azure", nonce)


def _cb(c, **params):
    return c.get("/api/auth/callback/azure", params=params)


def test_callback_valid_login_identical_in_both_modes(monkeypatch):
    for mode in ("audit", "enforce"):
        c, db = _client(monkeypatch, _token(), mode)
        r = _cb(c, code="c", state=_state())
        assert r.status_code == 302, mode
        assert "access_token=" in r.headers["location"]
        assert db.added and db.added[0].email == "user@example.com"


def test_callback_enforce_rejects_token_signed_by_wrong_key(monkeypatch):
    c, db = _client(monkeypatch, _token(pem=OTHER_PEM), "enforce")
    r = _cb(c, code="c", state=_state())
    assert r.status_code == 403 and not db.added


def test_callback_enforce_rejects_missing_aud_and_tid(monkeypatch):
    # The legacy checks were fail-open when aud/tid were absent.
    for tok in (_token(_claims(aud=None)), _token(_claims(tid=None))):
        c, db = _client(monkeypatch, tok, "enforce")
        r = _cb(c, code="c", state=_state())
        assert r.status_code == 403 and not db.added


def test_callback_enforce_rejects_nonce_from_other_flow(monkeypatch):
    c, db = _client(monkeypatch, _token(), "enforce")
    r = _cb(c, code="c", state=_state("DIFFERENT"))
    assert r.status_code == 403 and not db.added


def test_callback_audit_mode_keeps_legacy_behaviour(monkeypatch):
    c, db = _client(monkeypatch, _token(pem=OTHER_PEM), "audit")
    r = _cb(c, code="c", state=_state())
    assert r.status_code == 302 and db.added      # unchanged until the operator enforces


def test_callback_has_no_browser_supplied_token_path(monkeypatch):
    """An id_token query parameter is ignored: the only token read is the one
    returned by the server-side code exchange."""
    c, db = _client(monkeypatch, _token(pem=OTHER_PEM), "enforce")
    browser_token = _token()          # valid, but supplied by the browser
    r = _cb(c, code="c", state=_state(), id_token=browser_token)
    assert r.status_code == 403 and not db.added
    assert _cb(c, id_token=browser_token).status_code == 400
    assert _cb(c, code="c", state="forged").status_code == 400


def test_login_sends_nonce_matching_state(monkeypatch):
    c, _ = _client(monkeypatch, _token(), "audit")
    r = c.get("/api/auth/login/azure")
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["nonce"][0] == sso._check_state(q["state"][0])["nonce"]


# --- AZURE_AD_ID_TOKEN_VALIDATION mode resolution (CWE-1188 guard) ---------------

@pytest.mark.parametrize("raw,expected", [
    (None, "audit"), ("", "audit"), ("   ", "audit"), ("audit", "audit"),
    ("AUDIT", "audit"), ("enforce", "enforce"), (" Enforce ", "enforce"),
])
def test_mode_valid_values_resolve_without_warning(raw, expected, caplog):
    with caplog.at_level("WARNING", logger="docuaction.azure_auth"):
        assert sso.resolve_id_token_validation_mode(raw) == expected
    assert not caplog.records


@pytest.mark.parametrize("raw", ["enforced", "enforce!", "true", "1", "off", "enfroce"])
def test_mode_unrecognised_value_is_audit_with_redacted_warning(raw, caplog):
    with caplog.at_level("WARNING", logger="docuaction.azure_auth"):
        assert sso.resolve_id_token_validation_mode(raw) == "audit"
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "UNRECOGNISED" in msg and f"length={len(raw)}" in msg
    assert "does NOT enforce" in msg
    assert raw not in msg.replace(f"length={len(raw)}", "")  # offending value is not echoed


def test_mode_warning_never_echoes_a_token_shaped_value(caplog):
    secret = "eyJhbGciOiJSUzI1NiJ9.SECRETPAYLOAD.SIG"
    with caplog.at_level("WARNING", logger="docuaction.azure_auth"):
        assert sso.resolve_id_token_validation_mode(secret) == "audit"
    assert "SECRETPAYLOAD" not in caplog.text and "eyJ" not in caplog.text


def test_audit_mode_accepts_bad_signature_and_logs_would_reject(monkeypatch, caplog):
    """Audit = observation, NOT remediation: a wrongly signed token is still accepted
    (302 + user created) and only a value-free log line records the would-reject."""
    c, db = _client(monkeypatch, _token(pem=OTHER_PEM), "audit")
    with caplog.at_level("WARNING", logger="docuaction.azure_auth"):
        r = _cb(c, code="c", state=_state())
    assert r.status_code == 302 and db.added
    assert any("Azure ID token validation failed: reason=" in m and "mode=audit" in m
               for m in caplog.messages)
    assert "eyJ" not in caplog.text


def test_catalog_sql_header_and_column_privileges_query():
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parents[1] / "docs" / "READ_ONLY_CATALOG_QUERIES_DEV.sql").read_text(encoding="utf-8")
    head = sql.split("-- 1.")[0]
    assert "query 10" in head and "reads application rows" in head
    assert "information_schema.column_privileges" in sql and "column_name" in sql
