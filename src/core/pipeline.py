import pandas as pd
from src.tools.technical_analysis import TechnicalAnalysis
from typing import Dict, Any


def pipeline_analysis(df: pd.DataFrame, ticker: str = "NRZ5") -> Dict[str, Any]:
    """Пайплайн с ATR, Kalman Filter и Market Structure для NRZ5"""

    if df is None or df.empty:
        return {
            "close": 0.0,
            "RSI": 50.0,
            "trend": "FLAT",
            "ATR": 0.1,
            "ATR_SL": 0.05,
            "ATR_TP": 0.15,
            "market_state": "RANGE",
        }


    # Индикаторы (включая Kalman из technical_analysis.py)
    df_indicators = TechnicalAnalysis.add_indicators(df.copy())

    # Soft fallback: if add_indicators wiped all rows, keep raw df and add minimal columns
    if df_indicators is None or df_indicators.empty:
        df_indicators = df.copy()
        if "RSI" not in df_indicators.columns:
            df_indicators["RSI"] = 50.0
        if "ATR" not in df_indicators.columns:
            df_indicators["ATR"] = 0.0
        if "Kalman_Price" not in df_indicators.columns:
            df_indicators["Kalman_Price"] = df_indicators["close"]
        if "Kalman_Signal" not in df_indicators.columns:
            df_indicators["Kalman_Signal"] = 0
        if "SMA_50" not in df_indicators.columns:
            df_indicators["SMA_50"] = df_indicators["close"]
        if "BB_Width" not in df_indicators.columns:
            df_indicators["BB_Width"] = 0.0

    # Compute RSI/ATR locally if they are missing or constant placeholders
    try:
        if "RSI" not in df_indicators.columns or df_indicators["RSI"].isna().all() or (df_indicators["RSI"] == 50.0).all():
            delta = df_indicators["close"].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            avg_gain = gain.rolling(window=14, min_periods=1).mean()
            avg_loss = loss.rolling(window=14, min_periods=1).mean()
            rs = avg_gain / (avg_loss + 1e-9)
            df_indicators["RSI"] = 100 - (100 / (1 + rs))
        if "ATR" not in df_indicators.columns or df_indicators["ATR"].isna().all() or (df_indicators["ATR"] == 0.0).all():
            prev_close = df_indicators["close"].shift(1)
            tr1 = (df_indicators["high"] - df_indicators["low"]).abs()
            tr2 = (df_indicators["high"] - prev_close).abs()
            tr3 = (df_indicators["low"] - prev_close).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df_indicators["ATR"] = tr.rolling(window=14, min_periods=1).mean()
    except Exception as _e:
        pass




    # 🆕 ОПРЕДЕЛЯЕМ СТРУКТУРУ РЫНКА
    structure = TechnicalAnalysis.detect_structure(df_indicators)

    # Последние значения
    # Safety check for empty indicators
    if df_indicators is None or df_indicators.empty:
        last_close = float(df.iloc[-1]["close"]) if df is not None and not df.empty else 0.0
        return {
            "close": last_close,
            "RSI": 50.0,
            "trend": "FLAT",
            "ATR": 0.1,
            "ATR_SL": 0.05,
            "ATR_TP": 0.15,
            "market_state": "RANGE",
        }

    # Clean NaNs before taking last row

    if df_indicators.empty:
        last_close = float(df.iloc[-1]["close"]) if df is not None and not df.empty else 0.0
        return {
            "close": last_close,
            "RSI": 50.0,
            "trend": "FLAT",
            "ATR": 0.1,
            "ATR_SL": 0.05,
            "ATR_TP": 0.15,
            "market_state": "RANGE",
        }

    df_indicators_clean = df_indicators.dropna()
    # If indicator frame became empty (due to filtering), fall back to raw df
    if df_indicators is None or df_indicators.empty:
        df_indicators = df.copy()

    latest = (df_indicators_clean.iloc[-1] if not df_indicators_clean.empty else None)

    if latest is None:
        last_close = float(df.iloc[-1]["close"]) if df is not None and not df.empty else 0.0
        return {
            "close": last_close,
            "RSI": 50.0,
            "trend": "FLAT",
            "ATR": 0.1,
            "ATR_SL": 0.05,
            "ATR_TP": 0.15,
            "market_state": "RANGE",
        }


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
