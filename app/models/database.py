"""
SQLAlchemy models — all database tables
Updated: tenant_id column added for multi-tenant isolation
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, Boolean, ForeignKey, JSON, Index
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(50), default="default", nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), default="")
    company = Column(String(255), default="")
    role = Column(String(20), default="contributor")
    plan = Column(String(20), default="free")  # free, pro, business, enterprise
    is_active = Column(Boolean, default=True)
    last_active_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(50), default="default", nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    file_type = Column(String(10))
    file_size_bytes = Column(Integer)
    status = Column(String(20), default="uploaded")
    language = Column(String(10), default="en")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_doc_tenant_user', 'tenant_id', 'user_id'),
    )


class Output(Base):
    __tablename__ = "outputs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(50), default="default", nullable=False, index=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)
    content = Column(Text)
    model_used = Column(String(50))
    confidence = Column(Float, default=0)
    processing_time_ms = Column(Integer, default=0)
    status = Column(String(20), default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_output_tenant_user', 'tenant_id', 'user_id'),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(50), default="default", nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50))
    resource_id = Column(String(255))
    details = Column(JSON)
    ip_address = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)


class AudioFile(Base):
    __tablename__ = "audio_files"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(50), default="default", nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    file_size_bytes = Column(Integer)
    file_type = Column(String(10))
    duration_seconds = Column(Float)
    status = Column(String(20), default="uploaded")
    language_detected = Column(String(10))
    transcription_cost = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(50), default="default", nullable=False, index=True)
    audio_file_id = Column(UUID(as_uuid=True), ForeignKey("audio_files.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    full_text = Column(Text, nullable=False)
    word_count = Column(Integer)
    language = Column(String(10))
    confidence = Column(Float)
    segments = Column(JSON)
    model_used = Column(String(50), default="whisper-1")
    processing_time_ms = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
