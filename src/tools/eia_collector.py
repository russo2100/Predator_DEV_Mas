import requests
from typing import Optional, Dict, Any

from src.config.settings import settings


class EIACollector:
    """
    Обёртка над EIA API для получения недельных запасов газа.
    Ключ берётся из settings (pydantic), который уже подхватывает .env.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        # Берём из аргумента или из settings, если не передан явно
        self.api_key: Optional[str] = api_key or getattr(settings, "EIA_API_KEY", None)
        self.base_url: str = "https://api.eia.gov/v2/natural-gas/stor/wkly/data/"

    def get_latest_storage(self) -> Optional[Dict[str, Any]]:
        """
        Получает последние данные по запасам газа (Weekly Working Gas in Underground Storage).
        Возвращает словарь с полями: date, value, change, type, raw_json или None при ошибке.
        """
        if not self.api_key:
            # Можно оставить предупреждение, если хочешь видеть отсутствие ключа:
            # print("[EIA] Warning: No API Key provided in settings.")
            return None

        params = {
            "api_key": self.api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "offset": 0,
            "length": 2,
        }

        try:
            response = requests.get(self.base_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            resp = data.get("response", {})
            records = resp.get("data", [])

            if len(records) >= 2:
                current = records[0]
                previous = records[1]

                current_val = float(current["value"])
                prev_val = float(previous["value"])
                diff = current_val - prev_val
                report_type = "Injection" if diff > 0 else "Draw"

                return {
                    "date": current["period"],
                    "value": current_val,
                    "change": diff,
                    "type": report_type,
                    "raw_json": current,
                }

            # Недостаточно данных — просто возвращаем None без принтов
            return None

        except Exception:
            # Тихий фейл: при 403/сетевых ошибках возвращаем None, а верхний код сам
            # поставит storage_context = "EIA: NO DATA"
            return None
