import os
import logging
import asyncio
import datetime
import tempfile
from pathlib import Path
from typing import Any

from t_tech.invest import AsyncClient
from src.config.settings import settings  # проверь, что так же импортируется в main.py

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

                response: Any = None

                # Пробуем по очереди разные названия метода, которые бывают в разных версиях SDK
                for service in [client.instruments, client.market_data]:
                    for method_name in ["get_news", "get_instrument_news"]:
                        method = getattr(service, method_name, None)
                        if not method:
                            continue
                        try:
                            response = await method(figi=target_figi, from_=from_date, to=to_date)
                            break
                        except Exception:
                            continue
                    if response:
                        break

                if not response or not hasattr(response, "news") or not response.news:
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

    async def refresh_news_file(self, path: str = "news.txt", figi: str = None) -> str:
        """
        Перезаписывает news.txt авто-выжимкой (атомарно через temp + os.replace).
        os.replace используется как безопасная атомарная замена файла. [web:377]
        """
        text = await self.get_aggregated_sentiment_context(figi=figi)

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(target.parent), encoding="utf-8") as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name

        os.replace(tmp_name, target)
        logger.info(f"📝 news file updated: {target.resolve()}")
        return text

    async def get_market_sentiment(self, news_text: str) -> str:
        """
        Заглушка для совместимости. Анализ сентимента делает основной AI-агент.
        """
        if "Ошибка" in news_text or "нет" in news_text:
            return "neutral"
        return "analyzing"


if __name__ == "__main__":
    import argparse

    async def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("--refresh", action="store_true")
        parser.add_argument("--path", default="news.txt")
        parser.add_argument("--figi", default=None)
        args = parser.parse_args()

        agent = UnifiedNewsAgent()

        if args.refresh:


exit

[200~cd /root/Predator_DEV_Mas

cat > src/tools/news_aggregator.py << 'EOF'
import os
import logging
import asyncio
import datetime
import tempfile
from pathlib import Path
from typing import Any

from t_tech.invest import AsyncClient
from src.config.settings import settings  # проверь, что так же импортируется в main.py

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

                response: Any = None

                # Пробуем по очереди разные названия метода, которые бывают в разных версиях SDK
                for service in [client.instruments, client.market_data]:
                    for method_name in ["get_news", "get_instrument_news"]:
                        method = getattr(service, method_name, None)
                        if not method:
                            continue
                        try:
                            response = await method(figi=target_figi, from_=from_date, to=to_date)
                            break
                        except Exception:
                            continue
                    if response:
                        break

                if not response or not hasattr(response, "news") or not response.news:
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

    async def refresh_news_file(self, path: str = "news.txt", figi: str = None) -> str:
        """
        Перезаписывает news.txt авто-выжимкой (атомарно через temp + os.replace).
        os.replace используется как безопасная атомарная замена файла. [web:377]
        """
        text = await self.get_aggregated_sentiment_context(figi=figi)

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(target.parent), encoding="utf-8") as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name

        os.replace(tmp_name, target)
        logger.info(f"📝 news file updated: {target.resolve()}")
        return text

    async def get_market_sentiment(self, news_text: str) -> str:
        """
        Заглушка для совместимости. Анализ сентимента делает основной AI-агент.
        """
        if "Ошибка" in news_text or "нет" in news_text:
            return "neutral"
        return "analyzing"


if __name__ == "__main__":
    import argparse

    async def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("--refresh", action="store_true")
        parser.add_argument("--path", default="news.txt")
        parser.add_argument("--figi", default=None)
        args = parser.parse_args()

        agent = UnifiedNewsAgent()

        if args.refresh:
            await agent.refresh_news_file(path=args.path, figi=args.figi)
        else:
            ctx = await agent.get_aggregated_sentiment_context(figi=args.figi)
            print("\n--- СОБРАННЫЙ КОНТЕКСТ ---")
            print(ctx)

    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
