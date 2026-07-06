"""Application configuration loaded from environment / .env file."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- MongoDB ---
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "RS_2"
    users_collection: str = "users"
    operators_collection: str = "operators"
    audit_collection: str = "operator_logs"
    support_messages_collection: str = "support_messages"

    # --- Быстрые ответы (общая с ботом коллекция) ---
    # По умолчанию — та же база; если бот держит их в другом месте, задайте свои.
    # Имя коллекции подбирается умно: если точного имени нет, берётся вариант,
    # отличающийся только пробелами (в боте она исторически с хвостовым пробелом).
    quick_replies_mongo_url: str = ""      # пусто = MONGO_URL
    quick_replies_db: str = ""             # пусто = MONGO_DB
    quick_replies_collection: str = "support_quick_replies"

    # --- Telegram (бот техподдержки — для ответов в тикеты с сайта) ---
    tg_bot_token: str = ""      # токен САППОРТ-бота (тот же, что в боте поддержки)
    support_chat_id: int = 0    # id чата поддержки с топиками (-100...)

    # --- Auth ---
    jwt_secret: str  # REQUIRED — no default on purpose, service refuses to start without it
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 720  # 12h shift
    login_max_attempts: int = 10          # per login+IP within window
    login_attempt_window_sec: int = 900

    # --- ИИ-помощник (черновики ответов в тикетах) ---
    anthropic_api_key: str = ""            # ключ из console.anthropic.com; пусто = функция выключена
    ai_model: str = "claude-opus-4-8"
    ai_effort: str = "low"                 # low|medium|high — скорость/качество черновика
    ai_max_tokens: int = 1024
    ai_history_messages: int = 30          # сколько последних сообщений тикета отдавать модели

    # --- Remnawave ---
    remnawave_base_url: str = ""          # e.g. https://panel.example.com
    remnawave_token: str = ""             # API bearer token
    remnawave_timeout_sec: float = 15.0

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8100
    # Comma-separated origins for CORS; empty = same-origin only (frontend served by this app)
    cors_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
