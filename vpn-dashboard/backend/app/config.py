"""Application settings. Everything secret comes from the environment (.env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Mongo ---
    mongo_uri: str = Field(default="mongodb://localhost:27017", alias="MONGO_URI")
    mongo_db: str = Field(default="vpn_bot", alias="MONGO_DB")
    users_collection: str = Field(default="users", alias="USERS_COLLECTION")
    # Provider webhook log; swept into payments_flat when the collection exists
    payments_collection: str = Field(default="payments_webhook",
                                     alias="PAYMENTS_COLLECTION")
    # Database holding payments_collection; empty = same as MONGO_DB
    payments_db: str = Field(default="", alias="PAYMENTS_DB")
    # Minimum top-up amount enforced by the bot (₽)
    min_topup_rub: float = Field(default=75, alias="MIN_TOPUP_RUB")

    # --- Redis cache (falls back to in-memory when unreachable) ---
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # --- Remnawave panel ---
    remnawave_api_url: str = Field(default="", alias="REMNAWAVE_API_URL")
    remnawave_token: str = Field(default="", alias="REMNAWAVE_TOKEN")

    # --- Auth ---
    jwt_secret: str = Field(default="", alias="JWT_SECRET")
    jwt_ttl_hours: int = Field(default=72, alias="JWT_TTL_HOURS")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    # bcrypt hash preferred; ADMIN_PASSWORD is a plain-text fallback for dev
    admin_password_hash: str = Field(default="", alias="ADMIN_PASSWORD_HASH")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")
    # optional second admin
    admin2_username: str = Field(default="", alias="ADMIN2_USERNAME")
    admin2_password_hash: str = Field(default="", alias="ADMIN2_PASSWORD_HASH")
    cookie_secure: bool = Field(default=True, alias="COOKIE_SECURE")
    cookie_name: str = Field(default="vpn_dash_token", alias="COOKIE_NAME")

    # --- Telegram alerts ---
    tg_bot_token: str = Field(default="", alias="TG_BOT_TOKEN")
    tg_admin_chat_id: str = Field(default="", alias="TG_ADMIN_CHAT_ID")

    # --- Jobs ---
    etl_interval_minutes: int = Field(default=5, alias="ETL_INTERVAL_MINUTES")
    alerts_interval_minutes: int = Field(default=5, alias="ALERTS_INTERVAL_MINUTES")
    provider_silence_hours: float = Field(default=6, alias="PROVIDER_SILENCE_HOURS")
    alert_cooldown_hours: float = Field(default=6, alias="ALERT_COOLDOWN_HOURS")

    # --- Misc ---
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: str = Field(default="production", alias="ENVIRONMENT")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
