from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    cors_origins: list[str] = ["http://localhost:5173"]
    secret_key: str
    jwt_algorithm: str = "HS256"
    salted_hash: str = "73b0bec586bff32a0e635095b2fe9ebe7f96733a747922662f49f692d638b05c"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14
    cookie_secure: bool = False  # set True in production (HTTPS)
    openai_api_key: str = ""

settings = Settings()