"""Unit tests for scripts/zap_prepare_spec.py (DAST setup, no network)."""
import copy
import hashlib
import json

import pytest

from scripts import zap_prepare_spec as z

DEV = "https://docuaction-dev.azurewebsites.net"


def _spec(**extra):
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/health": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/api/x": {"post": {"responses": {"200": {"description": "ok"}}}},
        },
    }
    spec.update(extra)
    return spec


def test_adds_single_allowlisted_root_server_and_leaves_input_untouched():
    spec = _spec()
    snapshot = copy.deepcopy(spec)
    out, rep = z.prepare(spec, DEV + "/", (DEV,))
    assert out["servers"] == [{"url": DEV}]
    assert spec == snapshot  # original object not mutated
    assert rep["operations"] == 2 and rep["root_servers_before"] is None


@pytest.mark.parametrize(
    "bad",
    [
        "https://api.docuaction.io",
        "https://api-prod.docuaction.io",
        "https://docuaction-emffhfgwc0gffgc9.centralus-01.azurewebsites.net",
    ],
)
def test_production_refused_even_if_allowlisted(bad):
    with pytest.raises(z.SpecRefused):
        z.prepare(_spec(), bad, (bad,))


def test_non_allowlisted_origin_refused():
    with pytest.raises(z.SpecRefused):
        z.prepare(_spec(), "https://example.com", (DEV,))


@pytest.mark.parametrize("url", ["/", "docuaction-dev", "ftp://x", "https://u:p@docuaction-dev.azurewebsites.net", DEV + "/?a=1"])
def test_malformed_server_urls_refused(url):
    with pytest.raises(z.SpecRefused):
        z.prepare(_spec(), url, (DEV,))


def test_relative_path_and_operation_overrides_are_anchored_on_allowlisted_origin():
    spec = _spec()
    spec["paths"]["/health"]["servers"] = [{"url": "/v1"}]
    spec["paths"]["/api/x"]["post"]["servers"] = [{"url": DEV + "/ops"}]
    out, rep = z.prepare(spec, DEV, (DEV,))
    assert out["paths"]["/health"]["servers"] == [{"url": DEV + "/v1"}]
    assert out["paths"]["/api/x"]["post"]["servers"] == [{"url": DEV + "/ops"}]
    assert rep["path_overrides"] == 1 and rep["operation_overrides"] == 1


@pytest.mark.parametrize("where", ["path", "operation"])
@pytest.mark.parametrize("url", ["https://api.docuaction.io", "https://evil.example", "http://{host}/x"])
def test_unsafe_override_fails_closed(where, url):
    spec = _spec()
    if where == "path":
        spec["paths"]["/health"]["servers"] = [{"url": url}]
    else:
        spec["paths"]["/health"]["get"]["servers"] = [{"url": url}]
    with pytest.raises(z.SpecRefused):
        z.prepare(spec, DEV, (DEV,))


def test_zero_operations_fails():
    with pytest.raises(z.SpecRefused, match="zero operations"):
        z.prepare(_spec(paths={"/x": {"parameters": []}}), DEV, (DEV,))


def test_swagger2_and_missing_version_refused():
    with pytest.raises(z.SpecRefused):
        z.prepare({"swagger": "2.0", "paths": {}}, DEV, (DEV,))


def test_cli_preserves_original_bytes_and_writes_report(tmp_path):
    src, dst, rep = tmp_path / "o.json", tmp_path / "s.json", tmp_path / "r.json"
    src.write_text(json.dumps(_spec()))
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    rc = z.main(["--input", str(src), "--output", str(dst), "--server-url", DEV, "--report", str(rep)])
    assert rc == 0
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest
    assert json.loads(dst.read_text())["servers"] == [{"url": DEV}]
    assert json.loads(rep.read_text())["original_sha256"] == digest


def test_cli_exit_codes_and_no_output_on_refusal(tmp_path):
    src, dst = tmp_path / "o.json", tmp_path / "s.json"
    src.write_text(json.dumps(_spec()))
    assert z.main(["--input", str(src), "--output", str(dst), "--server-url", "https://api.docuaction.io",
                   "--allow-origin", "https://api.docuaction.io"]) == 2
    assert not dst.exists()
    empty = tmp_path / "e.json"
    empty.write_text(json.dumps(_spec(paths={})))
    assert z.main(["--input", str(empty), "--output", str(dst), "--server-url", DEV]) == 3
    assert z.main(["--input", str(src), "--output", str(src), "--server-url", DEV]) == 2


def test_cli_refuses_a_hardlinked_output_that_aliases_the_input(tmp_path):
    """CodeRabbit CR152-1: a plain string compare (input == output) misses a
    hard link pointing --output at the same file as --input under a different
    path. Without this check, open(output, "w") would silently overwrite the
    "read only" original spec through the alias. Hard links (unlike symlinks
    on Windows) need no elevated privilege, so this variant always runs."""
    import os

    src = tmp_path / "o.json"
    src.write_text(json.dumps(_spec()))
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    alias = tmp_path / "alias.json"
    os.link(src, alias)
    assert z.main(["--input", str(src), "--output", str(alias), "--server-url", DEV]) == 2
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest


def test_cli_refuses_a_symlinked_output_that_aliases_the_input(tmp_path):
    """Same bypass as above, via a symlink rather than a hard link."""
    src = tmp_path / "o.json"
    src.write_text(json.dumps(_spec()))
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    alias = tmp_path / "alias.json"
    try:
        alias.symlink_to(src)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this filesystem/OS")
    assert z.main(["--input", str(src), "--output", str(alias), "--server-url", DEV]) == 2
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest


def test_real_app_spec_prepares_with_no_overrides_and_original_has_no_servers():
    """The generated spec is what ZAP failed on: no servers at any level."""
    from app.main import app

    spec = app.openapi()
    assert "servers" not in spec  # root cause of 'Unable to obtain any server URL'
    out, rep = z.prepare(spec, DEV, (DEV,))
    assert out["servers"] == [{"url": DEV}]
    assert rep["operations"] > 300 and rep["path_overrides"] == 0 and rep["operation_overrides"] == 0
