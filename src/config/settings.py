import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, Field


BASE_DIR = Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    # --- Основные настройки ---
    APP_NAME: str = "Predator.FinAgent.v1"
    SANDBOX_MODE: bool = False
    DEV_MODE: bool = True  # <--- Включаем режим разработки

    # --- Tinkoff ---
    TINKOFF_TOKEN: SecretStr = Field(validation_alias="TINKOFF_TOKEN")

    # --- AI (OpenRouter) ---
    OPENROUTER_API_KEY: SecretStr = Field(
        validation_alias="OPENROUTER_API_KEY"
    )
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    AI_MODEL_ANALYST: str = "nex-agi/deepseek-v3.1-nex-n1:free"
    AI_MODEL_PLANNER: str = "openai/gpt-oss-20b:free"
    AI_MODEL_RISK: str = "xiaomi/mimo-v2-flash:free"

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: SecretStr = Field(
        validation_alias="TELEGRAM_BOT_TOKEN"
    )
    TELEGRAM_CHAT_ID: int = Field(validation_alias="TELEGRAM_CHAT_ID")

    # --- EIA (Natural Gas Storage) ---
    EIA_API_KEY: str = Field(validation_alias="EIA_API_KEY")

    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


try:
    settings = Settings()
except Exception as e:
    print(f"❌ Ошибка загрузки конфига: {e}")
    import pydantic

    if isinstance(e, pydantic.ValidationError):
        print(f"🔍 Детали: {e.errors()}")
    raise e
