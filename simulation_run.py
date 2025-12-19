import asyncio
import os
import json
from datetime import datetime

import pandas as pd

from src.core.pipeline import pipeline_analysis
from src.agents.analyst import MarketAnalyst
from src.agents.risk_agent import RiskAgent
from src.agents.planner import PlannerAgent
from src.tools.rss_collector import NewsCollector
from src.tools.weather_collector import WeatherCollector
from src.tools.eia_collector import EIACollector
from src.config.settings import settings  # если используется где‑то ещё


STATE_FILE = "state.json"
DATA_FILE = "data/test_market_data.csv"


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        state["updated_at"] = datetime.utcnow().isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения state.json: {e}")


async def run_simulation():
    print("🚀 Запуск симуляции (Full Auto MAS)...")

    state = load_state()

    if not os.path.exists(DATA_FILE):
        print("❌ Нет файла данных")
        return

    # 1. Сбор новостей (гибридный режим)
    print("\n🌍 [1/4] Сбор новостного фона...")

    news_sentiment = ""

    # Попытка А: ручной инсайд из файла
    try:
        if os.path.exists("news.txt"):
            with open("news.txt", "r", encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) > 10:
                news_sentiment = f"MANUAL INSIGHT: {content}"
                print("✅ Использован ручной файл news.txt (Приоритет)")
    except Exception as e:
        print(f"⚠️ Ошибка чтения news.txt: {e}")

    # Попытка Б: RSS, если файла нет/пустой
    if not news_sentiment:
        print("📂 Файл news.txt пуст. Запускаю RSS Парсер...")
        try:
            news_agent = NewsCollector()
            news_sentiment = news_agent.get_market_sentiment_news()
            print(f"📰 RSS Сентимент:\n{news_sentiment}")
        except Exception as e:
            print(f"⚠️ Ошибка RSS: {e}")
            news_sentiment = "NEUTRAL (No Data)"

    # 1b. Фундаментал: Погода + Запасы
    print("\n🌡️ [1b] Фундаментальный фон (Погода + EIA)...")

    weather_score = 0.0
    weather_alerts = []
    weather_override = ""

    # 1) Пытаемся прочитать ручной файл погоды
    try:
        if os.path.exists("weather_manual.txt"):
            with open("weather_manual.txt", "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                weather_override = content
                # print("✅ Использован ручной файл weather_manual.txt (Погода)")
    except Exception as e:
        print(f"⚠️ Ошибка чтения weather_manual.txt: {e}")

    # 2) Если ручного нет — берём погоду из WeatherCollector
    if not weather_override:
        try:
            wc = WeatherCollector()
            weather_score, weather_alerts = wc.get_arctic_blast_probability()
            print(f"🌬 Arctic Blast Score: {weather_score}, alerts: {len(weather_alerts)}")
            weather_part = (
                f"WEATHER: ArcticBlastScore={weather_score}, "
                f"Alerts={weather_alerts[:3]}"
            )
        except Exception as e:
            print(f"⚠️ Ошибка WeatherCollector: {e}")
            weather_part = "WEATHER: ArcticBlastScore=0.0, Alerts=[]"
    else:
        # Если есть ручной override — просто прокидываем строку как есть
        weather_part = f"WEATHER (MANUAL): {weather_override}"

    # 3) Запасы (EIA Weekly Storage) – тихий режим
    storage_context = "EIA: NO DATA"
    try:
        eia = EIACollector()
        report = eia.get_latest_storage()
        if report:
            direction = report["type"]  # Injection / Draw
            change = report["change"]
            storage_context = (
                f"EIA STORAGE: {direction} {abs(change)} Bcf on {report['date']}"
            )
            # при желании можно раскомментировать:
            # print(f"📊 {storage_context}")
    except Exception:
        # тихий фолбек, без 403 в лог
        storage_context = "EIA: NO DATA"

    # Собираем фундаментальный контекст
    fundamental_context = f"{weather_part} | {storage_context}"

    # Сохраняем состояние (новости + фундаментал)
    save_state(
        {
            "news_sentiment": news_sentiment,
            "fundamental_context": fundamental_context,
        }
    )

    # 2. Инициализация агентов
    print("\n🤖 [2/4] Инициализация Агентов...")
    planner = PlannerAgent()
    alpha = MarketAnalyst()
    risk = RiskAgent()

    # 3. Рыночные данные
    df = pd.read_csv(DATA_FILE)
    df_slice = df.iloc[-100:].copy()
    market_data = pipeline_analysis(df_slice)

    print(
        f"📊 Trend: {market_data.get('Kalman_Trend')} | "
        f"Price: {market_data.get('Close')} | "
        f"RSI: {market_data.get('RSI')}"
    )

    # Если по какой-то причине news_sentiment пуст, пробуем взять из state.json
    if not news_sentiment and state.get("news_sentiment"):
        news_sentiment = state["news_sentiment"]

    # То же для фундаментала
    fundamental_from_state = state.get("fundamental_context")

    # Контекст для планировщика
    market_context = {
        "ticker": "NRZ5",
        "trend_d1": "UP",  # пока хардкод
        "trend_h1": market_data.get("Kalman_Trend"),
        "news_summary": (
            f"{news_sentiment}\n"
            f"FUNDAMENTALS: {fundamental_from_state or fundamental_context}"
        ),
    }

    # 4. Планирование
    print("\n📜 [3/4] Планировщик думает...")
    daily_plan = planner.create_daily_plan(market_context)

    if daily_plan["bias"] == "NO_TRADE":
        print("⛔ Планировщик запретил торговлю.")
        return

    # 5. Исполнение (Alpha + Risk)
    print("\n⚔️ [4/4] Поиск сделки...")

    alpha_signal = alpha.analyze_market_situation(
        market_data,
        news_summary=(
            f"STRATEGY: {daily_plan['bias']}. "
            f"NEWS: {news_sentiment}. "
            f"FUNDAMENTALS: {fundamental_from_state or fundamental_context}"
        ),
    )

    print(
        f"💬 Сигнал Альфы: {alpha_signal.get('signal')} "
        f"({alpha_signal.get('confidence')}%)"
    )
    print(f"📝 Причина: {alpha_signal.get('reason')}")

    # Risk сверяет с планом
    risk_verdict = risk.evaluate_trade(alpha_signal, market_data)

    if daily_plan["bias"] == "SHORT_ONLY" and alpha_signal["signal"] == "BUY":
        risk_verdict["allowed"] = False
        risk_verdict["reason"] += " | VIOLATION: Planner said SHORT_ONLY"

    print("\n⚖️ ВЕРДИКТ:")
    if risk_verdict["allowed"]:
        print(f"✅ ОДОБРЕНО! (SL: {risk_verdict['modified_sl']})")
    else:
        print(f"⛔ ОТКЛОНЕНО: {risk_verdict['reason']}")


if __name__ == "__main__":
    asyncio.run(run_simulation())
