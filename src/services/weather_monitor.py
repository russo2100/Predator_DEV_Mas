import httpx
import logging
from datetime import datetime
from typing import Dict, Any, Optional

class SynopticMonitor:
    def __init__(self):
        # Координаты Henry Hub (основной хаб природного газа в США, Луизиана)
        # Именно погода в США (особенно на Северо-Востоке) двигает фьючерс NG
        self.lat = 29.95
        self.lon = -90.07
        self.url = "https://api.open-meteo.com/v1/forecast"
        self.logger = logging.getLogger(__name__)

    async def get_weather_impact(self) -> Dict[str, Any]:
        """
        Получает данные о температуре и рассчитывает аномалию (impact).
        Возвращает коэффициент влияния на цену газа.
        """
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "hourly": "temperature_2m",
            "daily": "temperature_2m_max,temperature_2m_min",
            "forecast_days": 3,
            "timezone": "America/Chicago"
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.url, params=params)
                response.raise_for_status()
                data = response.json()

                # Извлекаем минимальную температуру на завтра (прогноз)
                min_temp = data["daily"]["temperature_2m_min"][1]
                
                # Базовая логика: для NG критичен холод. 
                # Ниже 10°C в Луизиане/Техасе — это уже рост потребления.
                # Ниже 0°C — экстремальное событие.
                
                impact_score = 0.0
                is_extreme = False

                if min_temp < 0:
                    impact_score = 0.8  # Сильный бычий фактор
                    is_extreme = True
                elif min_temp < 10:
                    impact_score = 0.4  # Умеренный бычий фактор
                elif min_temp > 25:
                    impact_score = -0.3 # Медвежий фактор (снижение спроса на отопление)

                return {
                    "temp_min": min_temp,
                    "impact_score": impact_score,
                    "is_extreme": is_extreme,
                    "source": "Open-Meteo (GFS Model)"
                }

        except Exception as e:
            self.logger.error(f"Ошибка SynopticMonitor: {e}")
            return {
                "temp_min": 15.0,
                "impact_score": 0.0,
                "is_extreme": False,
                "error": str(e)
            }

    def get_weather_context_str(self, data: Dict[str, Any]) -> str:
        """Формирует строку для промпта аналитика"""
        if "error" in data:
            return "Метеоданные временно недоступны."
        
        status = "ЭКСТРЕМАЛЬНЫЙ ХОЛОД" if data["is_extreme"] else "Норма"
        return (
            f"ПОГОДА (Henry Hub): Мин. темп: {data['temp_min']}°C. "
            f"Статус: {status}. Влияние на спрос: {data['impact_score']*100}%"
        )
