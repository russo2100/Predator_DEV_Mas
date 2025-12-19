import pandas as pd
from src.tools.technical_analysis import TechnicalAnalysis
from typing import Dict, Any


def pipeline_analysis(df: pd.DataFrame, ticker: str = "NRZ5") -> Dict[str, Any]:
    """Пайплайн с ATR и Kalman Filter для NRZ5"""

    if df.empty:
        return {"error": "No data"}

    # print(f"📥 Загрузка данных для {ticker} за 7 дн...") # Можно закомментировать лишний спам

    # Индикаторы (включая новый Kalman из technical_analysis.py)
    df_indicators = TechnicalAnalysis.add_indicators(df.copy())

    # Последние значения
    latest = df_indicators.iloc[-1]

    # Безопасное извлечение Калмана (если вдруг индикатор не посчитался)
    kalman_price = float(latest.get('Kalman_Price', latest['close']))
    kalman_signal = int(latest.get('Kalman_Signal', 0))

    analysis = {
        "ticker": ticker,
        "close": float(latest['close']),

        # --- НОВЫЕ ПОЛЯ KALMAN ---
        "Kalman_Price": round(kalman_price, 4),
        "Kalman_Trend": "UP" if kalman_signal == 1 else "DOWN",
        # -------------------------

        "RSI": round(float(latest['RSI']), 2),
        "SMA_50": round(float(latest.get('SMA_50', 0)), 4),
        "BB_Width": round(float(latest.get('BB_Width', 0)), 4),
        "ATR": 0.0, "ATR_SL": 0.0, "ATR_TP": 0.0
    }

    # ✅ ATR БЕЗОПАСНО
    try:
        atr_raw = df_indicators['ATR'].iloc[-1]
        atr = float(atr_raw)
        analysis['ATR'] = round(atr, 4)
        analysis['ATR_SL'] = round(atr * 2, 4)
        analysis['ATR_TP'] = round(atr * 4, 4)

        # Лог для проверки (можно убрать потом)
        print(f"📊 Trend: {analysis['Kalman_Trend']} | Price: {analysis['close']} | RSI: {analysis['RSI']}")

    except Exception as e:
        print(f"⚠️ ATR Error: {e}")
        analysis.update({"ATR": 0.10, "ATR_SL": 0.20, "ATR_TP": 0.40})

    return analysis

