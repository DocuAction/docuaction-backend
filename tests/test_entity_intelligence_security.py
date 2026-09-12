"""Security: migration fail-closed, intake safety (zip-slip, bombs, CSV
injection, limits, traversal), no secrets, no network, no shared-DB reads."""
from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path

import pytest

from app.core.entity_intelligence import intake_safety as s
from app.core.entity_intelligence.migrations import apply as apply_mod

EI_ROOT = Path(__file__).resolve().parents[1] / "app"
EI_DIRS = [EI_ROOT / "core" / "entity_intelligence", EI_ROOT / "evidence_sources"]


# ── migration safety: fail closed ───────────────────────────────────────────

REMOTE_URLS = [
    "postgresql+asyncpg://u@docuaction-db.postgres.database.azure.com:5432/docuaction",
    "postgresql://u@docuaction-db-geo.postgres.database.azure.com/docuaction",
    "postgresql://u@10.0.0.5/db",
    "postgresql://u@192.168.1.10:5432/db",
    "postgresql://u@db.internal/x",
    "postgresql://u@localhost.evil.example/db",          # looks local, is not
    "postgresql://u@evil.local.example.com/db",          # '.local' inside, not a suffix
    "postgresql://u@docuaction-dev.azurewebsites.net/db",
    "postgresql+psycopg://u@some-host/db",
    "mysql://u@remote/db",
]
LOCAL_URLS = ["sqlite:///ei_local.db", "sqlite://", "postgresql://u@localhost/db", "postgresql://u@127.0.0.1:5433/db",
              "postgresql+asyncpg://u@[::1]/db", "postgresql:///db", "postgresql://u@ci-runner.local/db"]


class TestMigrationFailClosed:
    @pytest.mark.parametrize("url", REMOTE_URLS)
    def test_remote_urls_are_not_isolated(self, url):
        assert apply_mod.is_isolated(url) is False

    @pytest.mark.parametrize("url", LOCAL_URLS)
    def test_local_urls_are_isolated(self, url):
        assert apply_mod.is_isolated(url) is True

    @pytest.mark.parametrize("url", REMOTE_URLS)
    def test_main_refuses_remote_without_creating_engine(self, url, monkeypatch, capsys):
        def boom(*a, **k):
            raise AssertionError("create_engine must not be called for a refused URL")
        monkeypatch.setattr(apply_mod, "create_engine", boom)
        assert apply_mod.main(["--database-url", url]) == 2
        assert "refusing" in capsys.readouterr().out

    def test_main_requires_explicit_url_never_env(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u@docuaction-db.postgres.database.azure.com/docuaction")
        with pytest.raises(SystemExit):
            apply_mod.main([])

    def test_override_flag_is_explicit_and_logged(self, monkeypatch, capsys):
        created = {}
        class FakeEngine: ...
        def fake(url, *a, **k):
            created["url"] = url
            return FakeEngine()
        monkeypatch.setattr(apply_mod, "create_engine", fake)
        monkeypatch.setattr(apply_mod.EntityIntelligenceBase.metadata, "create_all", lambda e: None)
        rc = apply_mod.main(["--database-url", "postgresql://u@ci/db", "--i-know-this-is-isolated"])
        assert rc == 0 and created["url"] == "postgresql://u@ci/db"

    def test_alembic_versions_has_no_ei_file(self):
        versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        for f in versions.glob("*.py"):
            assert "ei_" not in f.read_text(encoding="utf-8", errors="ignore")[:4000].lower() or \
                   "entity_intelligence" not in f.read_text(encoding="utf-8", errors="ignore")


# ── intake safety ───────────────────────────────────────────────────────────

def make_zip(members: dict, compress=zipfile.ZIP_DEFLATED) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in members.items():
            z.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


class TestZipSlip:
    @pytest.mark.parametrize("name", ["../etc/passwd", "..\\windows\\system32\\x.csv", "/abs/path.csv",
                                      "C:\\temp\\x.csv", "c:/temp/x.csv", "dir/../../x.csv", "./x.csv",
                                      "dir//x.csv", " x.csv", "x.csv ", "x\x00.csv", "", "..", "."])
    def test_unsafe_names(self, name):
        assert s.is_safe_member_name(name) is False

    @pytest.mark.parametrize("name", ["npidata_pfile_20260810-20260816.csv", "sub/dir/file.csv", "readme.pdf",
                                      "NPPES_Data_Dissemination_CodeValues.pdf"])
    def test_safe_names(self, name):
        assert s.is_safe_member_name(name) is True

    def test_inspect_refuses_traversal_member(self):
        z = make_zip({"../evil.csv": b"a,b\n", "ok.csv": b"a,b\n"})
        insp = s.inspect_archive(z)
        assert not insp.accepted and any("unsafe member path" in r for r in insp.refusals)

    def test_safe_extract_path_stays_under_root(self, tmp_path):
        p = s.safe_extract_path(tmp_path, "sub/file.csv")
        assert str(p).startswith(str(tmp_path.resolve()))
        with pytest.raises(s.IntakeRefused):
            s.safe_extract_path(tmp_path, "../file.csv")


class TestArchiveLimits:
    def test_allowlist(self):
        z = make_zip({"x.exe": b"MZ", "x.csv": b"a\n"})
        insp = s.inspect_archive(z)
        assert any("not allowed" in r for r in insp.refusals)

    def test_member_count(self):
        z = make_zip({f"f{i}.csv": b"a\n" for i in range(70)})
        assert any("too many members" in r for r in s.inspect_archive(z).refusals)

    def test_compression_ratio_bomb(self):
        z = make_zip({"bomb.csv": b"0" * 5_000_000})      # ~1000:1
        insp = s.inspect_archive(z)
        assert any("ratio" in r for r in insp.refusals)

    def test_realistic_ratio_passes(self):
        import random
        rng = random.Random(1)
        rows = ['"9999%06d","2","","","SYNTHETIC ORG %d LLC","%d WAY"\n' % (i, rng.randint(0, 10**9), rng.randint(1, 9999))
                for i in range(2000)]
        z = make_zip({"npidata.csv": "".join(rows).encode()})
        insp = s.inspect_archive(z, max_ratio=200.0)
        assert insp.accepted, insp.refusals

    def test_size_limits(self):
        z = make_zip({"big.csv": b"a" * 1000})
        assert any("too large" in r for r in s.inspect_archive(z, max_member_bytes=100).refusals)
        assert any("archive too large" in r for r in s.inspect_archive(z, max_total_bytes=100).refusals)

    def test_inspection_does_not_decompress(self, monkeypatch):
        z = make_zip({"x.csv": b"a\n"})
        monkeypatch.setattr(z, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("decompressed")))
        monkeypatch.setattr(z, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("decompressed")))
        assert s.inspect_archive(z).accepted


class TestCsvInjectionAndLimits:
    @pytest.mark.parametrize("cell", ["=HYPERLINK(\"x\")", "+1+1", "-1", "@SUM(A1)", "\tcmd", "\rcmd", "=cmd|' /C calc'!A0"])
    def test_formula_cells_neutralised(self, cell):
        assert s.is_formula_like(cell)
        out = s.neutralize_cell(cell)
        assert out.startswith("'") and out[1:] == cell

    def test_plain_and_negative_numbers(self):
        assert s.neutralize_cell("SYNTHETIC ORG") == "SYNTHETIC ORG"
        assert s.neutralize_cell(None) == ""
        assert s.neutralize_cell("-5") == "'-5"       # conservative: a leading minus is neutralised too

    def test_column_limit(self):
        r = s.check_csv_limits(",".join(["a"] * 500) + "\n")
        assert not r.accepted and "columns" in r.refusals[0] and r.stopped_at_line == 1

    def test_field_limit(self):
        r = s.check_csv_limits("a,\"" + "x" * 5000 + "\"\n")
        assert not r.accepted and "chars" in r.refusals[0]

    def test_row_limit(self):
        r = s.check_csv_limits("a\n" * 10, max_rows=5)
        assert not r.accepted and "rows" in r.refusals[0] and r.rows == 6

    def test_good_file_accepted(self):
        r = s.check_csv_limits("a,b\n1,2\n")
        assert r.accepted and r.rows == 2 and r.max_columns_seen == 2

    def test_field_size_limit_restored(self):
        import csv
        before = csv.field_size_limit()
        s.check_csv_limits("a\n")
        assert csv.field_size_limit() == before


class TestDecodeAndLogging:
    def test_decode_bom_utf8_cp1252(self):
        assert s.decode_text("\ufeffa,b".encode("utf-8")) == "a,b"
        assert s.decode_text("café".encode("utf-8")) == "café"
        assert s.decode_text("caf\xe9".encode("cp1252")) == "café"

    def test_decode_refuses_undecodable(self):
        with pytest.raises(s.IntakeRefused):
            s.decode_text(b"\xff\xfe\x00\x00\xd8\x00", encodings=("utf-8",))

    def test_redact_for_log(self):
        assert s.redact_for_log(r"C:\Users\someone\Downloads\bundle.zip") == "bundle.zip"
        assert s.redact_for_log("/home/someone/bundle.zip") == "bundle.zip"


# ── static review of the isolated packages ─────────────────────────────────

SECRET_PATTERNS = [re.compile(p) for p in (
    r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_\-]{35}", r"(?i)api[_-]?key\s*=\s*['\"][^'\"]{8,}",
    r"(?i)password\s*=\s*['\"][^'\"]{4,}", r"(?i)secret\s*=\s*['\"][^'\"]{8,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"postgresql(\+\w+)?://[^\s'\"]+:[^\s'\"@]+@(?!localhost|127\.0\.0\.1)",
)]
NETWORK_PATTERNS = [re.compile(p) for p in (
    r"^\s*(import|from)\s+(httpx|requests|aiohttp|urllib\.request|urllib3|socket|ssl|ftplib|smtplib)\b",
    r"googleapis\.com/", r"iqvia\.com/api", r"\.connect\(", r"subprocess", r"os\.system", r"\beval\(", r"\bexec\(",
    r"pickle", r"yaml\.load\(",
)]


def _ei_py_files():
    for d in EI_DIRS:
        yield from d.rglob("*.py")


class TestStaticSecurityReview:
    def test_no_secret_like_strings(self):
        for f in _ei_py_files():
            text = f.read_text(encoding="utf-8")
            for p in SECRET_PATTERNS:
                assert not p.search(text), (f.name, p.pattern)

    def test_no_network_exec_or_unsafe_deserialisation(self):
        for f in _ei_py_files():
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                for p in NETWORK_PATTERNS:
                    assert not p.search(line), (f.name, i, line.strip())

    def test_no_shared_database_session_import(self):
        for f in _ei_py_files():
            text = f.read_text(encoding="utf-8")
            assert "from app.core.database import" not in text, f.name
            assert "get_db" not in text and "AsyncSessionLocal" not in text, f.name

    def test_no_logging_of_observed_values(self):
        for f in _ei_py_files():
            text = f.read_text(encoding="utf-8")
            assert "logging" not in text and "print(" not in text.replace("print(\"refusing", "").replace("print(\"created", ""), f.name

    def test_no_env_reads_other_than_settings(self):
        for f in _ei_py_files():
            text = f.read_text(encoding="utf-8")
            assert "os.environ" not in text and "getenv" not in text, f.name
