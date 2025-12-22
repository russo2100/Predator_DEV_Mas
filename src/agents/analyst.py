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
        bias_restriction = ""
        if bias.lower() == "bearish":
            bias_restriction = "🔴 РЕЖИМ: BEARISH (SHORT_ONLY). BUY КАТЕГОРИЧЕСКИ ЗАПРЕЩЕН. Только SELL или HOLD."
        elif bias.lower() == "bullish":
            bias_restriction = "🟢 РЕЖИМ: BULLISH (LONG_ONLY). SELL КАТЕГОРИЧЕСКИ ЗАПРЕЩЕН. Только BUY или HOLD."
        else:
            bias_restriction = "🟡 РЕЖИМ: NEUTRAL. Разрешены BUY, SELL и HOLD."

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

    async def analyze(self, market_data: Any, news_context: str, bias: str) -> AnalystResult:
        """
         market_data может быть строкой (из нашего хака) или словарем.
         Мы безопасно вытаскиваем из него значения.
        """
        # Безопасное извлечение данных для промпта
        if isinstance(market_data, str):
            # Если пришла строка, пробуем найти числа через регулярки или ставим заглушки
            price = re.search(r"Price: ([\d.]+)", market_data)
            rsi = re.search(r"RSI: ([\d.]+)", market_data)
            trend = re.search(r"Trend: (\w+)", market_data)
            mom = re.search(r"Momentum: ([\d.-]+)", market_data)
            
            p_val = price.group(1) if price else "N/A"
            r_val = rsi.group(1) if rsi else "50"
            t_val = trend.group(1) if trend else "FLAT"
            m_val = mom.group(1) if mom else "0"
        else:
            # Если пришел словарь
            p_val = market_data.get("close", "N/A")
            r_val = market_data.get("RSI", "50")
            t_val = market_data.get("Kalman_Trend", "FLAT")
            m_val = market_data.get("momentum_24h", "0")

        # Формируем промпт
        prompt_text = self._get_master_prompt(bias).format(
            price=p_val,
            rsi=r_val,
            kalman_trend=t_val,
            momentum_24h=m_val,
            manual_news=news_context,
            api_news="EIA Reports / Weather Models"
        )

        try:
            # Вызов LLM
            response = await self.llm.ainvoke(prompt_text)
            content = response.content
            
            # Парсим JSON
            data = json.loads(content)
            return AnalystResult(
                signal=data.get("signal", "HOLD"),
                reason=data.get("reason", "No reason provided"),
                confidence=data.get("confidence", 0),
                levels=data.get("levels", {})
            )
        except Exception as e:
            print(f"❌ Analyst AI Error: {e}")
            return AnalystResult("HOLD", f"AI Analysis failed: {e}", 0)
