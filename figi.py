import asyncio
from tinkoff.invest import AsyncClient

async def get_figi():
    TOKEN = 't.vfzS0V4mmmxVztZcVjFEsK6qrffeFQjzku2hnY-IchuSpALB4CPTc9JTPY3p4spimJMwigr-BVdGMwgYTOEtLA'
    TICKER = 'NRF6'
    
    async with AsyncClient(TOKEN) as client:
        # Получаем список всех фьючерсов
        instruments = await client.instruments.futures()
        
        # Ищем нужный тикер
        target = next((i for i in instruments.instruments if i.ticker == TICKER), None)
        
        if target:
            print(f"✅ Нашел инструмент: {target.name}")
            print(f"📌 Ticker: {target.ticker}")
            print(f"🔑 FIGI: {target.figi}")
        else:
            print(f"❌ Тикер {TICKER} не найден. Проверь правильность написания.")

if __name__ == "__main__":
    asyncio.run(get_figi())
