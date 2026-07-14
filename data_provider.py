import asyncio
from datetime import timedelta
from typing import List

# Добавлен импорт InstrumentStatus
from tinkoff.invest import AsyncClient, CandleInterval, InstrumentStatus
from tinkoff.invest.utils import now
from pandas import DataFrame

from src.config.settings import settings


class DataProvider:
    def __init__(self, token: str):
        self.token = token

    async def get_candles(self, ticker: str, days_back: int = 1) -> DataFrame:
        print(f"📥 Загрузка данных для {ticker} за {days_back} дн...")

        async with AsyncClient(self.token) as client:
            # ИСПРАВЛЕНО: Используем Enum вместо числа 1
            instruments = await client.instruments.futures(
                instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE
            )

            target_figi = None
            for item in instruments.instruments:
                if item.ticker == ticker:
                    target_figi = item.figi
                    print(f"   🎯 Нашел FIGI для {ticker}: {target_figi}")
                    break

            if not target_figi:
                print(f"❌ Тикер {ticker} не найден!")
                return DataFrame()

            # ... (остальной код без изменений) ...
            candles = []
            async for candle in client.get_all_candles(
                figi=target_figi,
                from_=now() - timedelta(days=days_back),
                interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
            ):
                candles.append({
                    'time': candle.time,
                    'open': candle.open.units + candle.open.nano / 1e9,
                    'high': candle.high.units + candle.high.nano / 1e9,
                    'low': candle.low.units + candle.low.nano / 1e9,
                    'close': candle.close.units + candle.close.nano / 1e9,
                    'volume': candle.volume
                })

            df = DataFrame(candles)
            if not df.empty:
                print(f"✅ Загружено {len(df)} свечей.")
            else:
                print("⚠️ Данных нет (пустой DataFrame). Возможно выходной?")

            return df

# Тест


async def main():
    provider = DataProvider(settings.TINKOFF_TOKEN.get_secret_value())
    # Попробуем SBER для гарантии (он точно есть), т.к. фьючерсы могут истечь
    df = await provider.get_candles(ticker="SBER", days_back=2)

    if not df.empty:
        print("\n📊 Первые 5 строк данных:")
        print(df.head())

if __name__ == "__main__":
    asyncio.run(main())
