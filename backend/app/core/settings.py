"""Application settings and configuration."""
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """App configuration from environment variables."""

    # App
    APP_NAME: str = "amzur-ai-chat"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://user:password@localhost:5432/amzur_chat"
    )
    DATABASE_URL_SYNC: str = DATABASE_URL.replace("asyncpg", "psycopg")

    # CORS
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(
        ","
    )

    # LiteLLM
    LITELLM_API_KEY: str = os.getenv("LITELLM_API_KEY", "")
    LITELLM_PROXY_URL: str = os.getenv("LITELLM_PROXY_URL", "http://localhost:4000")
    LITELLM_CHAT_MODEL: str = os.getenv("LITELLM_CHAT_MODEL", "gemini/gemini-2.5-flash")
    LITELLM_EMBEDDING_MODEL: str = os.getenv(
        "LITELLM_EMBEDDING_MODEL", "text-embedding-3-large"
    )
    IMAGE_GEN_MODEL: str = os.getenv(
        "IMAGE_GEN_MODEL", "gemini/imagen-4.0-fast-generate-001"
    )
    LITELLM_TIMEOUT_SECONDS: float = float(os.getenv("LITELLM_TIMEOUT_SECONDS", "20"))
    LITELLM_HARD_TIMEOUT_SECONDS: float = float(
        os.getenv("LITELLM_HARD_TIMEOUT_SECONDS", "25")
    )
    LITELLM_MAX_RETRIES: int = int(os.getenv("LITELLM_MAX_RETRIES", "1"))

    # LLM
    SYSTEM_PROMPT: str = os.getenv(
        "SYSTEM_PROMPT", "You are a concise, helpful assistant. Provide clear and actionable answers."
    )
    CONVERSATION_MEMORY_WINDOW: int = int(os.getenv("CONVERSATION_MEMORY_WINDOW", "5"))

    # RAG / Chroma
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))

    # Google OAuth (optional)
    GOOGLE_CLIENT_ID: Optional[str] = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: Optional[str] = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI: Optional[str] = os.getenv("GOOGLE_REDIRECT_URI")
    GOOGLE_SERVICE_ACCOUNT_JSON: Optional[str] = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    GOOGLE_SHEET_ID: Optional[str] = os.getenv("GOOGLE_SHEET_ID")

    # File uploads
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./uploads")
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "20"))

    # Accepted MIME types for local-file dataframe tool
    ALLOWED_DATAFRAME_MIME_TYPES: tuple[str, ...] = (
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


settings = Settings()
