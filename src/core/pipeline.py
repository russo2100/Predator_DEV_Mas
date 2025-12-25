import pandas as pd
from src.tools.technical_analysis import TechnicalAnalysis
from typing import Dict, Any


def pipeline_analysis(df: pd.DataFrame, ticker: str = "NRZ5") -> Dict[str, Any]:
    """Пайплайн с ATR, Kalman Filter и Market Structure для NRZ5"""

    if df.empty:
        return {"error": "No data"}

    # Индикаторы (включая Kalman из technical_analysis.py)
    df_indicators = TechnicalAnalysis.add_indicators(df.copy())

    # 🆕 ОПРЕДЕЛЯЕМ СТРУКТУРУ РЫНКА
    structure = TechnicalAnalysis.detect_structure(df_indicators)

    # Последние значения
    latest = df_indicators.iloc[-1]

    # Безопасное извлечение Калмана
    kalman_price = float(latest.get('Kalman_Price', latest['close']))
    kalman_signal = int(latest.get('Kalman_Signal', 0))

    analysis = {
        "ticker": ticker,
        "close": float(latest['close']),

        # --- KALMAN FIELDS ---
        "Kalman_Price": round(kalman_price, 4),
        "Kalman_Trend": "UP" if kalman_signal == 1 else "DOWN",

        # --- 🆕 MARKET STRUCTURE FIELDS ---
        "market_state": structure['market_state'],
        "range_width_atr": round(structure['range_width_atr'], 2),
        "impulse_strength": round(structure['impulse_strength'], 2),
        "structure_confidence": round(structure['confidence'], 2),

        # --- TECHNICAL INDICATORS ---
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

        # 🆕 Лог с market_state
        print(f"📊 [{analysis['market_state']}] Trend: {analysis['Kalman_Trend']} | "
              f"Price: {analysis['close']} | RSI: {analysis['RSI']} | "
              f"Strength: {analysis['impulse_strength']}%")

    except Exception as e:
        print(f"⚠️ ATR Error: {e}")
        analysis.update({"ATR": 0.10, "ATR_SL": 0.20, "ATR_TP": 0.40})

    return analysis
