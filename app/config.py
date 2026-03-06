from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://openclaw:openclaw@localhost:5432/openclaw"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "change-me-in-production"
    admin_token: str = "change-me-admin-token"
    anthropic_api_key: str = ""
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    initial_claw_human: int = 500
    initial_claw_agent: int = 200
    agent_registration_fee: int = 100


settings = Settings()
