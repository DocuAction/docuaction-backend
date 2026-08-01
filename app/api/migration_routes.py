"""
DocuAction — Migration Intelligence API Routes
Namespace: /api/migration/*

Security:
  - Every endpoint checks module_data_systems flag via require_module/require_permission
  - Returns 403 (not 404) when module disabled
  - Zero metadata leakage when disabled
  - RBAC enforced per-endpoint

Dependencies (ALLOWED):
  - app.middleware.module_gate (shared)
  - app.services.migration.* (module-specific)
  - app.models.migration_models (module-specific)
  - app.core.security (shared)
  - app.core.database (shared)

Dependencies (FORBIDDEN):
  - app.api.routes (document routes)
  - app.services.ai_engine (document AI)
  - app.services.audio_service
  - app.services.healthcare_claims_engine
"""
import json
import uuid
import hashlib
import logging
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import require_role
from app.middleware.module_gate import require_module, require_permission

logger = logging.getLogger("docuaction.migration.api")
router = APIRouter(prefix="/api/migration", tags=["Migration Intelligence"])


# ═══════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════

class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    description: str = Field("", max_length=2000)
    source_system: str = Field("", max_length=200)
    target_system: str = Field("", max_length=200)

class GenerateMappingsRequest(BaseModel):
    source_schema_id: str = Field(...)
    target_schema_id: str = Field(...)
    focus_tables: Optional[List[str]] = Field(None)

class ApproveMappingRequest(BaseModel):
    justification: str = Field(..., min_length=10, max_length=2000)

class ConflictPositionRequest(BaseModel):
    position: str = Field(..., min_length=50, max_length=5000)
    evidence: Optional[str] = Field(None, max_length=5000)


# ═══════════════════════════════════════════════════════
# PROJECTS
# ═══════════════════════════════════════════════════════

@router.post("/projects", status_code=201)
async def create_project(
    request: CreateProjectRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.schema.upload")),
):
    """Create a new migration project."""
    from app.models.migration_models import MigrationProject

    project = MigrationProject(
        project_id="MPRJ-" + uuid.uuid4().hex[:8].upper(),
        tenant_id=str(getattr(user, "tenant_id", "default")),
        user_id=user.id,
        name=request.name,
        description=request.description,
        source_system=request.source_system,
        target_system=request.target_system,
        correlation_id="DA-" + uuid.uuid4().hex[:4].upper() + "-MPRJ",
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    logger.info(f"Migration project created: {project.project_id} by {user.email}")
    return {
        "project_id": project.project_id,
        "name": project.name,
        "source_system": project.source_system,
        "target_system": project.target_system,
        "status": project.status,
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }


@router.get("/projects", dependencies=[Depends(require_role("viewer"))])
async def list_projects(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_module("data_systems")),
):
    """List all migration projects for the current tenant."""
    from app.models.migration_models import MigrationProject

    result = await db.execute(
        select(MigrationProject)
        .where(MigrationProject.user_id == user.id)
        .order_by(MigrationProject.created_at.desc())
        .limit(limit)
    )
    projects = result.scalars().all()
    return {
        "projects": [
            {
                "project_id": p.project_id,
                "name": p.name,
                "source_system": p.source_system,
                "target_system": p.target_system,
                "status": p.status,
                "total_schemas": p.total_schemas,
                "total_mappings": p.total_mappings,
                "approved_mappings": p.approved_mappings,
                "overall_risk_score": p.overall_risk_score,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in projects
        ],
        "total": len(projects),
    }


@router.get("/projects/{project_id}", dependencies=[Depends(require_role("viewer"))])
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_module("data_systems")),
):
    """Get a specific migration project."""
    from app.models.migration_models import MigrationProject

    result = await db.execute(
        select(MigrationProject).where(
            MigrationProject.project_id == project_id,
            MigrationProject.user_id == user.id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")

    return {
        "project_id": project.project_id,
        "name": project.name,
        "description": project.description,
        "source_system": project.source_system,
        "target_system": project.target_system,
        "status": project.status,
        "total_schemas": project.total_schemas,
        "total_fields": project.total_fields,
        "total_mappings": project.total_mappings,
        "approved_mappings": project.approved_mappings,
        "overall_risk_score": project.overall_risk_score,
        "foia_readiness_score": project.foia_readiness_score,
        "correlation_id": project.correlation_id,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


# ═══════════════════════════════════════════════════════
# SCHEMA INGESTION & ANALYSIS
# ═══════════════════════════════════════════════════════

@router.post("/schemas/upload", status_code=201)
async def upload_schema(
    file: UploadFile = File(...),
    project_id: str = Query(...),
    schema_type: str = Query("source", description="source or target"),
    system_type: str = Query("unknown", description="oracle, salesforce, sap, postgresql, cobol, etc."),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.schema.upload")),
):
    """
    Upload a schema file (DDL, CSV, SQL) for AI analysis.
    The system parses the schema and runs AI-powered analysis.
    """
    from app.models.migration_models import MigrationSchema, MigrationProject, MigrationField
    from app.services.migration.migration_schema_engine import parse_ddl, parse_csv_schema, analyze_schema, scan_schema_for_pii

    # Verify project exists
    proj_result = await db.execute(
        select(MigrationProject).where(
            MigrationProject.project_id == project_id,
            MigrationProject.user_id == user.id,
        )
    )
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")

    # Read file
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Schema file too large (max 10MB)")

    text = content.decode("utf-8", errors="ignore")
    file_hash = hashlib.sha256(content).hexdigest()

    # Determine input type and parse
    filename_lower = file.filename.lower() if file.filename else ""
    if filename_lower.endswith(".csv"):
        parsed = parse_csv_schema(text)
        input_type = "csv"
    else:
        parsed = parse_ddl(text)
        input_type = "ddl"

    # Save schema record
    schema = MigrationSchema(
        schema_id="MSCH-" + uuid.uuid4().hex[:8].upper(),
        project_id=project.id,
        user_id=user.id,
        name=file.filename or "unnamed",
        schema_type=schema_type,
        system_type=system_type,
        input_type=input_type,
        file_hash=file_hash,
        raw_content_length=len(content),
        table_count=parsed.get("table_count", 0),
        field_count=parsed.get("total_fields", 0),
        relationship_count=parsed.get("total_relationships", 0),
        status="analyzing",
    )
    db.add(schema)
    await db.commit()
    await db.refresh(schema)

    # Save individual fields
    pii_count = 0
    for table_name, table_data in parsed.get("tables", {}).items():
        for field in table_data.get("fields", []):
            pii = None
            from app.services.migration.migration_schema_engine import detect_pii_in_field
            pii = detect_pii_in_field(table_name, field["name"], field.get("data_type", ""))

            mf = MigrationField(
                field_id="MFLD-" + uuid.uuid4().hex[:8].upper(),
                schema_id=schema.id,
                table_name=table_name,
                field_name=field["name"],
                data_type=field.get("data_type", ""),
                is_nullable=field.get("nullable", True),
                is_primary_key=field.get("is_pk", False),
                is_pii=pii is not None,
                pii_type=pii["pii_type"] if pii else "",
                confidence=pii["confidence"] if pii else 0,
            )
            db.add(mf)
            if pii:
                pii_count += 1

    # Run AI analysis
    try:
        analysis = await analyze_schema(parsed, system_type)
        schema.analysis_result = analysis
        schema.confidence = analysis.get("overall_confidence", 0)
        schema.model_used = analysis.get("governance", {}).get("model_used", "")
        schema.processing_time_ms = analysis.get("processing_time_ms", 0)
        schema.pii_field_count = pii_count
        schema.status = "analyzed"
    except Exception as e:
        logger.error(f"Schema analysis failed: {e}")
        schema.status = "failed"

    # Update project metrics
    project.total_schemas += 1
    project.total_fields += parsed.get("total_fields", 0)

    await db.commit()
    await db.refresh(schema)

    logger.info(f"Schema uploaded: {schema.schema_id} tables={schema.table_count} fields={schema.field_count} pii={pii_count}")

    return {
        "schema_id": schema.schema_id,
        "name": schema.name,
        "schema_type": schema.schema_type,
        "system_type": schema.system_type,
        "table_count": schema.table_count,
        "field_count": schema.field_count,
        "relationship_count": schema.relationship_count,
        "pii_field_count": pii_count,
        "status": schema.status,
        "analysis": schema.analysis_result,
        "confidence": schema.confidence,
        "processing_time_ms": schema.processing_time_ms,
    }


@router.get("/schemas/{schema_id}")
async def get_schema(
    schema_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.schema.view")),
):
    """Get schema details with analysis results."""
    from app.models.migration_models import MigrationSchema

    result = await db.execute(
        select(MigrationSchema).where(
            MigrationSchema.schema_id == schema_id,
            MigrationSchema.user_id == user.id,
        )
    )
    schema = result.scalar_one_or_none()
    if not schema:
        raise HTTPException(404, "Schema not found")

    return {
        "schema_id": schema.schema_id,
        "name": schema.name,
        "schema_type": schema.schema_type,
        "system_type": schema.system_type,
        "table_count": schema.table_count,
        "field_count": schema.field_count,
        "pii_field_count": schema.pii_field_count,
        "status": schema.status,
        "analysis": schema.analysis_result,
        "confidence": schema.confidence,
        "processing_time_ms": schema.processing_time_ms,
        "created_at": schema.created_at.isoformat() if schema.created_at else None,
    }


@router.get("/schemas/{schema_id}/fields")
async def get_schema_fields(
    schema_id: str,
    table_name: Optional[str] = Query(None),
    pii_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.schema.view")),
):
    """List fields for a schema, optionally filtered by table or PII status."""
    from app.models.migration_models import MigrationSchema, MigrationField

    # Verify schema ownership
    schema_result = await db.execute(
        select(MigrationSchema).where(
            MigrationSchema.schema_id == schema_id,
            MigrationSchema.user_id == user.id,
        )
    )
    schema = schema_result.scalar_one_or_none()
    if not schema:
        raise HTTPException(404, "Schema not found")

    query = select(MigrationField).where(MigrationField.schema_id == schema.id)
    if table_name:
        query = query.where(MigrationField.table_name == table_name)
    if pii_only:
        query = query.where(MigrationField.is_pii == True)

    result = await db.execute(query.order_by(MigrationField.table_name, MigrationField.field_name))
    fields = result.scalars().all()

    return {
        "schema_id": schema_id,
        "total_fields": len(fields),
        "fields": [
            {
                "field_id": f.field_id,
                "table_name": f.table_name,
                "field_name": f.field_name,
                "data_type": f.data_type,
                "nullable": f.is_nullable,
                "is_pk": f.is_primary_key,
                "is_fk": f.is_foreign_key,
                "is_pii": f.is_pii,
                "pii_type": f.pii_type,
                "confidence": f.confidence,
                "business_description": f.business_description,
            }
            for f in fields
        ],
    }


# ═══════════════════════════════════════════════════════
# MAPPING GENERATION
# ═══════════════════════════════════════════════════════

@router.post("/mappings/generate")
async def generate_mappings(
    request: GenerateMappingsRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.mapping.approve")),
):
    """
    Generate AI mapping suggestions between source and target schemas.
    Each mapping becomes a decision in the Decision Bank.
    """
    from app.models.migration_models import MigrationSchema, MigrationField, MigrationMapping

    # Load source schema fields
    src_result = await db.execute(
        select(MigrationSchema).where(MigrationSchema.schema_id == request.source_schema_id, MigrationSchema.user_id == user.id)
    )
    source_schema = src_result.scalar_one_or_none()
    if not source_schema:
        raise HTTPException(404, "Source schema not found")

    # Load target schema fields
    tgt_result = await db.execute(
        select(MigrationSchema).where(MigrationSchema.schema_id == request.target_schema_id, MigrationSchema.user_id == user.id)
    )
    target_schema = tgt_result.scalar_one_or_none()
    if not target_schema:
        raise HTTPException(404, "Target schema not found")

    # Load fields
    src_fields_result = await db.execute(
        select(MigrationField).where(MigrationField.schema_id == source_schema.id)
    )
    src_fields = src_fields_result.scalars().all()

    tgt_fields_result = await db.execute(
        select(MigrationField).where(MigrationField.schema_id == target_schema.id)
    )
    tgt_fields = tgt_fields_result.scalars().all()

    # Build mapping suggestions using AI
    start_time = datetime.utcnow()
    mappings_created = []

    # Prepare field descriptions for AI
    src_desc = "\n".join(f"SOURCE: {f.table_name}.{f.field_name} ({f.data_type})" for f in src_fields[:500])
    tgt_desc = "\n".join(f"TARGET: {f.table_name}.{f.field_name} ({f.data_type})" for f in tgt_fields[:500])

    prompt = f"""You are a data migration architect. Map source fields to target fields.

SOURCE SCHEMA ({source_schema.system_type}):
{src_desc}

TARGET SCHEMA ({target_schema.system_type}):
{tgt_desc}

Return ONLY valid JSON array of mappings:
[
  {{
    "source_table": "table_name",
    "source_field": "field_name",
    "target_table": "table_name",
    "target_field": "field_name",
    "confidence": 0.0-1.0,
    "transformation": "direct|convert|calculate|custom",
    "transformation_rule": "SQL expression or empty",
    "rationale": "Why this mapping",
    "alternatives": [
      {{"target_table": "t", "target_field": "f", "confidence": 0.0-1.0, "reason": "why"}}
    ],
    "risk_factors": ["list of risks"]
  }}
]

Map EVERY source field. If no good target match, set target fields to empty strings with confidence 0.
Return ONLY JSON array."""

    try:
        import anthropic
        from app.core.config import settings

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        ai_mappings = json.loads(text)

    except Exception as e:
        logger.error(f"AI mapping generation failed: {e}")
        ai_mappings = []

    # Save mappings to database
    for m in ai_mappings:
        mapping = MigrationMapping(
            mapping_id="MMAP-" + uuid.uuid4().hex[:8].upper(),
            project_id=source_schema.project_id,
            source_schema_id=source_schema.id,
            source_table=m.get("source_table", ""),
            source_field=m.get("source_field", ""),
            source_type="",
            target_schema_id=target_schema.id,
            target_table=m.get("target_table", ""),
            target_field=m.get("target_field", ""),
            target_type="",
            transformation_type=m.get("transformation", "direct"),
            transformation_rule=m.get("transformation_rule", ""),
            confidence=m.get("confidence", 0),
            rationale=m.get("rationale", ""),
            alternatives=m.get("alternatives", []),
            risk_factors=m.get("risk_factors", []),
            status="proposed",
            assigned_to=user.id,
        )
        db.add(mapping)
        mappings_created.append({
            "mapping_id": mapping.mapping_id,
            "source": f"{mapping.source_table}.{mapping.source_field}",
            "target": f"{mapping.target_table}.{mapping.target_field}" if mapping.target_field else "UNMAPPED",
            "confidence": mapping.confidence,
            "transformation": mapping.transformation_type,
            "status": mapping.status,
        })

    await db.commit()

    processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000

    logger.info(f"Generated {len(mappings_created)} mappings for project {source_schema.project_id}")

    return {
        "total_mappings": len(mappings_created),
        "processing_time_ms": round(processing_time, 1),
        "mappings": mappings_created,
        "governance": {
            "correlation_id": "DA-" + uuid.uuid4().hex[:4].upper() + "-MMAP",
            "module_id": "data_systems",
            "ai_disclosure": "Mappings generated by AI. Human review required before approval.",
            "model_used": "claude-sonnet-4-20250514",
        },
    }


# ═══════════════════════════════════════════════════════
# MAPPING DECISIONS (approve/reject/conflict)
# ═══════════════════════════════════════════════════════

@router.post("/mappings/{mapping_id}/approve")
async def approve_mapping(
    mapping_id: str,
    request: ApproveMappingRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.mapping.approve")),
):
    """Approve a mapping with required justification."""
    from app.models.migration_models import MigrationMapping, MigrationMappingVersion

    result = await db.execute(
        select(MigrationMapping).where(MigrationMapping.mapping_id == mapping_id)
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(404, "Mapping not found")

    # Save version history
    version = MigrationMappingVersion(
        mapping_id=mapping.id,
        version=mapping.version + 1,
        change_type="approved",
        changed_by=user.id,
        change_reason=request.justification,
        snapshot={
            "source": f"{mapping.source_table}.{mapping.source_field}",
            "target": f"{mapping.target_table}.{mapping.target_field}",
            "confidence": mapping.confidence,
            "transformation": mapping.transformation_rule,
        },
    )
    db.add(version)

    # Update mapping
    mapping.status = "approved"
    mapping.approved_by = user.id
    mapping.approval_justification = request.justification
    mapping.approved_at = datetime.utcnow()
    mapping.version += 1

    await db.commit()

    logger.info(f"Mapping approved: {mapping_id} by {user.email}")
    return {"mapping_id": mapping_id, "status": "approved", "approved_by": user.email}


@router.post("/mappings/{mapping_id}/reject")
async def reject_mapping(
    mapping_id: str,
    justification: str = Query(..., min_length=10),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.mapping.approve")),
):
    """Reject a mapping with required justification."""
    from app.models.migration_models import MigrationMapping

    result = await db.execute(
        select(MigrationMapping).where(MigrationMapping.mapping_id == mapping_id)
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(404, "Mapping not found")

    mapping.status = "failed"
    mapping.approval_justification = f"REJECTED: {justification}"
    await db.commit()

    return {"mapping_id": mapping_id, "status": "rejected"}


@router.post("/mappings/{mapping_id}/conflict")
async def flag_conflict(
    mapping_id: str,
    request: ConflictPositionRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.decision.escalate")),
):
    """Flag a mapping as conflicted and submit a position."""
    from app.models.migration_models import MigrationMapping, MigrationMappingVersion

    result = await db.execute(
        select(MigrationMapping).where(MigrationMapping.mapping_id == mapping_id)
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(404, "Mapping not found")

    # Record conflict position as a version
    version = MigrationMappingVersion(
        mapping_id=mapping.id,
        version=mapping.version + 1,
        change_type="conflict_position",
        changed_by=user.id,
        change_reason=request.position,
        snapshot={"evidence": request.evidence},
    )
    db.add(version)

    mapping.status = "conflicted"
    mapping.version += 1
    await db.commit()

    logger.info(f"Mapping conflict flagged: {mapping_id} by {user.email}")
    return {"mapping_id": mapping_id, "status": "conflicted", "position_recorded": True}


# ═══════════════════════════════════════════════════════
# MANIFEST API (ETL Integration)
# ═══════════════════════════════════════════════════════

@router.get("/manifests/{project_id}")
async def get_manifest(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("data_systems", "migration.manifest.api")),
):
    """
    Get the current mapping manifest for ETL tool consumption.
    Returns all approved mappings as machine-readable JSON.
    """
    from app.models.migration_models import MigrationProject, MigrationMapping

    proj_result = await db.execute(
        select(MigrationProject).where(
            MigrationProject.project_id == project_id,
            MigrationProject.user_id == user.id,
        )
    )
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")

    mapping_result = await db.execute(
        select(MigrationMapping).where(
            MigrationMapping.project_id == project.id,
            MigrationMapping.status == "approved",
        )
    )
    mappings = mapping_result.scalars().all()

    manifest_content = [
        {
            "mapping_id": m.mapping_id,
            "source_table": m.source_table,
            "source_field": m.source_field,
            "target_table": m.target_table,
            "target_field": m.target_field,
            "transformation_type": m.transformation_type,
            "transformation_rule": m.transformation_rule,
            "confidence": m.confidence,
            "approved_at": m.approved_at.isoformat() if m.approved_at else None,
        }
        for m in mappings
    ]

    version_hash = hashlib.sha256(json.dumps(manifest_content, sort_keys=True).encode()).hexdigest()

    return {
        "project_id": project_id,
        "manifest_version_hash": version_hash,
        "total_mappings": len(manifest_content),
        "generated_at": datetime.utcnow().isoformat(),
        "mappings": manifest_content,
        "governance": {
            "module_id": "data_systems",
            "ai_disclosure": "Mappings generated by AI and approved by human reviewers.",
        },
    }


# ═══════════════════════════════════════════════════════
# MODULE STATUS (for UI feature flag check)
# ═══════════════════════════════════════════════════════

@router.get("/status")
async def module_status(user=Depends(require_module("data_systems"))):
    """Check if Migration Intelligence is enabled. Returns module status."""
    return {
        "module": "data_systems",
        "enabled": True,
        "version": "1.0.0",
        "capabilities": [
            "schema_ingestion", "ai_analysis", "mapping_generation",
            "decision_governance", "pii_detection", "etl_manifest",
        ],
    }
