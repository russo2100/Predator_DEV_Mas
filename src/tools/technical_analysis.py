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

    @staticmethod
    def detect_structure(df: pd.DataFrame) -> dict:
        """
        Определяет структуру рынка: IMPULSE_UP, IMPULSE_DOWN, CORRECTION, RANGE
        
        Returns:
            dict: {
                'market_state': str,
                'range_width_atr': float,
                'impulse_strength': float,
                'confidence': float
            }
        """
        if df.empty or len(df) < 20:
            return {
                'market_state': 'RANGE',
                'range_width_atr': 0.0,
                'impulse_strength': 0.0,
                'confidence': 0.5
            }
        
        try:
            # Получаем последние данные
            last = df.iloc[-1]
            last_20 = df.tail(20)
            
            close = last['close']
            atr = last.get('ATR', 0.10)
            bb_width = last.get('BB_Width', 0.02)
            kalman_signal = last.get('Kalman_Signal', 0)
            
            # Вычисляем силу движения за последние 20 свечей
            price_change = (close - last_20['close'].iloc[0]) / last_20['close'].iloc[0] * 100
            price_change_atr = abs(close - last_20['close'].iloc[0]) / atr if atr > 0 else 0
            
            # Волатильность (нормализованная)
            volatility = bb_width
            
            # --- ЛОГИКА КЛАССИФИКАЦИИ ---
            
            # 1. RANGE: узкий диапазон, низкая волатильность
            if bb_width < 0.02 and price_change_atr < 1.5:
                return {
                    'market_state': 'RANGE',
                    'range_width_atr': price_change_atr,
                    'impulse_strength': abs(price_change),
                    'confidence': 0.85
                }
            
            # 2. IMPULSE_UP: сильное движение вверх + Kalman подтверждает
            if price_change > 1.0 and price_change_atr > 2.0 and kalman_signal > 0:
                return {
                    'market_state': 'IMPULSE_UP',
                    'range_width_atr': price_change_atr,
                    'impulse_strength': price_change,
                    'confidence': 0.90
                }
            
            # 3. IMPULSE_DOWN: сильное движение вниз + Kalman подтверждает
            if price_change < -1.0 and price_change_atr > 2.0 and kalman_signal < 0:
                return {
                    'market_state': 'IMPULSE_DOWN',
                    'range_width_atr': price_change_atr,
                    'impulse_strength': abs(price_change),
                    'confidence': 0.90
                }
            
            # 4. CORRECTION: откат после тренда
            # Проверяем, был ли недавно импульс (последние 40 свечей)
            last_40 = df.tail(40) if len(df) >= 40 else df
            max_price = last_40['close'].max()
            min_price = last_40['close'].min()
            
            # Откат после роста
            if close < max_price * 0.98 and kalman_signal > 0 and price_change < 0:
                return {
                    'market_state': 'CORRECTION',
                    'range_width_atr': price_change_atr,
                    'impulse_strength': abs(price_change),
                    'confidence': 0.75
                }
            
            # Откат после падения
            if close > min_price * 1.02 and kalman_signal < 0 and price_change > 0:
                return {
                    'market_state': 'CORRECTION',
                    'range_width_atr': price_change_atr,
                    'impulse_strength': abs(price_change),
                    'confidence': 0.75
                }
            
            # 5. По умолчанию: RANGE (если не уверены)
            return {
                'market_state': 'RANGE',
                'range_width_atr': price_change_atr,
                'impulse_strength': abs(price_change),
                'confidence': 0.60
            }
            
        except Exception as e:
            print(f"⚠️ Ошибка detect_structure: {e}")
            return {
                'market_state': 'RANGE',
                'range_width_atr': 0.0,
                'impulse_strength': 0.0,
                'confidence': 0.5
            }
