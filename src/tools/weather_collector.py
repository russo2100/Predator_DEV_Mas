"""
Weather Collector (stub)
Заглушка для WeatherCollector — в будущем можно добавить API погоды
"""

class WeatherCollector:
    """Заглушка для сбора данных о погоде"""
    
    def __init__(self):
        pass
    
    def get_weather(self, location: str = "Houston") -> dict:
        """Возвращает пустые данные о погоде"""
        return {
            "location": location,
            "temperature": None,
            "conditions": "N/A",
            "impact": "neutral"
        }
