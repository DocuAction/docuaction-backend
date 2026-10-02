"""PROOF OF ZERO CURRENT-QA IMPACT.

With the master flag off (the default) the capability must not run, call out,
persist, register a route or a job, or touch the shared schema. These tests
read the code and the application, not a database.
"""
from __future__ import annotations

import inspect
import os
import re

import pytest
from sqlalchemy import create_engine, inspect as sa_inspect

from app.core.config import settings
from app.core.database import Base as CoreBase
from app.core.entity_intelligence import FeatureDisabled, flags
from app.core.entity_intelligence.models import EI_TABLES, EntityIntelligenceBase
from app.core.entity_intelligence.service import EntityIntelligenceService
from app.evidence_sources.iqvia_onekey import IQVIAOneKeyDeliveryAdapter, STATUS
from app.evidence_sources.iqvia_onekey.adapter import SchemaUnknown
from app.evidence_sources.nppes_v2 import NppesV2Adapter
from app.evidence_sources.nppes_v2.adapter import NppesV2Bundle

from ei_fixtures import delivered, enable_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED_ALEMBIC_HEAD = "20260903_delivery_grants"


class TestFlags:
    def test_every_flag_defaults_false(self):
        for name in flags.ALL_FLAGS:
            assert getattr(settings, name) is False, name

    def test_master_off_blocks_service_and_adapters(self):
        with pytest.raises(FeatureDisabled):
            EntityIntelligenceService().evaluate(canonical_entity_id="e", current=delivered("X"), source_ids=["NPPES_V2"])
        with pytest.raises(FeatureDisabled):
            NppesV2Adapter(NppesV2Bundle()).observations_for(canonical_entity_id="e", identifier="1", provenance=None)
        with pytest.raises(FeatureDisabled):
            IQVIAOneKeyDeliveryAdapter().observations_for(canonical_entity_id="e", identifier="1", provenance=None)

    def test_sub_flags_are_meaningless_without_master(self, monkeypatch):
        monkeypatch.setattr(settings, "NPPES_IDENTITY_CORROBORATION_ENABLED", True)
        assert flags.source_enabled(flags.NPPES) is False
        with pytest.raises(FeatureDisabled):
            NppesV2Adapter(NppesV2Bundle()).observations_for(canonical_entity_id="e", identifier="1", provenance=None)

    def test_flags_are_read_at_call_time(self, monkeypatch):
        assert flags.entity_intelligence_enabled() is False
        monkeypatch.setattr(settings, "ENTITY_INTELLIGENCE_ENABLED", True)
        assert flags.entity_intelligence_enabled() is True


class TestNoRoutesNoJobs:
    def test_openapi_has_no_entity_intelligence_route(self):
        from app.main import app
        paths = app.openapi()["paths"]
        assert not [p for p in paths if "intelligence" in p.lower() and "entity" in p.lower()]
        assert not [p for p in paths if "/ei/" in p or "nppes-v2" in p or "onekey" in p]

    def test_nothing_in_the_app_imports_the_capability(self):
        """No router, job, report or existing service imports the package."""
        offenders = []
        for base, _d, files in os.walk(os.path.join(ROOT, "app")):
            if "entity_intelligence" in base or "evidence_sources" in base:
                continue
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(base, f)
                    with open(path, encoding="utf-8", errors="replace") as h:
                        src = h.read()
                    if "entity_intelligence" in src or "evidence_sources" in src:
                        offenders.append(os.path.relpath(path, ROOT))
        # config.py declares the flags; nothing else may reference the package.
        assert offenders == ["app\\core\\config.py"] or offenders == ["app/core/config.py"], offenders

    def test_no_network_client_in_the_capability(self):
        import app.core.entity_intelligence as pkg
        import app.evidence_sources as src_pkg
        for pkg_dir in (os.path.dirname(pkg.__file__), os.path.dirname(src_pkg.__file__)):
            for base, _d, files in os.walk(pkg_dir):
                for f in files:
                    if f.endswith(".py"):
                        with open(os.path.join(base, f), encoding="utf-8") as h:
                            text = h.read()
                        for word in ("import httpx", "import requests", "import aiohttp", "urllib.request", "socket."):
                            assert word not in text, (f, word)


class TestSchemaIsolation:
    def test_models_are_on_their_own_base(self):
        assert not (set(CoreBase.metadata.tables) & set(EI_TABLES))
        for name in EI_TABLES:
            assert name.startswith("ei_")

    def test_alembic_head_is_unchanged(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(os.path.join(ROOT, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        assert heads == [EXPECTED_ALEMBIC_HEAD], heads

    def test_no_new_file_in_alembic_versions(self):
        versions = sorted(f for f in os.listdir(os.path.join(ROOT, "alembic", "versions")) if f.endswith(".py"))
        assert not [f for f in versions if "entity" in f or "ei_" in f]

    def test_schema_creates_on_an_isolated_database(self):
        engine = create_engine("sqlite://")
        EntityIntelligenceBase.metadata.create_all(engine)
        assert set(sa_inspect(engine).get_table_names()) == set(EI_TABLES)

    def test_apply_script_refuses_non_local_urls(self):
        from app.core.entity_intelligence.migrations.apply import is_isolated
        assert is_isolated("sqlite:///x.db") and is_isolated("postgresql+asyncpg://u:p@127.0.0.1:5432/db")
        assert not is_isolated("postgresql+asyncpg://u:p@docuaction-db-dev.postgres.database.azure.com/db")


class TestIqviaStub:
    def test_status_and_no_invented_schema(self):
        assert STATUS.value == "AWAITING_SCHEMA"
        a = IQVIAOneKeyDeliveryAdapter()
        text = "col_a,col_b\n1,2\n3,4\n"
        inv = a.inventory(text)
        assert inv.fields == ["col_a", "col_b"] and inv.record_count == 2 and inv.schema_fingerprint
        assert a.propose_mapping(inv) == {"col_a": None, "col_b": None}
        assert a.preserve(b"abc")["sha256"].startswith("ba7816bf")
        src = inspect.getsource(inspect.getmodule(IQVIAOneKeyDeliveryAdapter))
        assert "api.iqvia" not in src and "API_KEY" not in src

    def test_refuses_observations_even_when_enabled(self, monkeypatch):
        enable_all(monkeypatch)
        with pytest.raises(SchemaUnknown):
            IQVIAOneKeyDeliveryAdapter().observations_for(canonical_entity_id="e", identifier="1", provenance=None)
        with pytest.raises(SchemaUnknown):
            IQVIAOneKeyDeliveryAdapter({"col_a": "hco_name"}).observations_for(canonical_entity_id="e", identifier="1", provenance=None)


class TestGoogleAndStateRegistry:
    def test_no_google_or_state_code_exists(self):
        for base, _d, files in os.walk(os.path.join(ROOT, "app", "evidence_sources")):
            for f in files:
                assert not re.search(r"google|maryland|virginia|california|opencorporates", f, re.I), f
        for base, _d, files in os.walk(os.path.join(ROOT, "app", "core", "entity_intelligence")):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(base, f), encoding="utf-8") as h:
                        text = h.read()
                    assert "googleapis" not in text and "addressvalidation" not in text.lower()
