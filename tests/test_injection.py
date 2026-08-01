"""Injection resistance at the request boundary: SQL, XSS, path traversal, XXE.

These probe the boundary rather than the query builder. The point is not that a
particular payload is blocked by a particular regex — it is that a hostile input
never produces a 500 (an unhandled parser/driver error) and never comes back
reflected verbatim into an HTML response. A 400/401/404/422 is a fine answer; a
500 means the payload reached something that did not expect it.
"""
import pytest

SQL_PAYLOADS = [
    "' OR '1'='1",
    "1; DROP TABLE users--",
    "' UNION SELECT NULL,version()--",
    "admin'--",
    "%27%20OR%201=1",
]

XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    "\"><img src=x onerror=alert(1)>",
    "javascript:alert(1)",
]

TRAVERSAL_PAYLOADS = [
    "../../../../etc/passwd",
    "..%2f..%2f..%2fetc%2fpasswd",
    "....//....//etc/passwd",
]

OK = (200, 400, 401, 403, 404, 422)


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_payload_in_path_does_not_reach_the_driver(client, payload):
    r = client.get(f"/api/v1/bulletin/latest/{payload}")
    assert r.status_code != 500, f"{payload!r} produced a server error"
    assert r.status_code in OK


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_payload_in_query_string_is_handled(client, payload):
    r = client.get("/api/v1/bulletin/history/fcc", params={"agency_id": payload})
    assert r.status_code != 500


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_payload_in_login_body_is_rejected_not_executed(client, payload):
    """The classic auth bypass. Anything other than a clean rejection — and in
    particular a 200 — would mean the credential check was side-stepped."""
    r = client.post("/api/auth/login", json={"email": payload, "password": payload})
    assert r.status_code != 500
    assert r.status_code != 200, "SQL payload authenticated -- auth bypass"


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_xss_payload_is_not_reflected_into_html(client, payload):
    r = client.get(f"/api/v1/bulletin/briefings/{payload}/preview")
    assert r.status_code != 500
    ctype = r.headers.get("content-type", "")
    if "text/html" in ctype:
        assert "<script>alert(1)</script>" not in r.text
        assert "onerror=alert(1)" not in r.text


@pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
def test_path_traversal_does_not_read_the_filesystem(client, payload):
    r = client.get(f"/api/v1/bulletin/briefings/{payload}/excel")
    assert r.status_code != 500
    body = r.text[:4000].lower()
    assert "root:x:" not in body, "traversal returned /etc/passwd content"


def test_oversized_body_is_refused_not_buffered(client):
    """A 10 MB login body should be rejected, not parsed."""
    r = client.post("/api/auth/login",
                    json={"email": "a@b.c", "password": "x" * (10 * 1024 * 1024)})
    assert r.status_code != 500


def test_null_byte_in_path_is_handled(client):
    r = client.get("/api/v1/bulletin/latest/fcc%00.txt")
    assert r.status_code != 500


def test_xxe_entity_expansion_is_refused_by_the_parser():
    """The bulletin parses RSS from feeds it does not control. stdlib
    ElementTree expands entities; defusedxml must refuse the bomb outright."""
    from defusedxml import ElementTree as DET
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz ['
        '<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        ']><lolz>&lol2;</lolz>'
    )
    with pytest.raises(Exception):
        DET.fromstring(bomb)


def test_xxe_external_entity_cannot_read_local_files():
    from defusedxml import ElementTree as DET
    ext = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM '
           '"file:///etc/passwd">]><r>&x;</r>')
    with pytest.raises(Exception):
        DET.fromstring(ext)


def test_wellformed_feed_still_parses():
    """The XXE hardening must not have cost us ordinary RSS."""
    from defusedxml import ElementTree as DET
    root = DET.fromstring(
        "<rss><channel><item><title>FCC item</title></item></channel></rss>")
    assert root.find(".//title").text == "FCC item"
