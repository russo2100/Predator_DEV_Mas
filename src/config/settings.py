from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Required for trading/news
    TINKOFF_TOKEN: SecretStr

    # Required for LLM calls (analyst/planner/risk)
    OPENROUTER_API_KEY: SecretStr
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    AI_MODEL_ANALYST: str = "qwen/qwen-2.5-72b-instruct"
    AI_MODEL_RISK: str = "meta-llama/llama-2-70b-chat-hf"
    AI_MODEL_PLANNER: str = "anthropic/claude-3.5-sonnet"

    # Optional integrations
    TELEGRAM_BOT_TOKEN: SecretStr | None = None
    TELEGRAM_CHAT_ID: str | None = None
    EIA_API_KEY: str | None = None

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

settings = Settings()
