from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from src.config.settings import settings


class PlannerAgent:
    """
    Стратегический агент (The General).
    Определяет глобальный план на день/сессию.
    """

    def __init__(self) -> None:
        self.llm = ChatOpenAI(
            model=settings.AI_MODEL_PLANNER,
            temperature=0.1,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            timeout=30,  # ← ДОБАВИТЬ
            request_timeout=30,  # ← ДОБАВИТЬ (для старых версий langchain)
            model_kwargs={"response_format": {"type": "json_object"}},
        )
    # ---- Совместимость с ShadowAdapter ----
    def createplan(self, agent_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        ShadowAdapter вызывает planner.createplan(agent_state).
        """
        market_context: Dict[str, Any] = {
            "ticker": agent_state.get("ticker", "NG"),
            "trend_d1": agent_state.get("trend_d1", "UNKNOWN"),
            "trend_h1": agent_state.get("trendh1", agent_state.get("trend_h1", "UNKNOWN")),
            "trend_5m": agent_state.get("trend_5m", agent_state.get("trend5m", "UNKNOWN")),
            "market_state": agent_state.get("market_state", agent_state.get("marketstate", "UNKNOWN")),
            "news_summary": agent_state.get("newssummary", agent_state.get("news_summary", "")),
        }
        return self.create_daily_plan(market_context)

    def create_plan(self, market_context: Dict[str, Any]) -> Dict[str, Any]:
        return self.create_daily_plan(market_context)

    def create_daily_plan(self, market_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Анализирует макро-данные и выдает план.
        Фундаментальный слой: сезон + ArcticBlastScore + EIA (Draw/Injection).
        """
        raw_news = str(market_context.get("news_summary", "") or "")

        # --- 0) Trend override (детерминированный) ---
        # Цель: стабилизировать bias под текущий тренд/состояние рынка,
        # чтобы не было "паралича" при IMPULSE_UP.
        trend_5m = str(market_context.get("trend_5m", "UNKNOWN")).upper()
        market_state = str(market_context.get("market_state", "UNKNOWN")).upper()

        trend_bias = "NEUTRAL"
        if market_state in ("IMPULSE_UP", "IMPULSEUP") or trend_5m in ("IMPULSE_UP", "UPTREND", "UP"):
            trend_bias = "BULLISH"
        elif market_state in ("IMPULSE_DOWN", "IMPULSEDOWN") or trend_5m in ("IMPULSE_DOWN", "DOWNTREND", "DOWN"):
            trend_bias = "BEARISH"

        # --- 1) Фундаментальные флаги из news_summary ---
        arctic_score = 0.0
        storage_type = "UNKNOWN"

        m_score = re.search(r"ArcticBlastScore=([0-9.]+)", raw_news)
        if m_score:
            try:
                arctic_score = float(m_score.group(1))
            except ValueError:
                arctic_score = 0.0

        m_storage = re.search(r"EIA STORAGE:\s*(Injection|Draw)", raw_news, re.IGNORECASE)
        if m_storage:
            storage_type = m_storage.group(1).capitalize()

        # --- 2) Сезон по UTC-месяцу ---
        month = datetime.datetime.utcnow().month
        if month in (12, 1, 2):
            season = "WINTER"
        elif month in (3, 4, 5, 10, 11):
            season = "SHOULDER"
        else:
            season = "SUMMER"

        # --- 3) Базовый LLM-план ---
        template = """
SYSTEM: Ты - Главный Стратег (Planner Agent) хедж-фонда.
Твоя задача - определить глобальное направление торговли на сегодня.

ВХОДНЫЕ ДАННЫЕ:
Инструмент: {ticker}
Дневной Тренд (D1): {d1_trend}
Часовой Тренд (H1): {h1_trend}
Текущий Тренд (5m): {trend_5m}
Состояние рынка (market_state): {market_state}
Фундаментальный фон (новости + погода + запасы): {news}

ПРАВИЛА:
1. Если D1 и H1 совпадают -> Трендовая торговля (Strong Direction).
2. Если разнонаправленные -> Флэт/Осторожность (Range Trading).
3. Если новости и фундамент сильно медвежьи -> избегать агрессивных BUY.

Верни строго валидный JSON (без Markdown, без пояснений):
{{
  "bias": "NEUTRAL",
  "risk_mode": "CONSERVATIVE",
  "reason": "Стратегическое обоснование (RU)",
  "allowed_bias": ["LONG_ONLY", "SHORT_ONLY", "NEUTRAL", "NO_TRADE"],
  "allowed_risk_mode": ["AGGRESSIVE", "NORMAL", "CONSERVATIVE"]
}}
        """.strip()

        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | self.llm

        input_data = {
            "ticker": market_context.get("ticker", "NG"),
            "d1_trend": market_context.get("trend_d1", "UNKNOWN"),
            "h1_trend": market_context.get("trend_h1", "UNKNOWN"),
            "trend_5m": market_context.get("trend_5m", "UNKNOWN"),
            "market_state": market_context.get("market_state", "UNKNOWN"),
            "news": raw_news or "Нет новостей",
        }

        try:
            response = chain.invoke(input_data)
            plan = json.loads(self._clean_json_string(str(response.content)))
        except Exception as e:
            print(f"⚠️ Ошибка Planner (LLM): {e}")
            plan = {
                "bias": "NEUTRAL",
                "risk_mode": "CONSERVATIVE",
                "reason": "Ошибка AI в базовом плане",
            }

        base_bias = str(plan.get("bias", "NEUTRAL"))
        base_risk = str(plan.get("risk_mode", "CONSERVATIVE"))
        reason = str(plan.get("reason", "") or "")

        # --- 3.5) Применяем Trend override как первичный bias ---
        # Переводим BULLISH/BEARISH в доступные режимы bias движка (LONG_ONLY/SHORT_ONLY).
        if trend_bias == "BULLISH":
            base_bias = "LONG_ONLY"
            reason = (reason + " | Trend override: IMPULSE_UP/UPTREND -> LONG_ONLY.").strip(" |")
        elif trend_bias == "BEARISH":
            base_bias = "SHORT_ONLY"
            reason = (reason + " | Trend override: IMPULSE_DOWN/DOWNTREND -> SHORT_ONLY.").strip(" |")

        # --- 4) Фундаментальный слой ---
        fundamental_note: list[str] = []

        if season == "WINTER" and arctic_score > 0.6 and storage_type == "Draw":
            if base_bias in ("NEUTRAL", "SHORT_ONLY"):
                base_bias = "LONG_ONLY"
            if base_risk == "CONSERVATIVE":
                base_risk = "NORMAL"
            fundamental_note.append("Фундаментальный сдвиг: зимний Arctic Blast + EIA Draw -> LONG_ONLY.")

        if storage_type == "Injection" and arctic_score < 0.3 and season != "WINTER":
            if base_bias == "LONG_ONLY":
                base_bias = "NEUTRAL"
            elif base_bias == "NEUTRAL":
                base_bias = "SHORT_ONLY"
            if base_risk == "AGGRESSIVE":
                base_risk = "NORMAL"
            fundamental_note.append(
                "Фундаментальный сдвиг: EIA Injection без сильных холодов -> storage_oversupply, смещение к SHORT/NEUTRAL."
            )

        final_reason = reason
        if fundamental_note:
            final_reason = (reason + " | " + " ".join(fundamental_note)).strip()
            
        ai_confidence = float(market_context.get("ai_confidence", 0))

        # Определяем FORCE_WEIGHT на основе risk_mode
        force_weight = 0.60  # Default для CONSERVATIVE
        if base_risk == "NORMAL":
            force_weight = 0.70
        elif base_risk == "AGGRESSIVE":
            force_weight = 0.50
            
        # 🔥 BOOST для высокой уверенности AI (≥80%)
        if ai_confidence >= 85:
            force_weight = min(force_weight + 0.10, 0.70)  # +10%, max 0.70
            print(f"🚀 FORCE_WEIGHT boosted: {force_weight:.2f} (AI conf: {ai_confidence}%)")
        elif ai_confidence >= 80:
            force_weight = min(force_weight + 0.05, 0.65)  # +5%, max 0.65
            print(f"⬆️ FORCE_WEIGHT increased: {force_weight:.2f} (AI conf: {ai_confidence}%)")

        final_plan: Dict[str, Any] = {
            "bias": base_bias,
            "risk_mode": base_risk,
            "reason": final_reason,
            "signal": base_bias,
            "force_weight": force_weight,  # ← ДОБАВИТЬ
        }


        print(f"📜 [Planner] Стратегия на сессию: {final_plan['bias']} ({final_plan['risk_mode']})")
        return final_plan

    @staticmethod
    def _clean_json_string(text: str) -> str:
        text = text.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return m.group(0) if m else text
