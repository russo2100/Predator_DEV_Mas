from typing import Optional, Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    # API Keys & URLs
    TINKOFF_TOKEN: SecretStr
    OPENROUTER_API_KEY: SecretStr
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    TELEGRAM_BOT_TOKEN: SecretStr
    TELEGRAM_CHAT_ID: str
    EIA_API_KEY: Optional[SecretStr] = None

    # AI Model Configuration (Три модели с указанным nex-agi)
    AI_MODEL_ANALYST: str = "google/gemini-3-flash-preview"
    AI_MODEL_PLANNER: str = "google/gemini-3-flash-preview"
    AI_MODEL_RISK: str = "google/gemini-3-flash-preview"

    # Trading Parameters (v2.0 Hybrid Architecture)
    MAX_LOTS: int = 8
    
    # Bayesian Thresholds
    CONFIDENCE_THRESHOLD_DEFAULT: int = 70
    CONFIDENCE_THRESHOLD_EXTREME: int = 40
    
    # Bayesian Probability Thresholds
    PROB_HEDGE_TRIGGER: float = 0.30
    PROB_REBALANCE_TRIGGER: float = 0.65
    
    # Synoptic Monitor (Henry Hub Coordinates)
    WEATHER_LAT: float = 29.95
    WEATHER_LON: float = -90.07

    # App Settings
    LOG_LEVEL: str = "INFO"
    
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
