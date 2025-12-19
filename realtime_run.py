import asyncio
import os
from datetime import datetime

import pandas as pd

from src.core.pipeline import pipeline_analysis
from src.agents.analyst import MarketAnalyst
from src.agents.risk_agent import RiskAgent
from src.agents.planner import PlannerAgent
from src.tools.rss_collector import NewsCollector
from src.tools.weather_collector import WeatherCollector
from src.tools.eia_collector import EIACollector
from src.config.settings import settings
from data_provider import DataProvider  # твой существующий модуль


# --- Настройки ---
TICKER = "NGZ5"  # сюда поставь фактический тикер газа у Tinkoff (как в боевом боте)
DAYS_BACK = 1    # сколько дней истории брать для анализа
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "mas_realtime_log.csv")


async def run_realtime():
    print("🚀 Запуск реального MAS (read-only, лог в CSV)...")

    # 1. Загружаем реальные свечи из Tinkoff
    provider = DataProvider(settings.TINKOFF_TOKEN.get_secret_value())
    df = await provider.get_candles(ticker=TICKER, days_back=DAYS_BACK)
    if df.empty:
        print("❌ Нет данных по тикеру, выходим.")
        return

    # Берём последние 100 свечей для анализа
    df_slice = df.tail(100).copy()

    # 2. Сбор новостей (как в simulation_run)
    print("\n🌍 [1/4] Сбор новостного фона...")
    news_sentiment = ""

    try:
        if os.path.exists("news.txt"):
            with open("news.txt", "r", encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) > 10:
                news_sentiment = f"MANUAL INSIGHT: {content}"
                print("✅ Использован ручной файл news.txt (Приоритет)")
    except Exception as e:
        print(f"⚠️ Ошибка чтения файла: {e}")

    if not news_sentiment:
        print("📂 Файл news.txt пуст. Запускаю RSS Парсер...")
        try:
            news_agent = NewsCollector()
            news_sentiment = news_agent.get_market_sentiment_news()
            print(f"📰 RSS Сентимент: \n{news_sentiment}")
        except Exception as e:
            print(f"⚠️ Ошибка RSS: {e}")
            news_sentiment = "NEUTRAL (No Data)"

    # 1b. Погода + EIA
    print("\n🌡️ [1b] Фундаментальный фон (Погода + EIA)...")
    weather_score = 0.0
    weather_alerts = []
    try:
        wc = WeatherCollector()
        weather_score, weather_alerts = wc.get_arctic_blast_probability()
        print(f"🌬 Arctic Blast Score: {weather_score}, alerts: {len(weather_alerts)}")
    except Exception as e:
        print(f"⚠️ Ошибка WeatherCollector: {e}")

    storage_context = "NO EIA DATA"
    try:
        eia = EIACollector()
        report = eia.get_latest_storage()
        if report:
            direction = report["type"]
            change = report["change"]
            storage_context = (
                f"EIA STORAGE: {direction} {abs(change)} Bcf on {report['date']}"
            )
        print(f"📊 {storage_context}")
    except Exception as e:
        print(f"⚠️ Ошибка EIACollector: {e}")

    weather_part = (
        f"WEATHER: ArcticBlastScore={weather_score}, Alerts={weather_alerts[:3]}"
    )
    fundamental_context = f"{weather_part} | {storage_context}"

    # 2. Инициализация агентов
    print("\n🤖 [2/4] Инициализация Агентов...")
    planner = PlannerAgent()
    alpha = MarketAnalyst()
    risk = RiskAgent()

    # 3. Рыночные данные (через pipeline)
    market_data = pipeline_analysis(df_slice)

    market_context = {
        "ticker": TICKER,
        "trend_d1": "UNKNOWN",  # для реала можно будет подтянуть D1 отдельно
        "trend_h1": market_data.get("Kalman_Trend"),
        "news_summary": f"{news_sentiment}\nFUNDAMENTALS: {fundamental_context}",
    }

    # 4. Planner
    print("\n📜 [3/4] Планировщик думает...")
    daily_plan = planner.create_daily_plan(market_context)

    # 5. Alpha + Risk
    print("\n⚔️ [4/4] Поиск сделки...")
    alpha_signal = alpha.analyze_market_situation(
        market_data,
        news_summary=(
            f"STRATEGY: {daily_plan['bias']}. "
            f"NEWS: {news_sentiment}. "
            f"FUNDAMENTALS: {fundamental_context}"
        ),
    )

    print(
        f"💬 Сигнал Альфы: {alpha_signal.get('signal')} "
        f"({alpha_signal.get('confidence')}%)"
    )
    print(f"📝 Причина: {alpha_signal.get('reason')}")

    risk_verdict = risk.evaluate_trade(alpha_signal, market_data)

    print("\n⚖️ ВЕРДИКТ:")
    if risk_verdict["allowed"]:
        print(f"✅ ОДОБРЕНО! (SL: {risk_verdict['modified_sl']})")
    else:
        print(f"⛔ ОТКЛОНЕНО: {risk_verdict['reason']}")

    # 6. Лог в CSV
    print(f"\n📝 Запись результата в {LOG_FILE} ...")
    row = {
        "timestamp": datetime.utcnow().isoformat(),
        "ticker": TICKER,
        "price_close": market_data.get("close"),
        "kalman_trend": market_data.get("Kalman_Trend"),
        "rsi": market_data.get("RSI"),
        "arctic_score": weather_score,
        "eia_context": storage_context,
        "planner_bias": daily_plan.get("bias"),
        "planner_risk_mode": daily_plan.get("risk_mode"),
        "planner_reason": daily_plan.get("reason"),
        "alpha_signal": alpha_signal.get("signal"),
        "alpha_confidence": alpha_signal.get("confidence"),
        "alpha_reason": alpha_signal.get("reason"),
        "risk_allowed": risk_verdict.get("allowed"),
        "risk_reason": risk_verdict.get("reason"),
        "risk_sl": risk_verdict.get("modified_sl"),
        "risk_tp": risk_verdict.get("modified_tp"),
    }

    # Если файла нет — создаём с заголовком, если есть — дописываем
    if not os.path.exists(LOG_FILE):
        pd.DataFrame([row]).to_csv(LOG_FILE, index=False)
    else:
        pd.DataFrame([row]).to_csv(LOG_FILE, mode="a", header=False, index=False)

    print("✅ Запись в CSV завершена.")


if __name__ == "__main__":
    asyncio.run(run_realtime())
