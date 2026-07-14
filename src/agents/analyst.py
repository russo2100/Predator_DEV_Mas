# src/agents/analyst.py
import os
import json
import logging
from typing import Any

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from src.config.settings import settings


class AnalysisResponse(BaseModel):
    signal: str = Field(description="Торговый сигнал: BUY, SELL или HOLD")
    confidence: int = Field(description="Уверенность в сигнале от 0 до 100")
    bullish_prob: float = Field(description="Вероятность роста (0.0 - 1.0)")
    bearish_prob: float = Field(description="Вероятность падения (0.0 - 1.0)")
    reason: str = Field(description="Подробное обоснование решения")


class MarketAnalyst:
    def __init__(self) -> None:
        self.llm = ChatOpenAI(
            model=settings.AI_MODEL_ANALYST,
            temperature=0.3,
            api_key=settings.OPENROUTER_API_KEY.get_secret_value(),
            base_url=settings.OPENROUTER_BASE_URL,
            model_kwargs={"response_format": {"type": "json_object"}},
            timeout=30,
            max_retries=2,
        )
        self.logger = logging.getLogger(__name__)

    def get_master_prompt(self, bias: str) -> ChatPromptTemplate:
        bias_lower = (bias or "neutral").lower()

        if bias_lower == "bullish":
            bias_instr = "Приоритет LONG-сценариев; SHORT только при явных признаках разворота."
        elif bias_lower == "bearish":
            bias_instr = "Приоритет SHORT-сценариев; LONG только при явных признаках разворота."
        else:
            bias_instr = "Нейтральный режим: действуй по подтверждению RSI/тренда/волатильности."

        bias_upper = (bias or "neutral").upper()

        system_msg = (
            "NG Hybrid Architecture v2.0.\n"
            f"BIAS={bias_upper}\n"
            f"{bias_instr}\n\n"
            "Верни СТРОГО валидный JSON-объект без markdown и без пояснений.\n"
            "Схема JSON:\n"
            "{{\n"
            '  "signal": "BUY|SELL|HOLD",\n'
            '  "confidence": 0-100,\n'
            '  "bullish_prob": 0.0-1.0,\n'
            '  "bearish_prob": 0.0-1.0,\n'
            '  "reason": "string"\n'
            "}}\n"
        )

        return ChatPromptTemplate.from_messages(
            [
                ("system", system_msg),
                ("human", "marketdata:\n{marketdata}\n\nnewscontext:\n{newscontext}\n"),
            ]
        )

    async def analyze(self, marketdata: Any, newscontext: str, bias: str = "neutral") -> AnalysisResponse:
        prompt = self.get_master_prompt(bias)
        chain = prompt | self.llm

        try:
            resp = await chain.ainvoke(
                {
                    "marketdata": str(marketdata),
                    "newscontext": newscontext or "",
                }
            )
            payload = json.loads(resp.content)
            return AnalysisResponse(**payload)

        except Exception as e:
            self.logger.error(f"AI analysis error: {e}")
            return AnalysisResponse(
                signal="HOLD",
                confidence=0,
                bullish_prob=0.5,
                bearish_prob=0.5,
                reason=f"Safe Fallback: {e}",
            )
