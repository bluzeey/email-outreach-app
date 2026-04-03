"""Core configuration module."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Database
    DATABASE_URL: str = "sqlite:///./outreach.db"
    CHECKPOINT_DATABASE_URL: str = "sqlite:///./checkpoints.db"
    
    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/google/callback"
    GOOGLE_CLIENT_SECRETS_PATH: str = "./client_secrets.json"
    
    # LLM Configuration
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4"
    
    # Fireworks AI Configuration
    FIREWORKS_API_KEY: str = ""
    FIREWORKS_BASE_URL: str = "https://api.fireworks.ai/inference/v1"
    FIREWORKS_MODEL: str = "accounts/fireworks/routers/kimi-k2p5-turbo"
    
    # Provider selection: "fireworks" | "openai"
    LLM_PROVIDER: str = "fireworks"
    
    # Legacy model setting (deprecated, use provider-specific settings above)
    LLM_MODEL: str = "gpt-4"
    
    # Application
    DRY_RUN_DEFAULT: bool = True
    LOG_LEVEL: str = "INFO"
    
    # File Storage
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10MB
    
    # Rate Limiting
    MAX_SEND_RATE_PER_MINUTE: int = 30
    MAX_CONCURRENT_SENDS: int = 3
    
    # Security (auto-generated if not set - for OAuth token encryption)
    ENCRYPTION_KEY: str = ""
    
    @property
    def upload_path(self) -> Path:
        return Path(self.UPLOAD_DIR)


settings = Settings()
