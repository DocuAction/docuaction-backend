"""
Platform configuration models — Phase 1A (12 tables).

All tables are prefixed ``platform_`` to keep them clearly separated from the
existing application tables. They are the platform foundation and are created
BEFORE any TEFCA tables.

Design notes
------------
* Base: these models use the SHARED application Base (``app.core.database.Base``)
  — the same Base ``main.py``'s startup ``create_all`` and the platform Alembic
  migration operate on. (This mirrors ``app.Tefca.models``.)
* Enumerated string fields are plain ``VARCHAR`` columns (not native PG ENUM
  types); allowed values are documented in comments next to each column. This
  keeps the schema flexible — new values need no ``ALTER TYPE`` migration.
* Server-side defaults (``server_default``) are used for timestamps, booleans,
  and other DDL defaults so the values are applied even for rows inserted via
  raw SQL seed scripts in later phases.
* Circular FK (platform_tenants <-> platform_agencies) is broken with
  ``use_alter=True`` so ``create_all`` can order table creation.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Text, ForeignKey, Index, func, text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


# ─── platform_tenants ─────────────────────────────────────────────────────────

class PlatformTenant(Base):
    """Logical tenant separation. Single-tenant today, multi-tenant ready."""
    __tablename__ = "platform_tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_name = Column(String(500), nullable=False)
    tenant_code = Column(String(50), nullable=False)
    # federal, state, territory, tribal, military, commercial, academic
    tenant_type = Column(String(50), nullable=False)
    # active, inactive, suspended, onboarding
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    # Nullable — set after the agency/theme seed. use_alter breaks the
    # platform_tenants <-> platform_agencies FK cycle for create_all ordering.
    default_agency_id = Column(
        UUID(as_uuid=True),
        ForeignKey("platform_agencies.id", use_alter=True,
                   name="fk_platform_tenants_default_agency"),
        nullable=True,
    )
    default_theme_id = Column(
        UUID(as_uuid=True),
        ForeignKey("platform_themes.id", use_alter=True,
                   name="fk_platform_tenants_default_theme"),
        nullable=True,
    )
    configuration = Column(JSONB)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_platform_tenants_code", "tenant_code", unique=True),
        Index("idx_platform_tenants_status", "status"),
    )


# ─── platform_agencies ────────────────────────────────────────────────────────

class PlatformAgency(Base):
    """Federal agencies and organizational entities."""
    __tablename__ = "platform_agencies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable for shared agencies (not owned by a single tenant).
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("platform_tenants.id"), nullable=True)
    code = Column(String(20), nullable=False)
    name = Column(String(500), nullable=False)
    abbreviation = Column(String(20))
    parent_agency_id = Column(UUID(as_uuid=True), ForeignKey("platform_agencies.id"))
    # federal, state, territory, tribal, local, commercial, military, academic
    agency_type = Column(String(50), nullable=False)
    website = Column(String(500))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    sort_order = Column(Integer, server_default=text("0"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_platform_agencies_code", "code", unique=True),
        Index("idx_platform_agencies_active", "is_active"),
        Index("idx_platform_agencies_tenant", "tenant_id"),
    )


# ─── platform_programs ────────────────────────────────────────────────────────

class PlatformProgram(Base):
    """Programs within agencies."""
    __tablename__ = "platform_programs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agency_id = Column(UUID(as_uuid=True), ForeignKey("platform_agencies.id"), nullable=False)
    code = Column(String(50), nullable=False)
    name = Column(String(500), nullable=False)
    abbreviation = Column(String(20))
    description = Column(Text)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    sort_order = Column(Integer, server_default=text("0"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_platform_prog_code", "agency_id", "code", unique=True),
        Index("idx_platform_prog_agency", "agency_id"),
    )


# ─── platform_modules ─────────────────────────────────────────────────────────

class PlatformModule(Base):
    """Functional modules available in the platform."""
    __tablename__ = "platform_modules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), nullable=False)
    name = Column(String(500), nullable=False)
    description = Column(Text)
    # verification, compliance, import, analytics, reporting, administration,
    # audit, ai, connector, data_management
    module_type = Column(String(50), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    is_licensed = Column(Boolean, nullable=False, server_default=text("false"))
    sort_order = Column(Integer, server_default=text("0"))
    icon = Column(String(50))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_platform_mod_code", "code", unique=True),
    )


# ─── platform_workspaces ──────────────────────────────────────────────────────

class PlatformWorkspace(Base):
    """Workspaces link programs to modules."""
    __tablename__ = "platform_workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id = Column(UUID(as_uuid=True), ForeignKey("platform_programs.id"), nullable=False)
    module_id = Column(UUID(as_uuid=True), ForeignKey("platform_modules.id"), nullable=False)
    code = Column(String(50), nullable=False)
    name = Column(String(500), nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    configuration = Column(JSONB)
    sort_order = Column(Integer, server_default=text("0"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_platform_ws_code", "program_id", "code", unique=True),
        Index("idx_platform_ws_program", "program_id"),
        Index("idx_platform_ws_module", "module_id"),
    )


# ─── platform_pages ───────────────────────────────────────────────────────────

class PlatformPage(Base):
    """Navigation pages per workspace. Drives UI navigation from configuration."""
    __tablename__ = "platform_pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("platform_workspaces.id"), nullable=False)
    page_code = Column(String(50), nullable=False)
    page_name = Column(String(200), nullable=False)
    description = Column(Text)
    icon = Column(String(50))
    route = Column(String(500), nullable=False)
    display_order = Column(Integer, nullable=False, server_default=text("0"))
    is_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    required_permission = Column(String(100))
    parent_page_id = Column(UUID(as_uuid=True), ForeignKey("platform_pages.id"))
    # standard, dashboard, list, detail, form, report, settings, admin
    page_type = Column(String(50), server_default=text("'standard'"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_platform_pg_code", "workspace_id", "page_code", unique=True),
        Index("idx_platform_pg_workspace", "workspace_id"),
        Index("idx_platform_pg_order", "workspace_id", "display_order"),
        Index("idx_platform_pg_parent", "parent_page_id"),
    )


# ─── platform_features ────────────────────────────────────────────────────────

class PlatformFeature(Base):
    """Feature flags. Enable/disable capabilities without code changes."""
    __tablename__ = "platform_features"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    # ai, import, analytics, reporting, notifications, audit, documents, api,
    # fhir, integration, security
    feature_category = Column(String(50), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_platform_feat_code", "code", unique=True),
        Index("idx_platform_feat_category", "feature_category"),
    )


# ─── platform_workspace_features ──────────────────────────────────────────────

class PlatformWorkspaceFeature(Base):
    """Junction table: which features are enabled per workspace."""
    __tablename__ = "platform_workspace_features"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("platform_workspaces.id"), nullable=False)
    feature_id = Column(UUID(as_uuid=True), ForeignKey("platform_features.id"), nullable=False)
    is_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    configuration = Column(JSONB)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_platform_wsf_unique", "workspace_id", "feature_id", unique=True),
        Index("idx_platform_wsf_workspace", "workspace_id"),
    )


# ─── platform_data_sources ────────────────────────────────────────────────────

class PlatformDataSource(Base):
    """Registry of data sources for the universal import engine (config only)."""
    __tablename__ = "platform_data_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    # federal_api, state_api, fhir_server, file_upload, cloud_storage,
    # database, manual_entry, message_queue
    source_type = Column(String(50), nullable=False)
    # rest, soap, fhir_r4, sftp, s3, azure_blob, box, sharepoint,
    # database, hl7_v2, manual
    connection_type = Column(String(50))
    base_url = Column(String(1000))
    # none, api_key, oauth2, basic, certificate, smart_on_fhir, udap
    auth_type = Column(String(50))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    is_federal = Column(Boolean, nullable=False, server_default=text("false"))
    managing_agency = Column(String(50))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_platform_ds_code", "code", unique=True),
        Index("idx_platform_ds_type", "source_type"),
    )


# ─── platform_themes ──────────────────────────────────────────────────────────

class PlatformTheme(Base):
    """Branding and visual configuration per tenant/agency."""
    __tablename__ = "platform_themes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    theme_code = Column(String(50), nullable=False)
    theme_name = Column(String(200), nullable=False)
    primary_color = Column(String(7), nullable=False, server_default=text("'#0B3C5D'"))
    secondary_color = Column(String(7), nullable=False, server_default=text("'#0078D4'"))
    accent_color = Column(String(7), server_default=text("'#107C10'"))
    error_color = Column(String(7), server_default=text("'#C50F1F'"))
    warning_color = Column(String(7), server_default=text("'#F7630C'"))
    # standard, compact, minimal
    header_style = Column(String(50), server_default=text("'standard'"))
    # dark, light, branded
    sidebar_style = Column(String(50), server_default=text("'dark'"))
    logo_url = Column(String(500))
    favicon_url = Column(String(500))
    supports_dark_mode = Column(Boolean, nullable=False, server_default=text("true"))
    is_default = Column(Boolean, nullable=False, server_default=text("false"))
    custom_css = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                        onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_platform_theme_code", "theme_code", unique=True),
    )


# ─── platform_jurisdictions ───────────────────────────────────────────────────

class PlatformJurisdiction(Base):
    """Geographic jurisdictions — states, DC, territories, tribal nations, etc."""
    __tablename__ = "platform_jurisdictions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(10), nullable=False)
    name = Column(String(200), nullable=False)
    fips_code = Column(String(5))
    # state, district, territory, freely_associated, tribal_nation,
    # military_region, international, multi_state_area
    jurisdiction_type = Column(String(30), nullable=False)
    parent_jurisdiction_id = Column(UUID(as_uuid=True), ForeignKey("platform_jurisdictions.id"))
    # northeast, southeast, midwest, southwest, west, territory, pacific,
    # military, international
    region = Column(String(50))
    country = Column(String(2), server_default=text("'US'"))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_platform_juris_code", "code", unique=True),
        Index("idx_platform_juris_type", "jurisdiction_type"),
        Index("idx_platform_juris_parent", "parent_jurisdiction_id"),
    )


# ─── platform_import_formats ──────────────────────────────────────────────────

class PlatformImportFormat(Base):
    """Supported data import formats."""
    __tablename__ = "platform_import_formats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    mime_type = Column(String(200))
    file_extension = Column(String(20))
    description = Column(Text)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())


# ─── platform_identifier_types ────────────────────────────────────────────────

class PlatformIdentifierType(Base):
    """Registry of all healthcare identifier types."""
    __tablename__ = "platform_identifier_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), nullable=False)
    name = Column(String(200), nullable=False)
    system_uri = Column(String(500))
    description = Column(Text)
    format_pattern = Column(String(200))
    issuing_authority = Column(String(500))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_platform_idt_code", "code", unique=True),
    )


# Ordered parents-first so FK-scoped create_all / drop_all can resolve
# dependencies. Consumed by the platform Alembic migration.
PLATFORM_TABLE_ORDER = [
    "platform_themes",
    "platform_tenants",
    "platform_agencies",
    "platform_programs",
    "platform_modules",
    "platform_workspaces",
    "platform_pages",
    "platform_features",
    "platform_workspace_features",
    "platform_data_sources",
    "platform_jurisdictions",
    "platform_import_formats",
    "platform_identifier_types",
]
