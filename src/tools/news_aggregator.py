import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any
from src.config.settings import settings

class UnifiedNewsAgent:
    def __init__(self):
        self.finnhub_token = "ВАШ_FINNHUB_TOKEN" # Или забирайте из settings
        self.newsapi_key = "ВАШ_NEWSAPI_KEY"

    async def fetch_finnhub_news(self) -> List[str]:
        """Получает новости по сектору энергетики/газа через Finnhub."""
        if not self.finnhub_token or "ВАШ" in self.finnhub_token:
            return []
        
        url = f"https://finnhub.io/api/v1/news?category=energy&token={self.finnhub_token}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Берем последние 5 заголовков
                        return [f"Finnhub: {item['headline']}" for item in data[:5]]
        except Exception as e:
            print(f"⚠️ Finnhub error: {e}")
        return []

    async def fetch_newsapi_org(self) -> List[str]:
        """Получает новости по ключевым словам через NewsAPI."""
        if not self.newsapi_key or "ВАШ" in self.newsapi_key:
            return []
            
        today = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        url = (
            f"https://newsapi.org/v2/everything?"
            f"q=Natural+Gas+AND+(LNG+OR+Storage)&"
            f"from={today}&sortBy=publishedAt&language=en&"
            f"apiKey={self.newsapi_key}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [f"NewsAPI: {item['title']}" for item in data.get('articles', [])[:5]]
        except Exception as e:
            print(f"⚠️ NewsAPI error: {e}")
        return []

    async def get_aggregated_sentiment_context(self) -> str:
        """Собирает всё воедино для передачи в Planner/Analyst."""
        print("🌍 Сбор данных из News API...")
        tasks = [self.fetch_finnhub_news(), self.fetch_newsapi_org()]
        results = await asyncio.gather(*tasks)
        
        all_headlines = [item for sublist in results for item in sublist]
        if not all_headlines:
            return "No fresh news from API."
            
        return "\n".join(all_headlines)
