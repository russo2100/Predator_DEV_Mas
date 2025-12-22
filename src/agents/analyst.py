import json
import re
import asyncio
from typing import Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config.settings import settings

class AnalystResult:
    def __init__(self, signal: str, reason: str, confidence: int, levels: Optional[Dict] = None):
        self.signal = signal.upper()
        self.reason = reason
        self.confidence = min(100, max(0, confidence))
        self.levels = levels or {}

class MarketAnalyst:
    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.AI_MODEL_ANALYST,
            temperature=0.3,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    def _get_master_prompt(self, bias: str) -> str:
        # Формируем блок ограничений в зависимости от режима
        bias_restriction = ""
        if bias.lower() == "bearish":
            bias_restriction = "🔴 РЕЖИМ: BEARISH (SHORT_ONLY). BUY КАТЕГОРИЧЕСКИ ЗАПРЕЩЕН. Только SELL или HOLD."
        else:
            bias_restriction = "🟢 РЕЖИМ: NEUTRAL. Разрешены BUY, SELL и HOLD."

        return f"""
# MASTER PROMPTING SYSTEM: NG TRADING AGENT v3.0
================================================
РОЛЬ: Senior Commodity Analyst & Algo Trader (Natural Gas Focus).
{bias_restriction}

### ТЕКУЩИЕ ДАННЫЕ (LIVE):
- ЦЕНА: ${{price}} | RSI: {{rsi}} 
- ТРЕНД (KALMAN): {{kalman_trend}} | MOMENTUM (24h): {{momentum_24h}}%

### ИСТОЧНИКИ ДАННЫХ:
1. NEWS_FEED: {{manual_news}} (Приоритет)
2. MACRO_DATA: {{api_news}} (Trading Economics/EIA)
3. BROKER_DATA: Т-Инвест/Пульс

### АЛГОРИТМ RSIP (8 ШАГОВ):
1. VALIDATION: Фильтруй шум news.txt. Ищи Level 1 события (LNG, EIA).
2. DEEP THINK: Соотнеси запасы и погоду (La Niña). Поддержка $3.570.
3. RSIP CRITIQUE: Найди 3 причины, почему твой план ПРОВАЛИТСЯ.
4. RSIP REFACTOR: Перепиши решение с учетом найденных рисков.
5. SENTIMENT: Оцени Sentiment Score (-5 до +5).
6. RISK: SL/TP на основе ATR. Лимит сделки 2%.

ВЫДАЙ ОТВЕТ СТРОГО В JSON:
{{{{
  "signal": "BUY/SELL/HOLD",
  "confidence": 0-100,
  "reason": "3 тезиса + результат RSIP критики",
  "levels": {{{{ "entry": {{price}}, "sl": 0, "tp": 0 }}}},
  "warning": "фактор отмены"
}}}}
"""

    async def analyze(self, market_data: Dict[str, Any], news_context: str = "", bias: str = "neutral") -> AnalystResult:
        template = self._get_master_prompt(bias)
        
        # Подготовка данных (безопасное извлечение)
        input_data = {{
            "price": market_data.get("close", 0),
            "rsi": round(market_data.get("RSI", 50), 2),
            "kalman_trend": market_data.get("Kalman_Trend", "N/A"),
            "momentum_24h": round(market_data.get("Momentum_24h", 0), 2),
            "manual_news": news_context if news_context else "Нет данных",
            "api_news": "Ожидание данных Trading Economics..."
        }}

        try:
            prompt = ChatPromptTemplate.from_template(template)
            chain = prompt | self.llm
            response = await chain.ainvoke(input_data)
            
            # Извлечение и парсинг JSON
            clean_json = re.search(r"\{{.*\}}", response.content, re.DOTALL).group(0)
            res = json.loads(clean_json)
            
            # Финальная проверка BIAS перед выдачей (защита от галлюцинаций ИИ)
            if bias.lower() == "bearish" and res.get("signal") == "BUY":
                res["signal"] = "HOLD"
                res["reason"] = "INTERCEPT: BUY запрещен в BEARISH режиме."

            return AnalystResult(
                signal=res.get("signal", "HOLD"),
                reason=res.get("reason", ""),
                confidence=res.get("confidence", 50),
                levels=res.get("levels", {{}})
            )
        except Exception as e:
            return AnalystResult("HOLD", f"Ошибка анализа: {{str(e)}}", 0)

    def analyze_market_situation(self, candle_data: Dict[str, Any], news_summary: str = "") -> Dict[str, Any]:
        # Синхронная обертка для main.py
        return asyncio.run(self.analyze(candle_data, news_summary))
