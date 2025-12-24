import asyncio
from datetime import datetime
import pytz

from src.config.settings import settings
from src.core.executor import OrderExecutor  # путь поправь, если файл называется иначе

FIGI = "FUTNGM012600"  # январь NRF6

async def main():
    token = settings.TINKOFF_TOKEN.get_secret_value()
    executor = OrderExecutor(token)

    now_msk = datetime.now(pytz.timezone("Europe/Moscow"))
    print(f"MSK now: {now_msk}")

    # 1. Последняя свеча по тому FIGI, который должен быть в боте
    candles = await executor.get_candles(FIGI)
    if candles.empty:
        print("No candles for FIGI:", FIGI)
    else:
        last = float(candles["close"].iloc[-1])
        print(f"Last close for {FIGI}: {last:.3f}")

    # 2. Позиция по этому FIGI
    pos = await executor.get_position_data(FIGI)
    if pos is None:
        print("No position for", FIGI)
    else:
        print(f"Position: lots={pos.lots}, avg_price={pos.average_price}")

if __name__ == "__main__":
    asyncio.run(main())
