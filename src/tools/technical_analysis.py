import pandas as pd
import numpy as np
from pykalman import KalmanFilter  # <--- Импортируем библиотеку


class TechnicalAnalysis:
    """
    Модуль технического анализа с поддержкой Kalman Filter.
    """

    @staticmethod
    def get_latest_atr(df: pd.DataFrame) -> float:
        if 'ATR' in df.columns and not df.empty:
            return float(df['ATR'].iloc[-1])
        return 0.10

    @staticmethod
    def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        try:
            # Приводим к числам (защита от мусора)
            cols = ['close', 'high', 'low', 'open', 'volume']
            for col in cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            close = df['close']
            high = df['high']
            low = df['low']

            # --- 1. Фильтр Калмана (НОВОЕ) ---
            # Инициализируем фильтр: предполагаем, что цена движется плавно (transition_covariance мал)
            kf = KalmanFilter(
                transition_matrices=[1],
                observation_matrices=[1],
                initial_state_mean=close.iloc[0],
                initial_state_covariance=1,
                observation_covariance=1,
                transition_covariance=0.01
            )
            # Применяем фильтр ко всему ряду close
            state_means, _ = kf.filter(close.values)
            # Добавляем в DataFrame новую колонку
            df['Kalman_Price'] = state_means.flatten()

            # Добавляем сигнал "Kalman Trend": Цена выше Калмана = UPTREND
            df['Kalman_Signal'] = np.where(
                df['close'] > df['Kalman_Price'], 1, -1)

            # --- 2. Старые индикаторы (SMA, RSI, BB, ATR) ---

            # SMA 50 (для сравнения)
            df['SMA_50'] = close.rolling(window=50).mean()

            # RSI
            delta = close.diff()
            gain = delta.clip(lower=0)
            loss = -1 * delta.clip(upper=0)  # type: ignore
            avg_gain = gain.rolling(window=14).mean()
            avg_loss = loss.rolling(window=14).mean()
            rs = avg_gain / avg_loss
            df['RSI'] = 100 - (100 / (1 + rs))
            df['RSI'] = df['RSI'].fillna(50)

            # Bollinger Bands
            sma_20 = close.rolling(window=20).mean()
            std_20 = close.rolling(window=20).std()
            df['BB_Width'] = ((sma_20 + (std_20 * 2)) -
                              (sma_20 - (std_20 * 2))) / sma_20

            # ATR
            prev_close = close.shift(1)
            tr1 = high - low
            tr2 = (high - prev_close).abs()
            tr3 = (low - prev_close).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df['ATR'] = tr.rolling(window=14).mean()

        except Exception as e:
            print(f"⚠️ Ошибка индикаторов (Kalman/TA): {e}")

        df.dropna(inplace=True)
        return df
