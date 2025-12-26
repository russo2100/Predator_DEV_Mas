import os
import logging
import asyncio
from pathlib import Path

from t_tech.invest import AsyncClient
from src.config.settings import settings
from src.tools.eia_collector import EIACollector
from src.tools.rss_collector import NewsCollector
from src.tools.weather_collector import WeatherCollector

logger = logging.getLogger(__name__)


class UnifiedNewsAgent:
    """
    Объединённый агент для сбора:
    - EIA (запасы газа)
    - RSS новостей
    - Погода (NOAA алерты)
    """

    def __init__(self):
        self.eia = EIACollector()
        self.rss = NewsCollector()
        self.weather = WeatherCollector()

    async def get_aggregated_sentiment_context(self, figi: str = None) -> str:
        """Собирает все данные и форматирует для news.txt"""
        logger.info("🌍 Запуск сбора новостного контекста")

        parts = []

        # 1. EIA Storage
        try:
            storage = self.eia.get_latest_storage()
            if storage:
                parts.append(
                    f"📊 EIA Storage ({storage['date']}): {storage['value']:.0f} Bcf "
                    f"({storage['type']} {storage['change']:+.0f} Bcf)"
                )
            else:
                parts.append("📊 EIA Storage: NO DATA")
        except Exception as e:
            logger.error(f"EIA error: {e}")
            parts.append("📊 EIA Storage: ERROR")

        # 2. RSS News
        try:
            news_summary = self.rss.get_market_sentiment_news()
            parts.append(f"\n📰 Market News:\n{news_summary}")
        except Exception as e:
            logger.error(f"RSS error: {e}")
            parts.append("\n📰 Market News: ERROR")

        # 3. Weather Alerts
        try:
            score, alerts = self.weather.get_arctic_blast_probability()
            if score > 0:
                parts.append(f"\n🌨️ Weather Risk: {score:.1%}")
                for alert in alerts[:3]:
                    parts.append(f"  - {alert}")
            else:
                parts.append("\n🌨️ Weather Risk: No severe alerts")
        except Exception as e:
            logger.error(f"Weather error: {e}")
            parts.append("\n🌨️ Weather Risk: ERROR")

        return "\n".join(parts)

    async def refresh_news_file(self, path: str = "news.txt", figi: str = None) -> str:
        """Перезаписывает news.txt простой записью"""
        text = await self.get_aggregated_sentiment_context(figi=figi)

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())

        logger.info(f"📝 news file updated: {target.resolve()}")
        return text


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
            print(ctx)

    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
