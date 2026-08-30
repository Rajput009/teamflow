from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration.

    Values are read from environment variables (or a local .env file).
    A required value that is missing raises a ValidationError AT STARTUP —
    we fail fast instead of discovering broken config on request #47.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["local", "staging", "production"] = "local"

    jwt_secret_key: SecretStr
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14

    database_url: PostgresDsn

    # Background jobs. task_always_eager=True runs Celery tasks inline in the
    # API process (no broker needed — our current local setup). When Docker
    # arrives this flips to False and the same tasks run on real workers.
    redis_url: str = "redis://localhost:6379/0"
    task_always_eager: bool = True

    # --- AI layer (docs/features/11-ai-task-generator.md) ---
    # Provider-agnostic by design: any OpenAI-compatible endpoint works
    # (OpenRouter, OpenAI, Groq, local Ollama). No key => AI endpoints return
    # 503 AI_NOT_CONFIGURED; everything else keeps working.
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    ai_max_generated_tasks: int = 30

    # --- Cross-cutting (docs/wayfinder/DECISIONS.md, WF-6) ---
    # Comma-separated allowlist of browser origins permitted to call the API.
    # Defaults to "" (no cross-origin access) — secure by default. Use "*"
    # only for throwaway local demos; never in production.
    cors_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the singleton Settings instance.

    lru_cache means the .env file is parsed once per process, not per request.
    It is a function (not a bare global) so tests can override it cleanly.
    """
    return Settings()
