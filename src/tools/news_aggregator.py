import os
import logging
import asyncio
import datetime
from typing import List, Dict, Any
from tinkoff.invest import AsyncClient
from src.config.settings import settings # или просто проверь как импортируется в main.py


logger = logging.getLogger(__name__)

class UnifiedNewsAgent:
    """
    Агент для сбора новостей через Tinkoff Invest API.
    Гарантированно работает в РФ и не требует сторонних подписок.
    """
    def __init__(self):
        self.token = settings.TINKOFF_TOKEN  
        # FIGI для фьючерса на газ 
        self.default_figi = "FUTNG1225000" 

    async def get_aggregated_sentiment_context(self, figi: str = None) -> str:
        """
        Собирает последние новости по инструменту и формирует текстовый контекст для AI.
        """
        target_figi = figi or self.default_figi
        logger.info(f"🌍 Запуск сбора новостей Тинькофф для FIGI: {target_figi}")
        
        try:
            async with AsyncClient(self.token) as client:
                to_date = datetime.datetime.now(datetime.timezone.utc)
                from_date = to_date - datetime.timedelta(days=2)
                
                # Пробуем по очереди разные названия метода, которые бывают в разных версиях SDK
                response = None
                for service in [client.instruments, client.market_data]:
                    for method_name in ["get_news", "get_instrument_news"]:
                        method = getattr(service, method_name, None)
                        if method:
                            try:
                                response = await method(
                                    figi=target_figi,
                                    from_=from_date,
                                    to=to_date
                                )
                                break
                            except:
                                continue
                    if response: break

                if not response or not hasattr(response, 'news') or not response.news:
                    logger.warning("📭 Новости по инструменту не найдены через доступные методы.")
                    return "Новостей по инструменту в системе Тинькофф сейчас нет."

                # Формируем компактный текст для AI
                context_parts = []
                for idx, item in enumerate(response.news[:10], 1):
                    timestamp = item.published_at.strftime("%Y-%m-%d %H:%M")
                    context_parts.append(f"{idx}. [{timestamp}] {item.headline}")
                
                return "\n".join(context_parts)

        except Exception as e:
            logger.error(f"❌ Ошибка Tinkoff News API: {e}")
            return f"Ошибка получения новостей: {str(e)}"

    async def get_market_sentiment(self, news_text: str) -> str:
        """
        Заглушка для совместимости. Анализ сентимента теперь делает основной AI-агент.
        """
        if "Ошибка" in news_text or "нет" in news_text:
            return "neutral"
        return "analyzing"

# Тестовый запуск
if __name__ == "__main__":
    async def test():
        agent = UnifiedNewsAgent()
        context = await agent.get_aggregated_sentiment_context()
        print("\n--- СОБРАННЫЙ КОНТЕКСТ ---")
        print(context)

    logging.basicConfig(level=logging.INFO)
    asyncio.run(test())
