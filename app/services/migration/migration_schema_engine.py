"""
DocuAction — Migration Schema Engine
AI-powered schema understanding, profiling, and logic extraction.

Architecture:
  - Self-contained service (no imports from Documents, Audio, or Healthcare modules)
  - Uses Anthropic Claude for AI analysis
  - Handles massive schemas via domain chunking (Tier 1-4 architecture)
  - PII/PHI detection integrated at profiling stage
"""
import re
import json
import uuid
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger("docuaction.migration.schema")


# ═══════════════════════════════════════════════════════
# SCHEMA PARSING (DDL, CSV, API definitions)
# ═══════════════════════════════════════════════════════

def parse_ddl(ddl_text: str) -> dict:
    """Parse SQL DDL statements into structured schema."""
    tables = {}
    current_table = None

    # Regex for CREATE TABLE
    table_pattern = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?(\w+)["`]?\s*\(', re.IGNORECASE)
    # Regex for column definitions
    col_pattern = re.compile(r'^\s*["`]?(\w+)["`]?\s+([\w\(\),\s]+?)(?:\s+(NOT\s+NULL|NULL|PRIMARY\s+KEY|DEFAULT|REFERENCES|UNIQUE|CHECK).*)?[,\)]?\s*$', re.IGNORECASE)
    # FK pattern
    fk_pattern = re.compile(r'FOREIGN\s+KEY\s*\(["`]?(\w+)["`]?\)\s*REFERENCES\s*["`]?(\w+)["`]?\s*\(["`]?(\w+)["`]?\)', re.IGNORECASE)

    for line in ddl_text.split('\n'):
        line = line.strip()
        if not line or line.startswith('--'):
            continue

        # Detect new table
        tm = table_pattern.search(line)
        if tm:
            current_table = tm.group(1)
            tables[current_table] = {"fields": [], "primary_keys": [], "foreign_keys": [], "constraints": []}
            continue

        if current_table and tables.get(current_table) is not None:
            # Detect column
            cm = col_pattern.match(line)
            if cm and cm.group(1).upper() not in ('PRIMARY', 'FOREIGN', 'CONSTRAINT', 'INDEX', 'UNIQUE', 'CHECK', 'CREATE', 'ALTER'):
                field = {
                    "name": cm.group(1),
                    "data_type": cm.group(2).strip().rstrip(','),
                    "nullable": 'NOT NULL' not in (cm.group(3) or '').upper(),
                    "is_pk": 'PRIMARY KEY' in (cm.group(3) or '').upper(),
                }
                tables[current_table]["fields"].append(field)
                if field["is_pk"]:
                    tables[current_table]["primary_keys"].append(field["name"])

            # Detect foreign keys
            fkm = fk_pattern.search(line)
            if fkm:
                tables[current_table]["foreign_keys"].append({
                    "field": fkm.group(1),
                    "references_table": fkm.group(2),
                    "references_field": fkm.group(3),
                })

        # Detect end of table
        if line.startswith(')'):
            current_table = None

    return {
        "parser": "ddl",
        "tables": tables,
        "table_count": len(tables),
        "total_fields": sum(len(t["fields"]) for t in tables.values()),
        "total_relationships": sum(len(t["foreign_keys"]) for t in tables.values()),
    }


def parse_csv_schema(csv_text: str) -> dict:
    """Parse CSV schema definition (table_name, field_name, data_type, nullable, ...)."""
    import csv
    from io import StringIO

    reader = csv.DictReader(StringIO(csv_text))
    tables = {}

    for row in reader:
        table = row.get("table_name") or row.get("TABLE_NAME") or row.get("table") or "unknown"
        field = row.get("field_name") or row.get("COLUMN_NAME") or row.get("column") or row.get("field") or ""
        dtype = row.get("data_type") or row.get("DATA_TYPE") or row.get("type") or ""

        if table not in tables:
            tables[table] = {"fields": [], "primary_keys": [], "foreign_keys": [], "constraints": []}

        tables[table]["fields"].append({
            "name": field,
            "data_type": dtype,
            "nullable": (row.get("nullable") or row.get("IS_NULLABLE") or "YES").upper() != "NO",
            "is_pk": (row.get("is_pk") or row.get("PRIMARY_KEY") or "").upper() in ("YES", "TRUE", "1"),
        })

    return {
        "parser": "csv",
        "tables": tables,
        "table_count": len(tables),
        "total_fields": sum(len(t["fields"]) for t in tables.values()),
        "total_relationships": 0,
    }


# ═══════════════════════════════════════════════════════
# AI SCHEMA ANALYSIS
# ═══════════════════════════════════════════════════════

async def analyze_schema(parsed_schema: dict, system_type: str = "unknown") -> dict:
    """
    Send parsed schema to Claude for AI-powered analysis.
    Uses domain chunking for large schemas (Tier 1-4 architecture).
    """
    analysis_id = "MSAN-" + uuid.uuid4().hex[:8].upper()
    start_time = datetime.utcnow()

    tables = parsed_schema.get("tables", {})
    table_count = len(tables)

    # Tier selection based on schema size
    if table_count <= 100:
        # Small schema — process in single pass
        result = await _analyze_single_pass(tables, system_type)
    elif table_count <= 2000:
        # Medium schema — domain chunking
        result = await _analyze_domain_chunks(tables, system_type)
    else:
        # Massive schema — catalog first, then chunks
        result = await _analyze_massive_schema(tables, system_type)

    processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

    return {
        "analysis_id": analysis_id,
        "timestamp": datetime.utcnow().isoformat(),
        "processing_time_ms": round(processing_time, 1),
        "table_count": table_count,
        "total_fields": parsed_schema.get("total_fields", 0),
        "total_relationships": parsed_schema.get("total_relationships", 0),
        "system_type": system_type,
        "tier_used": "single" if table_count <= 100 else "chunked" if table_count <= 2000 else "massive",
        **result,
        "governance": {
            "correlation_id": "DA-" + uuid.uuid4().hex[:4].upper() + "-MSCH",
            "hash": hashlib.sha256(f"{analysis_id}{table_count}".encode()).hexdigest()[:16],
            "module_id": "data_systems",
            "ai_disclosure": "Schema analysis generated by AI. Human verification required.",
            "model_used": "claude-sonnet-4-20250514",
        },
    }


async def _analyze_single_pass(tables: dict, system_type: str) -> dict:
    """Analyze small schemas in a single AI call."""
    # Build schema summary for AI
    schema_desc = _build_schema_description(tables)

    prompt = f"""You are an enterprise data architect analyzing a database schema from a {system_type} system.

SCHEMA:
{schema_desc[:40000]}

Analyze this schema and return ONLY valid JSON with:
{{
  "summary": "2-3 sentence overview of this database",
  "functional_domains": [
    {{"domain": "domain name", "tables": ["table1", "table2"], "description": "what this domain handles"}}
  ],
  "key_tables": [
    {{"table": "name", "role": "primary|junction|lookup|audit|config", "business_purpose": "description", "estimated_importance": "critical|high|medium|low"}}
  ],
  "relationships": [
    {{"from_table": "t1", "to_table": "t2", "type": "one_to_many|many_to_many|one_to_one", "via_field": "field_name"}}
  ],
  "data_quality_risks": [
    {{"table": "name", "field": "field_name", "risk": "description", "severity": "high|medium|low"}}
  ],
  "migration_recommendations": ["list of recommendations"],
  "pii_candidates": [
    {{"table": "name", "field": "field_name", "pii_type": "ssn|email|phone|name|address|medical|financial", "confidence": 0.0-1.0}}
  ],
  "overall_complexity": "simple|moderate|complex|very_complex",
  "overall_confidence": 0.0-1.0
}}

Return ONLY JSON. No markdown.
"""

    try:
        import anthropic
        from app.core.config import settings

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        return json.loads(text)

    except Exception as e:
        logger.error(f"AI schema analysis failed: {e}")
        return _fallback_analysis(tables)


async def _analyze_domain_chunks(tables: dict, system_type: str) -> dict:
    """Analyze medium schemas by auto-clustering into domains."""
    # Cluster tables by naming convention (prefix grouping)
    domains = _auto_cluster_tables(tables)

    all_domains = []
    all_risks = []
    all_pii = []

    for domain_name, domain_tables in domains.items():
        subset = {t: tables[t] for t in domain_tables if t in tables}
        if subset:
            chunk_result = await _analyze_single_pass(subset, system_type)
            all_domains.extend(chunk_result.get("functional_domains", [{"domain": domain_name, "tables": domain_tables}]))
            all_risks.extend(chunk_result.get("data_quality_risks", []))
            all_pii.extend(chunk_result.get("pii_candidates", []))

    return {
        "functional_domains": all_domains,
        "data_quality_risks": all_risks,
        "pii_candidates": all_pii,
        "overall_complexity": "complex",
        "overall_confidence": 0.75,
        "chunking_strategy": f"{len(domains)} domain chunks",
        "migration_recommendations": [
            f"Schema contains {len(tables)} tables across {len(domains)} functional domains",
            "Recommend migrating by domain to reduce risk",
            "Start with smallest domain to validate approach",
        ],
    }


async def _analyze_massive_schema(tables: dict, system_type: str) -> dict:
    """Analyze massive schemas (10K+ tables) using catalog-first approach."""
    # Tier 1: Catalog-level overview (metadata only)
    catalog = {}
    for tname, tdata in tables.items():
        catalog[tname] = {
            "field_count": len(tdata.get("fields", [])),
            "has_pk": len(tdata.get("primary_keys", [])) > 0,
            "fk_count": len(tdata.get("foreign_keys", [])),
        }

    # Cluster into domains
    domains = _auto_cluster_tables(tables)

    return {
        "summary": f"Massive schema with {len(tables)} tables across {len(domains)} auto-detected domains. Full field-level analysis requires domain-by-domain processing.",
        "functional_domains": [{"domain": d, "tables": t[:20], "table_count": len(t)} for d, t in domains.items()],
        "data_quality_risks": [],
        "pii_candidates": [],
        "overall_complexity": "very_complex",
        "overall_confidence": 0.5,
        "chunking_strategy": f"catalog_first: {len(domains)} domains identified, {len(tables)} tables cataloged",
        "requires_deep_analysis": True,
        "migration_recommendations": [
            f"This {system_type} system has {len(tables)} tables — too large for single-pass analysis",
            f"Auto-detected {len(domains)} functional domains",
            "Process each domain individually using the /api/migration/schemas/analyze-domain endpoint",
            "Start with the smallest domain to calibrate AI accuracy",
        ],
    }


# ═══════════════════════════════════════════════════════
# PII / PHI DETECTION
# ═══════════════════════════════════════════════════════

PII_PATTERNS = {
    "ssn": [r'ssn', r'social.*security', r'tax.*id', r'tin\b', r'ein\b'],
    "email": [r'email', r'e_mail', r'mail.*addr'],
    "phone": [r'phone', r'mobile', r'cell', r'fax', r'tel\b'],
    "name": [r'\bfirst.*name', r'\blast.*name', r'\bfull.*name', r'fname', r'lname', r'person.*name'],
    "address": [r'address', r'street', r'city', r'state', r'zip', r'postal'],
    "medical": [r'mrn\b', r'medical.*record', r'diagnosis', r'patient.*id', r'health.*id'],
    "financial": [r'account.*num', r'routing', r'credit.*card', r'card.*num', r'bank'],
    "dob": [r'birth.*date', r'dob\b', r'date.*of.*birth'],
    "dl": [r'driver.*lic', r'license.*num'],
}


def detect_pii_in_field(table_name: str, field_name: str, data_type: str = "") -> Optional[dict]:
    """Check if a field likely contains PII based on naming patterns."""
    combined = f"{table_name}.{field_name}".lower()

    for pii_type, patterns in PII_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, combined):
                return {
                    "table": table_name,
                    "field": field_name,
                    "pii_type": pii_type,
                    "confidence": 0.85,
                    "detection_method": "pattern_match",
                }
    return None


def scan_schema_for_pii(tables: dict) -> List[dict]:
    """Scan all fields in a schema for PII candidates."""
    results = []
    for table_name, table_data in tables.items():
        for field in table_data.get("fields", []):
            pii = detect_pii_in_field(table_name, field["name"], field.get("data_type", ""))
            if pii:
                results.append(pii)
    return results


# ═══════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════

def _build_schema_description(tables: dict) -> str:
    """Build a text description of the schema for AI processing."""
    lines = []
    for tname, tdata in list(tables.items())[:200]:  # Cap at 200 tables per chunk
        fields = tdata.get("fields", [])
        field_list = ", ".join(f"{f['name']} ({f['data_type']})" for f in fields[:50])
        fks = tdata.get("foreign_keys", [])
        fk_str = ""
        if fks:
            fk_str = " | FKs: " + ", ".join(f"{fk['field']} -> {fk['references_table']}.{fk['references_field']}" for fk in fks)
        lines.append(f"TABLE {tname}: {field_list}{fk_str}")
    return "\n".join(lines)


def _auto_cluster_tables(tables: dict) -> dict:
    """Auto-cluster tables into functional domains by naming prefix."""
    domains = {}
    for tname in tables:
        # Extract prefix (e.g., HR_, FIN_, SO_, etc.)
        parts = tname.split('_')
        if len(parts) >= 2 and len(parts[0]) <= 5:
            prefix = parts[0].upper()
        else:
            prefix = "GENERAL"

        if prefix not in domains:
            domains[prefix] = []
        domains[prefix].append(tname)

    # Merge small domains into GENERAL
    merged = {}
    for domain, tbls in domains.items():
        if len(tbls) < 3 and domain != "GENERAL":
            if "GENERAL" not in merged:
                merged["GENERAL"] = []
            merged["GENERAL"].extend(tbls)
        else:
            merged[domain] = tbls

    return merged


def _fallback_analysis(tables: dict) -> dict:
    """Structural analysis when AI is unavailable."""
    pii = scan_schema_for_pii(tables)
    domains = _auto_cluster_tables(tables)

    return {
        "summary": f"Structural analysis of {len(tables)} tables (AI unavailable).",
        "functional_domains": [{"domain": d, "tables": t} for d, t in domains.items()],
        "data_quality_risks": [],
        "pii_candidates": pii,
        "overall_complexity": "complex" if len(tables) > 50 else "moderate",
        "overall_confidence": 0.3,
        "migration_recommendations": ["Re-run with AI enabled for full analysis"],
    }
