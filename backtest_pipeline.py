import asyncio
import pandas as pd
from datetime import datetime
from src.core.pipeline import pipeline_analysis
from src.agents.analyst import MarketAnalyst
from src.agents.risk_agent import RiskAgent
from src.agents.planner import PlannerAgent

async def run_backtest(csv_path: str):
    print(f"🚀 Запуск бэктеста на данных: {csv_path}")
    
    # 1. Загрузка данных
    df = pd.read_csv(csv_path)
    if df.empty:
        print("❌ Файл данных пуст")
        return

    # 2. Инициализация агентов
    planner = PlannerAgent()
    analyst = MarketAnalyst()
    risk = RiskAgent()
    
    # Виртуальный портфель
    balance = 0.0
    position = 0 # 0 - нет, 1 - long
    entry_price = 0.0
    trades_log = []

    # 3. Симуляция (проходим по свечам с шагом)
    # Начинаем с 100-й свечи, чтобы у индикаторов (SMA/RSI) были данные для расчета
    for i in range(100, len(df)):
        current_slice = df.iloc[i-100:i+1].copy()
        market_data = pipeline_analysis(current_slice)
        current_price = market_data['close']
        
        # Контекст для Планера (симулируем нейтральный фон)
        market_context = {
            "ticker": "NRZ5",
            "trend_h1": market_data.get("Kalman_Trend"),
            "news_summary": "BACKTEST: Neutral market conditions"
        }
        
        # Логика Агентов
        plan = planner.create_daily_plan(market_context)
        ai_signal = await analyst.analyze(market_data, bias=plan['bias'])
        verdict = risk.evaluate_trade(ai_signal.__dict__, market_data)

        # Симуляция исполнения
        if position == 0 and verdict['allowed'] and ai_signal.signal == "BUY":
            # ВХОД В LONG
            position = 1
            entry_price = current_price
            print(f"➕ BUY  @ {entry_price:.3f} | Причина: {ai_signal.reason}")
            trades_log.append({"type": "BUY", "price": entry_price, "time": i})

        elif position == 1:
            # ПРОВЕРКА ВЫХОДА (Signal SELL или StopLoss)
            pnl = (current_price - entry_price) / entry_price * 100
            
            # Условие выхода: сигнал SELL или PnL < -1.5% (Hard Stop)
            if ai_signal.signal == "SELL" or pnl < -1.5:
                position = 0
                profit = current_price - entry_price
                balance += profit
                print(f"➖ SELL @ {current_price:.3f} | PnL: {pnl:+.2f}%")
                trades_log.append({"type": "SELL", "price": current_price, "pnl_pct": pnl})

    # 4. Итоговый отчет
    print("\n" + "="*30)
    print(f"📊 ИТОГИ ТЕСТИРОВАНИЯ:")
    print(f"Всего сделок: {len(trades_log) // 2}")
    print(f"Чистая прибыль (в пунктах): {balance:.4f}")
    print("="*30)

if __name__ == "__main__":
    asyncio.run(run_backtest("data/test_market_data.csv"))
