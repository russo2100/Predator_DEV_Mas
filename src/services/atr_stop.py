"""
ATR Stop-Loss Engine с адаптацией по тренду.
Unified logic для LONG и SHORT позиций.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ATRStopState:
    """Состояние ATR Stop-Loss"""
    entry_price: float
    atr_at_entry: float
    sl_level: float
    p_high_since_entry: float
    p_low_since_entry: float
    direction: str  # "LONG" or "SHORT"
    trend: str  # "UPTREND", "DOWNTREND", "FLAT"


class ATRStopEngine:
    """
    Движок динамического стопа по ATR с адаптацией по тренду.

    Основные принципы:
    - При открытии позиции фиксируем entry_price, ATR_0 и trend
    - SL рассчитывается как: entry ± (ATR * k_sl)
    - k_sl зависит от тренда:
      - LONG + UPTREND: k_sl = 2.0 (шире стоп)
      - SHORT + DOWNTREND: k_sl = 2.0 (шире стоп)
      - FLAT: k_sl = 2.5 (самый широкий стоп, защита от whipsaw)
      - Остальное: k_sl = 1.5 (стандартный стоп)
    - На каждом цикле обновляем экстремумы и trailing stop
    - Стоп двигается только в сторону уменьшения риска
    - При прибыли >= m_be * ATR_0 подтягиваем к безубытку
    """

    def __init__(self, ksl_uptrend: float = 3.0, ksl_other: float = 3.5, ksl_flat: float = 5.0, m_be: float = 1.0):
        """
        Args:
            ksl_uptrend: ATR multiplier for LONG/UPTREND, SHORT/DOWNTREND
            ksl_other: ATR multiplier for other
            ksl_flat: ATR multiplier for FLAT (raised from 2.5 to 3.5 to avoid whipsaw)
            m_be: ATR multiplier for breakeven
        """
        self.ksl_uptrend = ksl_uptrend
        self.ksl_other = ksl_other
        self.ksl_flat = ksl_flat  # ✅ Now configurable, default 3.5
        self.m_be = m_be


    def on_open(self, direction: str, entry_price: float, atr_0: float, trend: str = "FLAT") -> None:
        """
        Вызывается при открытии новой позиции.

        direction: "LONG" или "SHORT"
        entry_price: цена входа
        atr_0: ATR на момент входа
        trend: текущий тренд ("UPTREND", "DOWNTREND", "FLAT")
        """
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"Invalid direction for ATRStopEngine: {direction}")

        if atr_0 <= 0:
            # Защита от мусорного ATR
            atr_0 = 0.015

        # Определить k_sl на основе направления и тренда
        if (direction == "LONG" and trend == "UPTREND") or (direction == "SHORT" and trend == "DOWNTREND"):
            k_sl = self.ksl_uptrend  # 2.0 для позиций по тренду
        elif trend == "FLAT":
            k_sl = self.ksl_flat  # 2.5 для FLAT (wider stop to avoid whipsaw)
        else:
            k_sl = self.ksl_other  # 1.5 для остальных

        # Рассчитать initial SL
        if direction == "LONG":
            sl0 = entry_price - k_sl * atr_0
        else:  # SHORT
            sl0 = entry_price + k_sl * atr_0

        # Минимальный offset (1% от entry)
        min_offset = entry_price * 0.01
        if direction == "LONG":
            if (entry_price - sl0) < min_offset:
                sl0 = entry_price - min_offset
        else:  # SHORT
            if (sl0 - entry_price) < min_offset:
                sl0 = entry_price + min_offset

        self.state = ATRStopState(
            entry_price=entry_price,
            atr_at_entry=atr_0,
            sl_level=sl0,
            p_high_since_entry=entry_price,
            p_low_since_entry=entry_price,
            direction=direction,
            trend=trend,
        )

        # DEBUG вывод
        offset = abs(entry_price - sl0)
        print(
            f"🔧 DEBUG ATR ENGINE: atr={atr_0:.4f}, trend={trend}, k_sl={k_sl:.1f}, "
            f"direction={direction}"
        )
        print(
            f"🎯 ATR Stop инициализирован: entry={entry_price:.4f}, offset={offset:.4f} "
            f"({trend}), SL={sl0:.4f}"
        )

    def on_close(self) -> None:
        """Вызывается при полном закрытии позиции."""
        self.state = None

    def on_update(self, price_t: float, atr_t: float, trend: str = "FLAT") -> None:
        """
        Вызывается на каждом цикле, пока позиция открыта.
        Обновляет p_high/p_low, trailing SL и безубыток.

        price_t: текущая цена
        atr_t: текущий ATR
        trend: текущий тренд (может меняться)
        """
        if self.state is None:
            return

        s = self.state

        if atr_t <= 0:
            atr_t = s.atr_at_entry if s.atr_at_entry > 0 else 0.015

        # Обновление тренда (если изменился)
        if trend != s.trend:
            s.trend = trend

        # Определить k_sl на основе текущего тренда
        if (s.direction == "LONG" and s.trend == "UPTREND") or (s.direction == "SHORT" and s.trend == "DOWNTREND"):
            k_sl = self.ksl_uptrend
        elif s.trend == "FLAT":
            k_sl = self.ksl_flat  # 2.5 для FLAT
        else:
            k_sl = self.ksl_other

        # Обновление экстремумов
        if s.direction == "LONG":
            s.p_high_since_entry = max(s.p_high_since_entry, price_t)
        else:
            s.p_low_since_entry = min(s.p_low_since_entry, price_t)

        # Кандидат на новый стоп (trailing)
        if s.direction == "LONG":
            sl_candidate = s.p_high_since_entry - k_sl * atr_t
            # Только в сторону уменьшения риска (вверх для LONG)
            sl_new = max(s.sl_level, sl_candidate)

            # Безубыток: прибыль >= m_be * ATR_0
            profit = price_t - s.entry_price
            if profit >= self.m_be * s.atr_at_entry:
                sl_new = max(sl_new, s.entry_price)

            # Если SL поднялся, вывести в лог
            if sl_new > s.sl_level:
                profit_pct = (price_t - s.entry_price) / s.entry_price * 100
                print(
                    f"🔼 Trailing Stop LONG поднят: {s.sl_level:.4f} → {sl_new:.4f} "
                    f"(high={s.p_high_since_entry:.4f}, profit={profit_pct:+.2f}%)"
                )

        else:  # SHORT
            sl_candidate = s.p_low_since_entry + k_sl * atr_t
            # Только в сторону уменьшения риска (вниз для SHORT)
            sl_new = min(s.sl_level, sl_candidate)

            # Безубыток
            profit = s.entry_price - price_t
            if profit >= self.m_be * s.atr_at_entry:
                sl_new = min(sl_new, s.entry_price)

            # Если SL снизился, вывести в лог
            if sl_new < s.sl_level:
                profit_pct = (s.entry_price - price_t) / s.entry_price * 100
                print(
                    f"🔽 Trailing Stop SHORT снижен: {s.sl_level:.4f} → {sl_new:.4f} "
                    f"(low={s.p_low_since_entry:.4f}, profit={profit_pct:+.2f}%)"
                )

        s.sl_level = sl_new

    def get_sl(self) -> Optional[float]:
        """Получить текущий уровень SL"""
        return self.state.sl_level if self.state else None

    def get_direction(self) -> Optional[str]:
        """Получить направление позиции"""
        return self.state.direction if self.state else None

    def get_state(self) -> Optional[ATRStopState]:
        """Получить полное состояние"""
        return self.state
