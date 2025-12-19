import requests
from bs4 import BeautifulSoup
from datetime import datetime

class WeatherCollector:
    def __init__(self):
        self.noaa_api_url = "https://api.weather.gov/alerts/active"
        # Ключевые регионы потребления газа (Midwest, Northeast)
        self.target_zones = ["IL", "OH", "PA", "NY", "MI"] 
        self.headers = {
            "User-Agent": "Predator_DEV_Mas/2.0 (learning_bot; contact@example.com)"
        }

    def get_arctic_blast_probability(self):
        """
        Проверяет наличие экстремальных холодов через NOAA API.
        Возвращает: float (0.0 - 1.0), где 1.0 - подтвержденный Arctic Blast.
        """
        try:
            # Запрашиваем активные алерты погоды по США
            response = requests.get(
                f"{self.noaa_api_url}?status=actual&message_type=alert&severity=severe",
                headers=self.headers,
                timeout=10
            )
            data = response.json()
            
            arctic_score = 0
            alerts_found = []

            if "features" in data:
                for feature in data["features"]:
                    props = feature["properties"]
                    # Фильтруем по ключевым словам холода
                    event = props.get("event", "").lower()
                    headline = props.get("headline", "").lower()
                    
                    cold_keywords = ["freeze", "blizzard", "chill", "winter storm", "cold"]
                    
                    # Проверяем, касается ли это целевых штатов (очень грубая проверка по areaDesc)
                    area = props.get("areaDesc", "")
                    if any(zone in area for zone in self.target_zones):
                        if any(k in event for k in cold_keywords):
                            alerts_found.append(f"{props['event']} in {area}")
                            arctic_score += 0.2  # Накапливаем риск

            # Нормализуем score (максимум 1.0)
            final_score = min(arctic_score, 1.0)
            return final_score, alerts_found

        except Exception as e:
            print(f"[Weather] NOAA API Error: {e}")
            return 0.0, []

    def scrape_natgasweather_sentiment(self):
        """
        Парсит заголовки с NatGasWeather (демо-режим через requests).
        Внимание: Это хрупкий метод (Web Scraping), требует поддержки.
        """
        url = "https://www.natgasweather.com/" # Пример, нужно уточнять актуальный endpoint для новостей
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Ищем заголовки (структура может меняться, здесь примерная логика)
            headlines = []
            for h2 in soup.find_all('h2'):
                text = h2.get_text().strip()
                if text:
                    headlines.append(text)
            
            return headlines[:5] # Возвращаем топ-5 заголовков для анализа LLM
        except Exception as e:
            print(f"[Weather] Scraping Error: {e}")
            return []

# Пример использования
if __name__ == "__main__":
    wc = WeatherCollector()
    score, alerts = wc.get_arctic_blast_probability()
    print(f"Arctic Blast Score: {score}")
    print(f"Active Alerts: {alerts}")
