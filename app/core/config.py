"""Application settings — reads from environment variables or .env file"""
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://docuaction:simple123@localhost:5432/docuaction"
    SECRET_KEY: str = "change-me-in-production"
    AI_PROVIDER: str = "anthropic"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_SONNET_MODEL: str = "claude-sonnet-4-20250514"
    OPENAI_API_KEY: str = ""
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173,https://app.docuaction.io"
    STORAGE_PROVIDER: str = "local"
    UPLOAD_DIR: str = "./uploads"
    WHISPER_MODEL: str = "whisper-1"
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT_ID: str = "common"

    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()
