"""
Sleeping Market Detection Module
Детектор "спящего" рынка для блокировки новых входов при низкой волатильности
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class SleepingMarketInput:
    """Входные данные для детектора спящего рынка"""
    atr: float
    atr_threshold: float
    trend_ltf: str  # "UPTREND", "DOWNTREND", "FLAT"
    trend_htf: str  # "UPTREND", "DOWNTREND", "NEUTRAL", "FLAT"
    price_high: float
    price_low: float
    price_current: float
    rsi: float


@dataclass
class SleepingMarketResult:
    """Результат детекции спящего рынка"""
    is_sleeping: bool
    reason: str


class SleepingMarketDetector:
    """
    Детектор спящего рынка.
    
    Блокирует новые активные входы при:
    - Флэт на LTF
    - ATR ниже порога
    - Узкий дневной диапазон
    """
    
    def __init__(
        self,
        atr_threshold: float = 0.020,
        daily_range_threshold_pct: float = 0.30,
        rsi_neutral_min: float = 45.0,
        rsi_neutral_max: float = 55.0
    ):
        self.atr_threshold = atr_threshold
        self.daily_range_threshold_pct = daily_range_threshold_pct
        self.rsi_neutral_min = rsi_neutral_min
        self.rsi_neutral_max = rsi_neutral_max
    
    def detect(self, input_data: SleepingMarketInput) -> SleepingMarketResult:
        """
        Проверяет условия спящего рынка
        
        Args:
            input_data: Данные о текущем состоянии рынка
            
        Returns:
            SleepingMarketResult с флагом is_sleeping и причиной
        """
        reasons = []
        
        # Условие 1: Флэт на LTF
        if input_data.trend_ltf != "FLAT":
            return SleepingMarketResult(
                is_sleeping=False,
                reason=f"Market active: Trend LTF = {input_data.trend_ltf}"
            )
        
        # Условие 2: Низкий ATR
        if input_data.atr >= self.atr_threshold:
            return SleepingMarketResult(
                is_sleeping=False,
                reason=f"Market active: ATR = {input_data.atr:.4f} >= {self.atr_threshold}"
            )
        
        # Условие 3: Узкий дневной диапазон
        daily_range = input_data.price_high - input_data.price_low
        atr_range_threshold = 3 * input_data.atr
        
        if daily_range >= atr_range_threshold:
            return SleepingMarketResult(
                is_sleeping=False,
                reason=f"Market active: Range = {daily_range:.4f} >= {atr_range_threshold:.4f} (3×ATR)"
            )
        
        # ВСЕ УСЛОВИЯ ВЫПОЛНЕНЫ → Рынок спит
        return SleepingMarketResult(
            is_sleeping=True,
            reason=(
                f"💤 SLEEPING MARKET: FLAT + "
                f"ATR={input_data.atr:.4f} < {self.atr_threshold} + "
                f"Range={daily_range:.4f} < {atr_range_threshold:.4f}"
            )
        )
