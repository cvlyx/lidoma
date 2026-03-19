from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = ""
    jwt_secret: str = "change-me"
    jwt_minutes: int = 720  # 12 hours

    admin_username: str = "admin"
    admin_password: str = "change-me"

    allowed_origins: str = "http://127.0.0.1:8787,http://localhost:8787"

