"""Pydantic schemas for API request/response validation"""
from pydantic import BaseModel
from typing import Optional, List, Any
from uuid import UUID
from datetime import datetime


# ═══ AUTH ═══

class SignupRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = ""
    company: Optional[str] = ""

class LoginRequest(BaseModel):
    email: str
    password: str

class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    company: str
    role: str
    created_at: datetime
    class Config:
        from_attributes = True

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ═══ PROCESS (AI Engine) ═══

class ProcessRequest(BaseModel):
    """Input for /api/process — send text for AI analysis"""
    text: str
    action_type: str = "summary"  # summary | actions | insights | email | brief
    output_language: Optional[str] = None

class TaskItem(BaseModel):
    task: str
    owner: str = "TBD"
    deadline: str = "TBD"
    priority: str = "medium"
    source_reference: Optional[str] = None

class DecisionItem(BaseModel):
    decision: str
    decided_by: str = "Unknown"
    context: Optional[str] = None
    impact: Optional[str] = None

class FollowUpItem(BaseModel):
    item: str
    owner: str = "TBD"
    due: str = "TBD"

class ProcessResponse(BaseModel):
    """Structured JSON output from AI engine"""
    summary: Optional[str] = None
    tasks: Optional[List[TaskItem]] = []
    decisions: Optional[List[DecisionItem]] = []
    follow_ups: Optional[List[FollowUpItem]] = []
    insights: Optional[List[Any]] = []
    key_metrics: Optional[List[Any]] = []
    recommendations: Optional[List[str]] = []
    confidence: float = 0
    _meta: Optional[dict] = None


# ═══ DOCUMENTS ═══

class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    file_type: Optional[str]
    file_size_bytes: Optional[int]
    status: str
    created_at: datetime
    class Config:
        from_attributes = True

class OutputResponse(BaseModel):
    id: UUID
    document_id: UUID
    action_type: str
    content: Optional[str]
    model_used: Optional[str]
    confidence: Optional[float]
    processing_time_ms: Optional[int]
    status: str
    created_at: datetime
    class Config:
        from_attributes = True


# ═══ AUDIO ═══

class TranscribeResponse(BaseModel):
    """Output from /api/transcribe"""
    transcript: str
    word_count: int
    language: str
    duration_seconds: float
    confidence: float
    cost_usd: float
    model: str
    segments: Optional[List[Any]] = None
