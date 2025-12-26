import logging
from typing import Dict, Any

import httpx


class SynopticMonitor:
    """
    Synoptic monitor (MVP).
    Источник: Open-Meteo (GFS Model), точка Henry Hub (Луизиана).
    """

    def __init__(self) -> None:
        # Координаты Henry Hub (основной хаб природного газа в США, Луизиана).
        # Погода в США (особенно на Северо-Востоке) влияет на спрос на NG,
        # но для MVP используем Henry Hub как простой прокси.
        self.lat: float = 29.95
        self.lon: float = -90.07
        self.hub_name: str = "Henry Hub"
        self.url: str = "https://api.open-meteo.com/v1/forecast"
        self.logger = logging.getLogger(__name__)

    async def get_weather_impact(self) -> Dict[str, Any]:
        """
        Получает данные о температуре и рассчитывает влияние (impact_score).
        Возвращает словарь с флагом экстремума, готовый для использования в main.py.
        """
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "hourly": "temperature_2m",
            "daily": "temperature_2m_max,temperature_2m_min",
            "forecast_days": 3,
            "timezone": "America/Chicago",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.url, params=params)
                response.raise_for_status()
                payload = response.json()

            daily = (payload or {}).get("daily") or {}
            tmins = daily.get("temperature_2m_min") or []

            # "на завтра" — это индекс 1 (если есть хотя бы 2 дня).
            if len(tmins) >= 2:
                min_temp = float(tmins[1])
            elif len(tmins) == 1:
                # fallback: если вернулся только 1 день, берём его.
                min_temp = float(tmins[0])
            else:
                raise ValueError("Open-Meteo: daily.temperature_2m_min is empty")

            # Базовая логика: для NG критичен холод.
            # Ниже 10°C — рост потребления; ниже 0°C — экстремум; выше 25°C — медвежий фактор.
            impact_score = 0.0
            is_extreme = False

            if min_temp < 0.0:
                impact_score = 0.8  # сильный бычий фактор
                is_extreme = True
            elif min_temp < 10.0:
                impact_score = 0.4  # умеренный бычий фактор
            elif min_temp > 25.0:
                impact_score = -0.3  # медвежий фактор

            # Возвращаем и новые, и "legacy" ключи, чтобы ничего не ломалось в других местах.
            return {
                # основное (как у тебя в текущем файле)
                "temp_min": min_temp,
                "impact_score": float(impact_score),
                "is_extreme": bool(is_extreme),
                "source": "Open-Meteo (GFS Model)",
                "hub": self.hub_name,
                # legacy-ключи (на всякий случай)
                "tempmin": min_temp,
                "impactscore": float(impact_score),
                "isextreme": bool(is_extreme),
            }

        except Exception as e:
            self.logger.error(f"Ошибка SynopticMonitor: {e}")
            return {
                "temp_min": 15.0,
                "impact_score": 0.0,
                "is_extreme": False,
                "source": "Open-Meteo (fallback)",
                "hub": self.hub_name,
                "error": str(e),
                # legacy
                "tempmin": 15.0,
                "impactscore": 0.0,
                "isextreme": False,
            }

    def get_weather_context_str(self, data: Dict[str, Any]) -> str:
        """Формирует строку для промпта аналитика."""
        if not isinstance(data, dict):
            return "Метеоданные временно недоступны."

        if "error" in data:
            return "Метеоданные временно недоступны."

        temp_min = data.get("temp_min", data.get("tempmin", None))
        impact_score = data.get("impact_score", data.get("impactscore", 0.0))
        is_extreme = data.get("is_extreme", data.get("isextreme", False))
        hub = data.get("hub", self.hub_name)

        status = "ЭКСТРЕМАЛЬНЫЙ ХОЛОД" if bool(is_extreme) else "Норма"

        try:
            temp_str = f"{float(temp_min):.1f}°C" if temp_min is not None else "n/a"
        except Exception:
            temp_str = "n/a"

        try:
            impact_pct = float(impact_score) * 100.0
        except Exception:
            impact_pct = 0.0

        return (
            f"ПОГОДА ({hub}): Мин. темп: {temp_str}. "
            f"Статус: {status}. Влияние на спрос: {impact_pct:.1f}%"
        )
