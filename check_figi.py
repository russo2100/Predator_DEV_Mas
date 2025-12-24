import asyncio
from tinkoff.invest import AsyncClient, CandleInterval
from datetime import datetime, timedelta, timezone

TOKEN = "ТВОЙ_ТИНЬКОФФ_ТОКЕН"
FIGI_DEC = "FUTNGM122500"   # декабрь
FIGI_JAN = "FUTNGM012600"   # январь

async def get_last_price(figi: str, label: str):
    now = datetime.now(timezone.utc)
    frm = now - timedelta(hours=4)

    async with AsyncClient(TOKEN) as client:
        candles = await client.market_data.get_candles(
            figi=figi,
            from_=frm,
            to=now,
            interval=CandleInterval.CANDLE_INTERVAL_1_MIN,
        )
        if not candles.candles:
            print(f"{label}: нет свечей")
            return
        last = candles.candles[-1].close
        print(f"{label}: FIGI={figi}  last={last.units + last.nano/1e9:.3f}")

async def main():
    await get_last_price(FIGI_DEC, "DEC  NRZ5")
    await get_last_price(FIGI_JAN, "JAN  NRF6")

if __name__ == "__main__":
    asyncio.run(main())
