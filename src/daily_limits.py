"""
Daily Limits Manager - контроль дневных лимитов торговли
Блокирует переторговку и защищает от катастрофических просадок
"""

from dataclasses import dataclass
from datetime import date
from typing import Tuple


@dataclass
class DailyLimitsConfig:
    """Конфигурация дневных лимитов"""
    ENABLED: bool = True
    MAX_TRADES_PER_DAY: int = 15              # Максимум 15 сделок в день
    MAX_DAILY_DRAWDOWN_RUB: float = 100.0     # Стоп на день при -100 RUB
    

class DailyLimitsManager:
    """
    Управление дневными лимитами торговли.
    
    Функционал:
    - Подсчёт количества сделок за день
    - Отслеживание realized PnL за день
    - Блокировка торговли при превышении лимитов
    - Автоматический сброс в полночь
    """
    
    def __init__(self, config: DailyLimitsConfig = None):
        self.config = config or DailyLimitsConfig()
        self.trades_today: int = 0
        self.realized_pnl_today: float = 0.0
        self.current_date: date = date.today()
        
    def reset_if_new_day(self) -> None:
        """Сброс счётчиков в полночь (вызывается каждый цикл)"""
        today = date.today()
        if today != self.current_date:
            print(f"📅 NEW DAY: Сброс лимитов (было {self.trades_today} сделок, PnL {self.realized_pnl_today:+.2f} RUB)")
            self.trades_today = 0
            self.realized_pnl_today = 0.0
            self.current_date = today
            
    def register_trade(self, pnl: float) -> None:
        """
        Регистрация закрытой сделки.
        
        Args:
            pnl: Realized PnL сделки (в рублях)
        """
        self.reset_if_new_day()
        self.trades_today += 1
        self.realized_pnl_today += pnl
        
        print(f"📊 Trade #{self.trades_today} today: PnL {pnl:+.2f} RUB (total today: {self.realized_pnl_today:+.2f} RUB)")
        
    def can_trade(self) -> Tuple[bool, str]:
        """
        Проверка, можно ли открывать новые позиции.
        
        Returns:
            (can_trade, reason): (True/False, текстовое объяснение)
        """
        if not self.config.ENABLED:
            return True, "Daily limits disabled"
        
        self.reset_if_new_day()
        
        # Лимит 1: Максимум сделок в день
        if self.trades_today >= self.config.MAX_TRADES_PER_DAY:
            return False, f"⛔ DAILY LIMIT: {self.trades_today}/{self.config.MAX_TRADES_PER_DAY} trades reached"
        
        # Лимит 2: Максимальная просадка за день
        if self.realized_pnl_today <= -self.config.MAX_DAILY_DRAWDOWN_RUB:
            return False, f"⛔ DAILY DRAWDOWN LIMIT: {self.realized_pnl_today:.2f} RUB <= -{self.config.MAX_DAILY_DRAWDOWN_RUB} RUB"
        
        # Всё OK
        remaining_trades = self.config.MAX_TRADES_PER_DAY - self.trades_today
        return True, f"OK (trades today: {self.trades_today}/{self.config.MAX_TRADES_PER_DAY}, PnL: {self.realized_pnl_today:+.2f} RUB)"
    
    def get_stats(self) -> dict:
        """Получить статистику за сегодня"""
        self.reset_if_new_day()
        return {
            "date": self.current_date.isoformat(),
            "trades_today": self.trades_today,
            "realized_pnl_today": self.realized_pnl_today,
            "trades_remaining": max(0, self.config.MAX_TRADES_PER_DAY - self.trades_today),
            "drawdown_remaining": self.config.MAX_DAILY_DRAWDOWN_RUB + self.realized_pnl_today,
        }
