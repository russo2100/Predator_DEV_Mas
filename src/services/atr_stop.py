from dataclasses import dataclass
from typing import Optional


@dataclass
class ATRStopState:
    entry_price: float
    atr_at_entry: float
    sl_level: float
    p_high_since_entry: float
    p_low_since_entry: float
    direction: str  # "LONG" or "SHORT"


class ATRStopEngine:
    """
    Движок динамического стопа по ATR.

    Общая идея:
    - При открытии позиции фиксируем entry_price и ATR_0.
    - На каждом цикле обновляем экстремумы цены (P_high / P_low).
    - Считаем кандидат на новый SL через k_sl * ATR_t.
    - Стоп двигается только в сторону уменьшения риска.
    - При прибыли >= m_be * ATR_0 подтягиваем SL не ниже/выше точки входа (безубыток).
    """

    def __init__(self, k_sl: float = 1.5, m_be: float = 1.0):
        """
        k_sl  – множитель ATR для расчёта стопа.
        m_be  – множитель ATR_0 для перевода стопа в безубыток.
        """
        self.k_sl = k_sl
        self.m_be = m_be
        self.state: Optional[ATRStopState] = None

    def on_open(self, direction: str, entry_price: float, atr_0: float) -> None:
        """
        Вызывается один раз при открытии новой позиции.
        direction: "LONG" или "SHORT"
        """
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"Invalid direction for ATRStopEngine: {direction}")

        if atr_0 <= 0:
            # Защита от мусорного ATR
            atr_0 = 0.01

        if direction == "LONG":
            sl0 = entry_price - self.k_sl * atr_0
        else:  # SHORT
            sl0 = entry_price + self.k_sl * atr_0

        self.state = ATRStopState(
            entry_price=entry_price,
            atr_at_entry=atr_0,
            sl_level=sl0,
            p_high_since_entry=entry_price,
            p_low_since_entry=entry_price,
            direction=direction,
        )

    def on_close(self) -> None:
        """
        Вызывается при полном закрытии позиции.
        """
        self.state = None

    def on_update(self, price_t: float, atr_t: float) -> None:
        """
        Вызывается на каждом цикле, пока позиция открыта.
        Обновляет p_high/p_low, кандидатный SL и безубыток.
        """
        if self.state is None:
            return

        s = self.state

        if atr_t <= 0:
            atr_t = s.atr_at_entry if s.atr_at_entry > 0 else 0.01

        # Обновление экстремумов
        if s.direction == "LONG":
            s.p_high_since_entry = max(s.p_high_since_entry, price_t)
        else:
            s.p_low_since_entry = min(s.p_low_since_entry, price_t)

        # Кандидат на новый стоп
        if s.direction == "LONG":
            sl_candidate = s.p_high_since_entry - self.k_sl * atr_t
            # Только в сторону уменьшения риска
            sl_new = max(s.sl_level, sl_candidate)

            # Безубыток: прибыль >= m_be * ATR_0
            profit = price_t - s.entry_price
            if profit >= self.m_be * s.atr_at_entry:
                sl_new = max(sl_new, s.entry_price)
        else:  # SHORT
            sl_candidate = s.p_low_since_entry + self.k_sl * atr_t
            sl_new = min(s.sl_level, sl_candidate)

            profit = s.entry_price - price_t
            if profit >= self.m_be * s.atr_at_entry:
                sl_new = min(sl_new, s.entry_price)

        s.sl_level = sl_new

    def get_sl(self) -> Optional[float]:
        return self.state.sl_level if self.state else None

    def get_direction(self) -> Optional[str]:
        return self.state.direction if self.state else None

    def get_state(self) -> Optional[ATRStopState]:
        return self.state
