import datetime

class CalendarInjector:
    """
    Инжектирует сезонные веса в байесовский движок GWDD в зависимости от текущего месяца.
    """
    def __init__(self):
        # Месяцы: 1-12. Ключ - месяц, Значение - смещение (bias) для Long/Short
        self.seasonality_map = {
            # Зима (Отопительный сезон) - Высокая волатильность, склонность к шорт-сквизам
            1: {"long_weight": 1.2, "short_weight": 0.8, "volatility_multiplier": 1.5},
            2: {"long_weight": 1.1, "short_weight": 0.9, "volatility_multiplier": 1.5},
            12: {"long_weight": 1.3, "short_weight": 0.7, "volatility_multiplier": 1.5},
            
            # Весна (Межсезонье) - Формирование дна, исторические минимумы
            3: {"long_weight": 1.1, "short_weight": 0.9, "volatility_multiplier": 1.0},
            4: {"long_weight": 1.3, "short_weight": 0.7, "volatility_multiplier": 0.8}, # Часто дно
            5: {"long_weight": 1.0, "short_weight": 1.0, "volatility_multiplier": 0.9},
            
            # Лето (Сезон кондиционирования) - Вторичный пик
            6: {"long_weight": 1.1, "short_weight": 0.9, "volatility_multiplier": 1.1},
            7: {"long_weight": 1.2, "short_weight": 0.8, "volatility_multiplier": 1.2},
            8: {"long_weight": 1.2, "short_weight": 0.8, "volatility_multiplier": 1.2},
            
            # Осень (Межсезонье) - Спад после лета, подготовка к зиме
            9: {"long_weight": 0.8, "short_weight": 1.2, "volatility_multiplier": 1.0}, # Сентябрьская слабость
            10: {"long_weight": 0.9, "short_weight": 1.1, "volatility_multiplier": 1.1},
            11: {"long_weight": 1.1, "short_weight": 0.9, "volatility_multiplier": 1.3}
        }

    def get_current_seasonality_bias(self, current_date=None):
        if current_date is None:
            current_date = datetime.datetime.now()
        
        month = current_date.month
        return self.seasonality_map.get(month, {"long_weight": 1.0, "short_weight": 1.0, "volatility_multiplier": 1.0})

    def adjust_gwdd_scores(self, base_long_score, base_short_score, current_date=None):
        """
        Корректирует базовые оценки GWDD на основе календаря.
        Возвращает: скорректированный_лонг, скорректированный_шорт, множитель_волатильности
        """
        bias = self.get_current_seasonality_bias(current_date)
        adjusted_long = base_long_score * bias["long_weight"]
        adjusted_short = base_short_score * bias["short_weight"]
        return adjusted_long, adjusted_short, bias["volatility_multiplier"]
