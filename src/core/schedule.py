from datetime import datetime, time
import pytz  # Библиотека для часовых поясов


class MarketSchedule:
    def __init__(self):
        self.moscow_tz = pytz.timezone('Europe/Moscow')
        # Время работы срочного рынка (Фьючерсы)
        self.start_time = time(9, 0)   # 09:00 МСК
        self.end_time = time(23, 50)   # 23:50 МСК

    def is_market_open(self) -> bool:
        """Проверяет, открыта ли биржа ПРЯМО СЕЙЧАС"""
        now_msk = datetime.now(self.moscow_tz)

        # 1. Проверка дня недели (0=Пн, 6=Вс)
        if now_msk.weekday() >= 5:  # 5=Суббота, 6=Воскресенье
            return False

        # 2. Проверка времени
        current_time = now_msk.time()
        if self.start_time <= current_time <= self.end_time:
            return True

        return False

    def get_next_opening_time(self) -> str:
        """Возвращает строку, когда (примерно) откроется биржа"""
        return "09:00 MSK в будний день"
