import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config.settings import settings

# Схема для гарантированного парсинга без ошибок
class AnalysisResponse(BaseModel):
    signal: str = Field(description="Торговый сигнал: BUY, SELL или HOLD")
    confidence: int = Field(description="Уверенность в сигнале от 0 до 100")
    bullish_prob: float = Field(description="Вероятность роста (0.0 - 1.0)")
    bearish_prob: float = Field(description="Вероятность падения (0.0 - 1.0)")
    reason: str = Field(description="Подробное обоснование решения")

class MarketAnalyst:
    def __init__(self):
        # Инициализация модели с поддержкой структурированного вывода
        self.llm = ChatOpenAI(
            model=settings.AI_MODEL_ANALYST,
            temperature=0.3,
            api_key=settings.OPENROUTER_API_KEY.get_secret_value(),
            base_url=settings.OPENROUTER_BASE_URL,
        ).with_structured_output(AnalysisResponse)
        
        self.logger = logging.getLogger(__name__)

    def _get_master_prompt(self, bias: str) -> ChatPromptTemplate:
        """Формирование промпта с учетом текущего BIAS и метеоданных"""
        bias_instr = {
            "bullish": "ПРИОРpriority: LONG. Ищи точки входа в покупку. SHORT только при явном сломе тренда.",
            "bearish": "ПРИОРpriority: SHORT. Ищи точки входа в продажу. LONG запрещен или только как хедж.",
            "neutral": "Рынок нейтрален. Работай по техническим уровням и RSI."
        }.get(bias.lower(), "Действуй по ситуации.")

        system_msg = (
            "Ты — ведущий аналитик рынка природного газа (NG). Твоя цель — Hybrid Architecture v2.0.\n"
            "Вместо однозначного выбора ты должен оценивать ВЕРОЯТНОСТИ сценариев.\n"
            f"Текущий фундаментальный BIAS: {bias.upper()}.\n"
            f"Инструкция: {bias_instr}\n\n"
            "АНАЛИЗИРУЙ:\n"
            "1. Техническую картину (RSI, Trend, Momentum).\n"
            "2. Фундаментальный контекст (погода, HDD, запасы).\n"
            "3. Вероятность изменения тренда (Bayesian thinking).\n\n"
            "Выдай ответ строго в формате JSON с оценкой вероятностей для обоих направлений."
        )

        return ChatPromptTemplate.from_messages([
            ("system", system_msg),
            ("human", "МАРКЕТ-ДАННЫЕ: {market_data}\nНОВОСТНОЙ КОНТЕКСТ: {news_context}")
        ])

    async def analyze(self, market_data: Any, news_context: str, bias: str) -> AnalysisResponse:
        """
        Основной метод анализа. 
        Возвращает объект AnalysisResponse, что исключает ошибки атрибутов .get()
        """
        prompt_template = self._get_master_prompt(bias)
        chain = prompt_template | self.llm

        try:
            # Прямой вызов возвращает уже валидированный объект AnalysisResponse
            result = await chain.ainvoke({
                "market_data": str(market_data),
                "news_context": news_context
            })
            return result
        except Exception as e:
            self.logger.error(f"Критическая ошибка AI анализа: {e}")
            # Safe Fallback в случае сбоя сети или API
            return AnalysisResponse(
                signal="HOLD",
                confidence=0,
                bullish_prob=0.5,
                bearish_prob=0.5,
                reason=f"Safe Fallback: {str(e)}"
            )
