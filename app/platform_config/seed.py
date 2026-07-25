"""
Platform configuration seed — Phase 1A-SEED.

Populates all 13 ``platform_*`` configuration tables with the baseline reference
data for the AGT / HHS-ONC TEFCA Review Protocol deployment.

Design
------
* **Idempotent.** Every row is written with a PostgreSQL ``INSERT ... ON CONFLICT``
  upsert keyed on the table's natural-key unique index, so re-running never
  duplicates and always converges to the values defined here.
* **Deterministic IDs.** Each row's UUID is derived with ``uuid5`` from its
  natural key, so foreign-key references between tables can be wired without a
  round-trip, and IDs are stable across environments and re-runs.
* **FK order.** Tables are seeded parents-first (themes/tenants -> agencies ->
  programs -> workspaces -> pages, etc.).
* **Standalone.** ``python -m app.platform_config.seed`` creates the platform
  tables if missing (idempotent), seeds them, validates row counts against the
  Phase 1A-SEED targets, and prints a per-table report.

Note: importing ``app.core.database`` does NOT import ``app.core.config``, so
this script runs with only ``DATABASE_URL`` set (no SECRET_KEY requirement).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import Base, async_session_maker, engine
from app.platform_config import models as m

logger = logging.getLogger("docuaction.platform.seed")

# Fixed namespace for deterministic row IDs. Do not change — it anchors every
# seeded UUID and all FK references.
_NS = uuid.UUID("1e5b0c9a-2f4d-4b6e-8a1c-9d3e5f7a0b2c")


def _id(table: str, key: str) -> uuid.UUID:
    """Deterministic UUID for (table, natural-key)."""
    return uuid.uuid5(_NS, f"{table}:{key}")


# Expected row counts after seeding (Phase 1A-SEED targets).
EXPECTED = {
    "platform_tenants": 1,
    "platform_agencies": 13,
    "platform_programs": 14,
    "platform_modules": 16,
    "platform_workspaces": 4,
    "platform_pages": 11,
    "platform_features": 9,
    "platform_workspace_features": 8,
    "platform_data_sources": 14,
    "platform_themes": 2,
    "platform_jurisdictions": 57,
    "platform_import_formats": 11,
    "platform_identifier_types": 18,
}


# ══════════════════════════════════════════════════════════════════════════════
# Upsert helper
# ══════════════════════════════════════════════════════════════════════════════

async def _upsert(session, model, rows, conflict_cols):
    """Multi-row INSERT ... ON CONFLICT upsert.

    ``rows`` must all share the same set of keys. On conflict (by the unique
    index over ``conflict_cols``) every non-key column present in the rows is
    refreshed from the incoming values (id and created_at are never updated).
    """
    if not rows:
        return
    table = model.__table__
    stmt = pg_insert(table).values(rows)
    present = set(rows[0].keys())
    update = {
        name: getattr(stmt.excluded, name)
        for name in present
        if name not in ("id", "created_at") and name not in conflict_cols
    }
    if update:
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
    await session.execute(stmt)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Themes
# ══════════════════════════════════════════════════════════════════════════════

def _themes():
    return [
        dict(
            id=_id("platform_themes", "FEDERAL"),
            theme_code="FEDERAL", theme_name="Federal (Azure Fluent 2)",
            primary_color="#0B3C5D", secondary_color="#0078D4",
            accent_color="#107C10", error_color="#C50F1F", warning_color="#F7630C",
            header_style="standard", sidebar_style="dark",
            logo_url=None, favicon_url=None,
            supports_dark_mode=True, is_default=True, custom_css=None,
        ),
        dict(
            id=_id("platform_themes", "COMMERCIAL"),
            theme_code="COMMERCIAL", theme_name="Commercial (Light)",
            primary_color="#0078D4", secondary_color="#2B88D8",
            accent_color="#107C10", error_color="#C50F1F", warning_color="#F7630C",
            header_style="compact", sidebar_style="light",
            logo_url=None, favicon_url=None,
            supports_dark_mode=True, is_default=False, custom_css=None,
        ),
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 2. Tenant
# ══════════════════════════════════════════════════════════════════════════════

def _tenants():
    return [
        dict(
            id=_id("platform_tenants", "AGT_HHS"),
            tenant_name="AGT — HHS/ONC TEFCA Review Protocol",
            tenant_code="AGT_HHS", tenant_type="federal", status="active",
            default_agency_id=_id("platform_agencies", "HHS"),
            default_theme_id=_id("platform_themes", "FEDERAL"),
            configuration={"contract": "7571MN26F80064", "program": "TEFCA"},
        ),
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 3. Agencies (HHS parent + operating divisions, plus VA/DOD/State Medicaid)
# ══════════════════════════════════════════════════════════════════════════════

def _agencies():
    # (code, name, abbreviation, parent_code, agency_type, website)
    data = [
        ("HHS", "U.S. Department of Health and Human Services", "HHS", None, "federal", "https://www.hhs.gov"),
        ("CMS", "Centers for Medicare & Medicaid Services", "CMS", "HHS", "federal", "https://www.cms.gov"),
        ("CDC", "Centers for Disease Control and Prevention", "CDC", "HHS", "federal", "https://www.cdc.gov"),
        ("FDA", "U.S. Food and Drug Administration", "FDA", "HHS", "federal", "https://www.fda.gov"),
        ("HRSA", "Health Resources and Services Administration", "HRSA", "HHS", "federal", "https://www.hrsa.gov"),
        ("AHRQ", "Agency for Healthcare Research and Quality", "AHRQ", "HHS", "federal", "https://www.ahrq.gov"),
        ("ACL", "Administration for Community Living", "ACL", "HHS", "federal", "https://acl.gov"),
        ("ASPR", "Administration for Strategic Preparedness and Response", "ASPR", "HHS", "federal", "https://aspr.hhs.gov"),
        ("IHS", "Indian Health Service", "IHS", "HHS", "federal", "https://www.ihs.gov"),
        ("ONC", "Assistant Secretary for Technology Policy / Office of the National Coordinator for Health IT", "ONC", "HHS", "federal", "https://www.healthit.gov"),
        ("VA", "U.S. Department of Veterans Affairs", "VA", None, "federal", "https://www.va.gov"),
        ("DOD", "U.S. Department of Defense", "DOD", None, "military", "https://www.defense.gov"),
        ("STATE_MEDICAID", "State Medicaid Agencies", "Medicaid", None, "state", None),
    ]
    rows = []
    for i, (code, name, abbr, parent, atype, site) in enumerate(data, start=1):
        rows.append(dict(
            id=_id("platform_agencies", code),
            tenant_id=None,  # shared federal reference registry
            code=code, name=name, abbreviation=abbr,
            parent_agency_id=_id("platform_agencies", parent) if parent else None,
            agency_type=atype, website=site,
            is_active=True, sort_order=i * 10,
        ))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 4. Programs (within agencies)
# ══════════════════════════════════════════════════════════════════════════════

def _programs():
    # (agency_code, code, name, abbreviation, description)
    data = [
        ("CMS", "MEDICARE", "Medicare", "Medicare", "Federal health insurance for people 65+ and certain younger people with disabilities."),
        ("CMS", "MEDICAID", "Medicaid", "Medicaid", "Joint federal-state program covering low-income individuals and families."),
        ("CMS", "QPP", "Quality Payment Program", "QPP", "Value-based payment program (MIPS / Advanced APMs)."),
        ("CMS", "PROVIDER_ENROLLMENT", "Provider Enrollment (PECOS)", "PECOS", "Medicare provider enrollment, chain, and ownership system."),
        ("ONC", "TEFCA", "Trusted Exchange Framework and Common Agreement", "TEFCA", "Nationwide health information exchange framework and common agreement."),
        ("ONC", "HEALTH_IT_CERT", "Health IT Certification Program", "CHPL", "ONC certification program for health IT products."),
        ("ONC", "USCDI", "United States Core Data for Interoperability", "USCDI", "Standardized set of health data classes and elements for exchange."),
        ("HRSA", "DRUG_PRICING_340B", "340B Drug Pricing Program", "340B", "Discount drug pricing for eligible covered entities."),
        ("HRSA", "NHSC", "National Health Service Corps", "NHSC", "Loan repayment / scholarships for clinicians in underserved areas."),
        ("CDC", "NNDSS", "National Notifiable Diseases Surveillance System", "NNDSS", "Nationwide public-health surveillance for notifiable conditions."),
        ("FDA", "UDI", "Unique Device Identification System", "UDI", "Identification system for medical devices through distribution and use."),
        ("VA", "VHIE", "Veterans Health Information Exchange", "VHIE", "Secure exchange of veterans' health data with community providers."),
        ("DOD", "MHS_GENESIS", "MHS GENESIS", "MHS GENESIS", "Department of Defense electronic health record system."),
        ("IHS", "RPMS", "Resource and Patient Management System", "RPMS", "IHS integrated clinical and administrative information system."),
    ]
    rows = []
    for i, (agency, code, name, abbr, desc) in enumerate(data, start=1):
        rows.append(dict(
            id=_id("platform_programs", code),
            agency_id=_id("platform_agencies", agency),
            code=code, name=name, abbreviation=abbr, description=desc,
            is_active=True, sort_order=i * 10,
        ))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 5. Modules
# ══════════════════════════════════════════════════════════════════════════════

def _modules():
    # (code, name, module_type, icon, is_licensed)
    data = [
        ("TEFCA_REVIEW", "TEFCA Review", "verification", "ShieldCheckmark", False),
        ("ENTITY_VERIFICATION", "Entity Verification", "verification", "ContactCard", False),
        ("COMPLIANCE", "Compliance Engine", "compliance", "ClipboardTaskList", False),
        ("IMPORT", "Universal Import", "import", "CloudArrowUp", False),
        ("ANALYTICS", "Analytics Dashboard", "analytics", "DataTrending", False),
        ("REPORTING", "Reporting", "reporting", "DocumentBulletList", False),
        ("ADMIN", "Administration", "administration", "Settings", False),
        ("AUDIT", "Audit Trail", "audit", "History", False),
        ("AI_CLASSIFY", "AI Classification", "ai", "BrainCircuit", True),
        ("CONNECTORS", "Connectors", "connector", "PlugConnected", False),
        ("DATA_MGMT", "Data Management", "data_management", "Database", False),
        ("BULLETIN", "Bulletin Intelligence", "analytics", "News", True),
        ("DOCUMENT", "Document Intelligence", "ai", "DocumentSearch", True),
        ("VOICE", "Voice Intelligence", "ai", "Mic", True),
        ("MIGRATION", "Migration Intelligence", "data_management", "ArrowSwap", True),
        ("QA_MONITOR", "QA Monitor", "audit", "CheckmarkStarburst", False),
    ]
    rows = []
    for i, (code, name, mtype, icon, licensed) in enumerate(data, start=1):
        rows.append(dict(
            id=_id("platform_modules", code),
            code=code, name=name, description=f"{name} module.",
            module_type=mtype, is_active=True, is_licensed=licensed,
            sort_order=i * 10, icon=icon,
        ))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 6. Workspaces (TEFCA program x TEFCA_REVIEW module)
# ══════════════════════════════════════════════════════════════════════════════

def _workspaces():
    prog = _id("platform_programs", "TEFCA")
    module = _id("platform_modules", "TEFCA_REVIEW")
    # (code, name, description)
    data = [
        ("TEFCA_RETROSPECTIVE", "Task 3 — Retrospective Review", "Statistical retrospective review of TEFCA entities."),
        ("TEFCA_ONGOING", "Task 4 — Ongoing Monitoring", "Continuous ongoing monitoring and review cycles."),
        ("TEFCA_PRIORITY", "Task 5 — Priority Cases", "COR-directed priority case reviews."),
        ("TEFCA_DASHBOARD", "Executive Dashboard", "Executive reporting and connector health dashboard."),
    ]
    rows = []
    for i, (code, name, desc) in enumerate(data, start=1):
        rows.append(dict(
            id=_id("platform_workspaces", code),
            program_id=prog, module_id=module,
            code=code, name=name, description=desc,
            is_active=True, configuration=None, sort_order=i * 10,
        ))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 7. Pages
# ══════════════════════════════════════════════════════════════════════════════

def _pages():
    # (page_code, workspace_code, page_name, icon, route, order, permission, page_type)
    data = [
        ("DASHBOARD", "TEFCA_DASHBOARD", "Dashboard", "Home", "/tefca/dashboard", 1, "tefca.view", "dashboard"),
        ("ENTITIES", "TEFCA_DASHBOARD", "Entities", "People", "/tefca/entities", 2, "tefca.view", "list"),
        ("ENTITY_DETAIL", "TEFCA_DASHBOARD", "Entity Detail", "ContactCard", "/tefca/entities/:id", 3, "tefca.view", "detail"),
        ("EVIDENCE", "TEFCA_DASHBOARD", "Evidence Records", "DocumentSearch", "/tefca/evidence", 4, "tefca.view", "list"),
        ("REPORTS", "TEFCA_DASHBOARD", "Reports", "DocumentBulletList", "/tefca/reports", 5, "tefca.report", "report"),
        ("CONNECTORS", "TEFCA_DASHBOARD", "Connector Health", "PlugConnected", "/tefca/connectors", 6, "tefca.admin", "admin"),
        ("IMPORT", "TEFCA_DASHBOARD", "Import", "CloudArrowUp", "/tefca/import", 7, "tefca.import", "form"),
        ("SETTINGS", "TEFCA_DASHBOARD", "Settings", "Settings", "/tefca/settings", 8, "tefca.admin", "settings"),
        ("RETROSPECTIVE_QUEUE", "TEFCA_RETROSPECTIVE", "Retrospective Review", "History", "/tefca/retrospective", 1, "tefca.review", "list"),
        ("ONGOING_QUEUE", "TEFCA_ONGOING", "Review Queue", "TaskListSquareLtr", "/tefca/queue", 1, "tefca.review", "list"),
        ("PRIORITY_CASES", "TEFCA_PRIORITY", "Priority Cases", "Warning", "/tefca/priority", 1, "tefca.review", "list"),
    ]
    rows = []
    for page_code, ws_code, name, icon, route, order, perm, ptype in data:
        rows.append(dict(
            id=_id("platform_pages", page_code),
            workspace_id=_id("platform_workspaces", ws_code),
            page_code=page_code, page_name=name, description=name,
            icon=icon, route=route, display_order=order,
            is_enabled=True, required_permission=perm,
            parent_page_id=None, page_type=ptype,
        ))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 8. Features
# ══════════════════════════════════════════════════════════════════════════════

def _features():
    # (code, name, feature_category)
    data = [
        ("AI_CLASSIFICATION", "AI Classification", "ai"),
        ("BULK_IMPORT", "Bulk Import", "import"),
        ("ADVANCED_ANALYTICS", "Advanced Analytics", "analytics"),
        ("SCHEDULED_REPORTS", "Scheduled Reports", "reporting"),
        ("EMAIL_ALERTS", "Email Alerts", "notifications"),
        ("AUDIT_LOGGING", "Audit Logging", "audit"),
        ("FHIR_EXCHANGE", "FHIR Exchange", "fhir"),
        ("API_ACCESS", "API Access", "api"),
        ("SSO_INTEGRATION", "SSO Integration", "security"),
    ]
    return [
        dict(
            id=_id("platform_features", code),
            code=code, name=name, description=f"{name} capability.",
            feature_category=cat, is_active=True,
        )
        for code, name, cat in data
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 9. Workspace features (enable features per TEFCA workspace)
# ══════════════════════════════════════════════════════════════════════════════

def _workspace_features():
    # (workspace_code, feature_code)
    pairs = [
        ("TEFCA_DASHBOARD", "ADVANCED_ANALYTICS"),
        ("TEFCA_DASHBOARD", "SCHEDULED_REPORTS"),
        ("TEFCA_DASHBOARD", "AUDIT_LOGGING"),
        ("TEFCA_RETROSPECTIVE", "AI_CLASSIFICATION"),
        ("TEFCA_RETROSPECTIVE", "BULK_IMPORT"),
        ("TEFCA_ONGOING", "AI_CLASSIFICATION"),
        ("TEFCA_ONGOING", "EMAIL_ALERTS"),
        ("TEFCA_PRIORITY", "FHIR_EXCHANGE"),
    ]
    return [
        dict(
            id=_id("platform_workspace_features", f"{ws}:{feat}"),
            workspace_id=_id("platform_workspaces", ws),
            feature_id=_id("platform_features", feat),
            is_enabled=True, configuration=None,
        )
        for ws, feat in pairs
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 10. Data sources
# ══════════════════════════════════════════════════════════════════════════════

def _data_sources():
    # (code, name, source_type, connection_type, base_url, auth_type, is_federal, managing_agency)
    data = [
        ("NPPES", "NPPES NPI Registry", "federal_api", "rest", "https://npiregistry.cms.hhs.gov/api", "none", True, "CMS"),
        ("PECOS", "Medicare PECOS Enrollment", "federal_api", "rest", "https://data.cms.gov/provider-data", "none", True, "CMS"),
        ("LEIE", "OIG List of Excluded Individuals/Entities", "federal_api", "rest", "https://oig.hhs.gov/exclusions", "none", True, "HHS"),
        ("SAM", "SAM.gov Entity Management", "federal_api", "rest", "https://api.sam.gov", "api_key", True, "GSA"),
        ("RCE", "RCE / Recognized Coordinating Entity Directory", "fhir_server", "fhir_r4", "https://rce.sequoiaproject.org", "api_key", False, "ONC"),
        ("ONC_BOX", "ONC Box Document Repository", "cloud_storage", "box", "https://api.box.com/2.0", "oauth2", False, "ONC"),
        ("RXNORM", "NLM RxNorm", "federal_api", "rest", "https://rxnav.nlm.nih.gov/REST", "none", True, "NIH"),
        ("UMLS", "NLM UMLS Terminology Services", "federal_api", "rest", "https://uts-ws.nlm.nih.gov/rest", "api_key", True, "NIH"),
        ("CDC_NNDSS", "CDC NNDSS Data", "federal_api", "rest", "https://data.cdc.gov/resource", "none", True, "CDC"),
        ("FDA_GUDID", "FDA AccessGUDID", "federal_api", "rest", "https://accessgudid.nlm.nih.gov/api", "none", True, "FDA"),
        ("HRSA_340B", "HRSA 340B OPAIS", "federal_api", "rest", "https://340bopais.hrsa.gov", "none", True, "HRSA"),
        ("FHIR_R4_ENDPOINT", "Generic FHIR R4 Endpoint", "fhir_server", "fhir_r4", None, "smart_on_fhir", False, None),
        ("STATE_MEDICAID_API", "State Medicaid Provider API", "state_api", "rest", None, "api_key", False, "STATE_MEDICAID"),
        ("MANUAL_UPLOAD", "Manual File Upload", "file_upload", "manual", None, "none", False, None),
    ]
    rows = []
    for code, name, stype, ctype, url, auth, fed, agency in data:
        rows.append(dict(
            id=_id("platform_data_sources", code),
            code=code, name=name, description=name,
            source_type=stype, connection_type=ctype, base_url=url,
            auth_type=auth, is_active=True, is_federal=fed, managing_agency=agency,
        ))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 11. Jurisdictions (50 states + DC + 6 territories)
# ══════════════════════════════════════════════════════════════════════════════

def _jurisdictions():
    # (code, name, fips, region)  — all 50 states, jurisdiction_type=state
    states = [
        ("AL", "Alabama", "01", "southeast"), ("AK", "Alaska", "02", "west"),
        ("AZ", "Arizona", "04", "southwest"), ("AR", "Arkansas", "05", "southeast"),
        ("CA", "California", "06", "west"), ("CO", "Colorado", "08", "west"),
        ("CT", "Connecticut", "09", "northeast"), ("DE", "Delaware", "10", "southeast"),
        ("FL", "Florida", "12", "southeast"), ("GA", "Georgia", "13", "southeast"),
        ("HI", "Hawaii", "15", "west"), ("ID", "Idaho", "16", "west"),
        ("IL", "Illinois", "17", "midwest"), ("IN", "Indiana", "18", "midwest"),
        ("IA", "Iowa", "19", "midwest"), ("KS", "Kansas", "20", "midwest"),
        ("KY", "Kentucky", "21", "southeast"), ("LA", "Louisiana", "22", "southeast"),
        ("ME", "Maine", "23", "northeast"), ("MD", "Maryland", "24", "southeast"),
        ("MA", "Massachusetts", "25", "northeast"), ("MI", "Michigan", "26", "midwest"),
        ("MN", "Minnesota", "27", "midwest"), ("MS", "Mississippi", "28", "southeast"),
        ("MO", "Missouri", "29", "midwest"), ("MT", "Montana", "30", "west"),
        ("NE", "Nebraska", "31", "midwest"), ("NV", "Nevada", "32", "west"),
        ("NH", "New Hampshire", "33", "northeast"), ("NJ", "New Jersey", "34", "northeast"),
        ("NM", "New Mexico", "35", "southwest"), ("NY", "New York", "36", "northeast"),
        ("NC", "North Carolina", "37", "southeast"), ("ND", "North Dakota", "38", "midwest"),
        ("OH", "Ohio", "39", "midwest"), ("OK", "Oklahoma", "40", "southwest"),
        ("OR", "Oregon", "41", "west"), ("PA", "Pennsylvania", "42", "northeast"),
        ("RI", "Rhode Island", "44", "northeast"), ("SC", "South Carolina", "45", "southeast"),
        ("SD", "South Dakota", "46", "midwest"), ("TN", "Tennessee", "47", "southeast"),
        ("TX", "Texas", "48", "southwest"), ("UT", "Utah", "49", "west"),
        ("VT", "Vermont", "50", "northeast"), ("VA", "Virginia", "51", "southeast"),
        ("WA", "Washington", "53", "west"), ("WV", "West Virginia", "54", "southeast"),
        ("WI", "Wisconsin", "55", "midwest"), ("WY", "Wyoming", "56", "west"),
    ]
    # (code, name, fips, region) — DC, jurisdiction_type=district
    district = [("DC", "District of Columbia", "11", "southeast")]
    # (code, name, fips, region) — territories, jurisdiction_type=territory
    territories = [
        ("PR", "Puerto Rico", "72", "territory"),
        ("VI", "U.S. Virgin Islands", "78", "territory"),
        ("GU", "Guam", "66", "pacific"),
        ("AS", "American Samoa", "60", "pacific"),
        ("MP", "Northern Mariana Islands", "69", "pacific"),
        ("UM", "U.S. Minor Outlying Islands", "74", "pacific"),
    ]
    rows = []
    for code, name, fips, region in states:
        rows.append(_juris_row(code, name, fips, "state", region))
    for code, name, fips, region in district:
        rows.append(_juris_row(code, name, fips, "district", region))
    for code, name, fips, region in territories:
        rows.append(_juris_row(code, name, fips, "territory", region))
    return rows


def _juris_row(code, name, fips, jtype, region):
    return dict(
        id=_id("platform_jurisdictions", code),
        code=code, name=name, fips_code=fips,
        jurisdiction_type=jtype, parent_jurisdiction_id=None,
        region=region, country="US", is_active=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 12. Import formats
# ══════════════════════════════════════════════════════════════════════════════

def _import_formats():
    # (code, name, mime_type, file_extension)
    data = [
        ("FHIR_R4", "FHIR R4 Resource (JSON)", "application/fhir+json", ".json"),
        ("FHIR_BUNDLE", "FHIR Bundle", "application/fhir+json", ".json"),
        ("CSV", "Comma-Separated Values", "text/csv", ".csv"),
        ("EXCEL_XLSX", "Excel Workbook (XLSX)", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
        ("EXCEL_XLS", "Excel 97-2003 (XLS)", "application/vnd.ms-excel", ".xls"),
        ("JSON", "JSON", "application/json", ".json"),
        ("NDJSON", "Newline-Delimited JSON", "application/x-ndjson", ".ndjson"),
        ("XML", "XML", "application/xml", ".xml"),
        ("HL7_V2", "HL7 v2.x Message", "application/hl7-v2+er7", ".hl7"),
        ("CDA_CCDA", "Consolidated CDA (C-CDA)", "application/xml", ".xml"),
        ("PDF", "PDF Document", "application/pdf", ".pdf"),
    ]
    return [
        dict(
            id=_id("platform_import_formats", code),
            code=code, name=name, mime_type=mime, file_extension=ext,
            description=name, is_active=True,
        )
        for code, name, mime, ext in data
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 13. Identifier types
# ══════════════════════════════════════════════════════════════════════════════

def _identifier_types():
    # (code, name, system_uri, format_pattern, issuing_authority)
    data = [
        ("NPI", "National Provider Identifier", "http://hl7.org/fhir/sid/us-npi", r"^\d{10}$", "CMS / NPPES"),
        ("HCID", "TEFCA Health Care Identifier", "urn:tefca:hcid", None, "ONC / RCE"),
        ("TEFCAID", "TEFCA Identifier", "urn:tefca:tefcaid", None, "ONC / RCE"),
        ("CCN", "CMS Certification Number", "urn:oid:2.16.840.1.113883.4.336", r"^[0-9A-Z]{6,10}$", "CMS"),
        ("CLIA", "CLIA Laboratory Number", "urn:oid:2.16.840.1.113883.4.7", r"^\d{2}[A-Z]\d{7}$", "CMS"),
        ("UEI", "Unique Entity Identifier (SAM.gov)", "https://sam.gov", r"^[A-Z0-9]{12}$", "GSA"),
        ("DUNS", "Data Universal Numbering System", "urn:oid:1.3.6.1.4.1.343", r"^\d{9}$", "Dun & Bradstreet"),
        ("EIN", "Employer Identification Number", "urn:oid:2.16.840.1.113883.4.4", r"^\d{2}-?\d{7}$", "IRS"),
        ("DEA", "DEA Registration Number", "urn:oid:2.16.840.1.113883.4.814", r"^[A-Z]{2}\d{7}$", "DEA"),
        ("MBI", "Medicare Beneficiary Identifier", "http://hl7.org/fhir/sid/us-mbi", r"^[0-9A-Z]{11}$", "CMS"),
        ("MEDICAID_ID", "State Medicaid Provider ID", None, None, "State Medicaid Agency"),
        ("PAC_ID", "PECOS Associate Control ID", "https://pecos.cms.hhs.gov", r"^\d{10}$", "CMS"),
        ("TAXONOMY", "Healthcare Provider Taxonomy Code", "http://nucc.org/provider-taxonomy", r"^[0-9A-Z]{10}$", "NUCC"),
        ("NCPDP", "NCPDP Provider Identification Number", "urn:oid:2.16.840.1.113883.3.88", r"^\d{7}$", "NCPDP"),
        ("SSN", "Social Security Number", "http://hl7.org/fhir/sid/us-ssn", r"^\d{3}-?\d{2}-?\d{4}$", "SSA"),
        ("OID", "ISO Object Identifier", "urn:ietf:rfc:3986", r"^[0-2](\.\d+)+$", "ISO/HL7"),
        ("UUID", "Universally Unique Identifier", "urn:ietf:rfc:4122", r"^[0-9a-fA-F-]{36}$", "IETF"),
        ("UPIN", "Unique Physician Identification Number (legacy)", "urn:oid:2.16.840.1.113883.4.5", r"^[A-Z]\d{5}$", "CMS"),
    ]
    return [
        dict(
            id=_id("platform_identifier_types", code),
            code=code, name=name, system_uri=uri, description=name,
            format_pattern=pattern, issuing_authority=auth, is_active=True,
        )
        for code, name, uri, pattern, auth in data
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Orchestration
# ══════════════════════════════════════════════════════════════════════════════

# (model, rows-builder, conflict-columns) in FK-safe order.
_SEED_PLAN = [
    (m.PlatformTheme, _themes, ["theme_code"]),
    # Agencies before tenants: the tenant's default_agency_id references HHS.
    # (Agencies' tenant_id is left NULL — shared registry — so there is no
    # reverse data dependency, even though the schema FK cycle exists.)
    (m.PlatformAgency, _agencies, ["code"]),
    (m.PlatformTenant, _tenants, ["tenant_code"]),
    (m.PlatformProgram, _programs, ["agency_id", "code"]),
    (m.PlatformModule, _modules, ["code"]),
    (m.PlatformWorkspace, _workspaces, ["program_id", "code"]),
    (m.PlatformPage, _pages, ["workspace_id", "page_code"]),
    (m.PlatformFeature, _features, ["code"]),
    (m.PlatformWorkspaceFeature, _workspace_features, ["workspace_id", "feature_id"]),
    (m.PlatformDataSource, _data_sources, ["code"]),
    (m.PlatformJurisdiction, _jurisdictions, ["code"]),
    (m.PlatformImportFormat, _import_formats, ["code"]),
    (m.PlatformIdentifierType, _identifier_types, ["code"]),
]


async def seed_all(session) -> None:
    """Idempotently upsert every platform table (FK-safe order)."""
    for model, builder, conflict_cols in _SEED_PLAN:
        rows = builder()
        await _upsert(session, model, rows, conflict_cols)
        logger.info("seeded %s (%d rows)", model.__tablename__, len(rows))


async def _ensure_tables() -> None:
    """Create the platform tables if missing (idempotent)."""
    tables = [Base.metadata.tables[t] for t in m.PLATFORM_TABLE_ORDER]
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=tables, checkfirst=True)
        )


async def validate(session) -> dict:
    """Return {table: count} for every platform table."""
    counts = {}
    for table in EXPECTED:
        n = await session.scalar(
            select(func.count()).select_from(Base.metadata.tables[table])
        )
        counts[table] = int(n or 0)
    return counts


async def run() -> dict:
    """Ensure tables, seed, validate. Returns the count report."""
    await _ensure_tables()
    async with async_session_maker() as session:
        await seed_all(session)
        await session.commit()
        return await validate(session)


def _print_report(counts: dict) -> bool:
    print("\nPlatform seed — record counts")
    print("=" * 52)
    print(f"{'table':<32}{'count':>8}{'expected':>8}  ok")
    print("-" * 52)
    all_ok = True
    for table, expected in EXPECTED.items():
        actual = counts.get(table, 0)
        ok = actual == expected
        all_ok &= ok
        print(f"{table:<32}{actual:>8}{expected:>8}  {'OK' if ok else 'MISMATCH'}")
    print("=" * 52)
    print("RESULT:", "ALL COUNTS MATCH" if all_ok else "COUNT MISMATCH — see above")
    return all_ok


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    counts = asyncio.run(run())
    ok = _print_report(counts)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
