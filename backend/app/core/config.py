from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Environment: "development" or "production"
    ENVIRONMENT: str = "development"
    DEV_AUTH_BYPASS: bool = False
    
    # CORS: Comma-separated list of allowed origins
    ALLOWED_ORIGINS: str = "http://localhost:8001,http://localhost:8000"
    
    CHROMA_DB_PATH: str = "./chroma_db"
    COLLECTION_NAME: str = "antigravity_docs"
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    ANTHROPIC_API_KEY: str = ""
    LLM_DEFAULT_MODEL: str = "claude-3-5-sonnet-20241022"
    LLM_FALLBACK_MODEL: str = "claude-3-haiku-20240307"
    LLM_ALLOWED_MODELS: str = "claude-3-5-sonnet-20241022,claude-3-5-haiku-20241022,claude-3-haiku-20240307"
    CHAT_RATE_LIMIT_ENABLED: bool = True
    CHAT_RATE_LIMIT_USER_PER_MINUTE: int = 20
    CHAT_RATE_LIMIT_PROJECT_PER_MINUTE: int = 80
    CHAT_DAILY_USER_QUOTA: int = 300
    CHAT_DAILY_PROJECT_QUOTA: int = 1500
    MISTRAL_API_KEY: str = ""
    GDPR_CARD_PREFIX: str = "DOKUMENTKORT"
    GDPR_NAME_SCRUB_MODEL: str = "mistral-small-latest"
    GDPR_SCRUB_PROVIDER: str = "MISTRAL_EU"
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_STORAGE_BUCKET: str = ""
    ALLOW_LOCAL_FALLBACK: bool = False
    HEALTH_CHECK_EMBEDDINGS: bool = False
    DIRECT_ATTACHMENT_MAX_CHARS: int = 8000
    DIRECT_ATTACHMENT_MAX_PAGES: int = 20
    DEFAULT_PERSONA_PROMPT: str = """Du är en intelligent dokumentassistent som heter Antigravity. 
Ditt uppdrag är att hjälpa handläggare genom att svara på frågor baserat på de tillhandahållna styrdokumenten.

INSTRUKTIONER:
1. Använd ENDAST den tillhandahållna kontexten för att svara på frågan.
2. Om svaret inte finns i kontexten, säg vänligt att du inte kan hitta informationen i styrdokumenten.
3. Skriv formellt, sakligt och koncist på svenska. Undvik utfyllnadsord.
4. Citera alltid källfilen (filename) för dina påståenden."""


    class Config:
        import os
        # Look for .env in the current directory, or in the backend directory relative to this file
        _env_path = ".env"
        if not os.path.exists(_env_path):
             _base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
             _env_path = os.path.join(_base_dir, ".env")
        
        env_file = _env_path

settings = Settings()

# Default: allow local fallback only in development if not explicitly set
import os as _os
if "ALLOW_LOCAL_FALLBACK" not in _os.environ and settings.ENVIRONMENT == "development":
    settings.ALLOW_LOCAL_FALLBACK = True
