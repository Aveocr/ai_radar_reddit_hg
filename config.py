from pydantic_settings import BaseSettings
from functools import lru_cache
from pydantic import field_validator


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_radar"
    debug: bool = True
    admin_static_path: str = "static/admin"
    parser_interval_hours: int = 48

    # ─── Admin Auth ───
    admin_username: str = "admin"
    admin_password: str = "admin"

    # ─── External Services ───
    auth_service_url: str = "http://localhost:8001"

    # ─── LLM Settings ───
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # ─── Embedding Settings ───
    embedding_provider: str = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    gigachat_client_id: str = ""
    gigachat_client_secret: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    gigachat_verify_ssl: bool = False

    # ─── FAISS Settings ───
    faiss_index_path: str = "data/faiss.index"
    faiss_meta_path: str = "data/faiss_meta.json"

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_mode(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "production", "prod"}:
                return False
            if normalized in {"debug", "development", "dev"}:
                return True
        return value

    @field_validator("embedding_provider", mode="before")
    @classmethod
    def validate_embedding_provider(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"local", "gigachat"}:
                return normalized
        return "local"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


SERVICES = {
    "auth": {
        "url": get_settings().auth_service_url
    }
}
