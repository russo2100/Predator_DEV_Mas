import os
import httpx
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

class EIADataSource:
    """Клиент для работы с EIA API (Natural Gas Data)"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.eia.gov/v2/natural-gas/data"
    
    async def get_series_data(self, series_id: str, start_date: str = None, end_date: str = None) -> Optional[Dict[str, Any]]:
        """Получение данных по series_id"""
        now = datetime.now()
        end_d = end_date or now.strftime("%Y-%m-%d")
        start_d = start_date or (now - timedelta(days=180)).strftime("%Y-%m-%d")
        
        params = {
            "api_key": self.api_key,
            "series_id": series_id,
            "data": "value",
            "frequency": "monthly",
            "start": start_d,
            "end": end_d
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.base_url, params=params)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            
            return None
    
    async def get_storage_level(self) -> float:
        """Уровень запасов в хранилищах (0–1)"""
        # Weekly Natural Gas Storage Report
        series_id = "NG.N5010USD2.M"  # Total Working Gas in Underground Storage
        
        data = await self.get_series_data(series_id)
        if not data:
            return 0.5  # default if API fails
        
        try:
            # Получаем последнее значение
            latest_value = float(data["response"]["data"][0]["value"])
            
            # Нормализация: min=1000, max=4000 тыс. млрд куб. футов
            min_storage = 1000.0
            max_storage = 4000.0
            normalized = (latest_value - min_storage) / (max_storage - min_storage)
            
            return max(0.0, min(1.0, normalized))
            
        except (IndexError, KeyError, ValueError):
            return 0.5
    
    async def get_production_level(self) -> float:
        """Уровень добычи газа (0–1)"""
        # Natural Gas Gross Withdrawals
        series_id = "NG.N9050US2.M"
        data = await self.get_series_data(series_id)
        if not data:
            return 0.5  # default if API fails
        
        try:
            latest_value = float(data["response"]["data"][0]["value"])
            
            # Нормализация: min=80, max=100 млрд куб. футов в день
            min_production = 80.0
            max_production = 100.0
            normalized = (latest_value - min_production) / (max_production - min_production)
            
            return max(0.0, min(1.0, normalized))
            
        except (IndexError, KeyError, ValueError):
            return 0.5
