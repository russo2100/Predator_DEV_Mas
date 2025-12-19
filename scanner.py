import asyncio
from datetime import datetime, timedelta, timezone
from tinkoff.invest import AsyncClient, CandleInterval
from src.config.settings import settings

# ПАРАМЕТРЫ ПОИСКА
# Макс. полная цена контракта (руб). ГО обычно 20-40% от этого.
MAX_CONTRACT_PRICE = 15000
MIN_DAYS_TO_EXPIRY = 3      # Исключаем экспирацию в ближайшие дни
TOP_N_RESULTS = 10          # Сколько показать


async def main():
    token = settings.TINKOFF_TOKEN.get_secret_value()
    print(f"🚀 ЗАПУСК СКАНЕРА ЛИКВИДНОСТИ (Depo < 3000 RUB)...")
    print(
        f"Параметры: Price < {MAX_CONTRACT_PRICE} RUB, Expiry > {MIN_DAYS_TO_EXPIRY} days\n")

    async with AsyncClient(token) as client:
        # 1. Получаем все фьючерсы
        instruments = await client.instruments.futures()
        now = datetime.now(timezone.utc)
        candidates = []

        print(
            f"📥 Загружено {len(instruments.instruments)} фьючерсов. Фильтрация...")

        for item in instruments.instruments:
            # Фильтр 1: Экспирация
            if item.expiration_date <= now + timedelta(days=MIN_DAYS_TO_EXPIRY):
                continue

            # Фильтр 2: Отсекаем явный мусор по названию
            if "S&P" in item.name or "Nasd" in item.name:
                continue

            candidates.append(item)

        # 2. Получаем цены (batch request)
        # Тинькофф разрешает запрашивать цены пачками, но для простоты и объема запросим в цикле порциями
        # Или лучше: отфильтруем сначала по ГО/Цене, если бы знали. Но мы не знаем цену.
        # Придется запрашивать цены. Чтобы не спамить, возьмем первые 100 "популярных" префиксов или просто переберем.

        # Оптимизация: Запросим цены пакетами по 100 шт
        figi_chunks = [candidates[i:i + 100]
                       for i in range(0, len(candidates), 100)]
        valid_futures = []

        print(f"🔍 Проверка цен для {len(candidates)} кандидатов...")

        for chunk in figi_chunks:
            try:
                figis = [x.figi for x in chunk]
                prices = await client.market_data.get_last_prices(figi=figis)

                for price_obj in prices.last_prices:
                    # Находим инструмент
                    instr = next(
                        (x for x in chunk if x.figi == price_obj.figi), None)
                    if not instr:
                        continue

                    price_pts = price_obj.price.units + price_obj.price.nano / 1e9

                    # Расчет стоимости контракта в рублях
                    # Cost = Price_Points * (Min_Step_Price / Min_Step)
                    if instr.min_price_increment.units == 0 and instr.min_price_increment.nano == 0:
                        continue

                    step_size = instr.min_price_increment.units + \
                        instr.min_price_increment.nano / 1e9
                    step_val = instr.min_price_increment_amount.units + \
                        instr.min_price_increment_amount.nano / 1e9

                    if step_size == 0:
                        continue
                    cost_of_point = step_val / step_size
                    full_price = price_pts * cost_of_point

                    # Фильтр 3: Цена
                    if full_price <= MAX_CONTRACT_PRICE and full_price > 0:
                        valid_futures.append({
                            "name": instr.name,
                            "ticker": instr.ticker,
                            "figi": instr.figi,
                            "full_price": full_price,
                            # Грубая оценка ГО (25%)
                            "go_estimate": full_price * 0.25,
                            "expiry": instr.expiration_date.strftime("%Y-%m-%d")
                        })
            except Exception as e:
                print(f"⚠️ Ошибка пакета: {e}")

        print(
            f"✅ Найдено {len(valid_futures)} доступных по цене. Проверка ликвидности (объем)...")

        # 3. Проверка объема (запрашиваем свечи за сегодня)
        # Берем топ по цене (или всех) и качаем свечи
        final_list = []

        # Ограничимся проверкой топ-30 самых "похожих на правду" или всех, если их мало
        for fut in valid_futures:
            try:
                # Качаем 1 день минуток
                candles = await client.market_data.get_candles(
                    figi=fut['figi'],
                    from_=now - timedelta(days=1),
                    to=now,
                    interval=CandleInterval.CANDLE_INTERVAL_1_MIN
                )

                volume = sum([c.volume for c in candles.candles])
                count = len(candles.candles)

                if count > 50:  # Хоть какая-то жизнь
                    fut["volume"] = volume
                    fut["candles"] = count
                    final_list.append(fut)
                    print(f"   Found: {fut['ticker']} Vol: {volume}")
            except:
                pass

        # 4. Сортировка и вывод
        final_list.sort(key=lambda x: x["volume"], reverse=True)

        print(f"\n🏆 ТОП ЛИКВИДНЫХ И ДОСТУПНЫХ ФЬЮЧЕРСОВ (ГО < 3000 RUB):\n")
        print(f"{'TICKER':<10} | {'NAME':<30} | {'FULL PRICE':<10} | {'~EST GO':<10} | {'VOL (24h)':<10} | {'EXPIRY':<10}")
        print("-" * 90)

        for item in final_list[:TOP_N_RESULTS]:
            print(f"{item['ticker']:<10} | {item['name'][:30]:<30} | {item['full_price']:<10.0f} | {item['go_estimate']:<10.0f} | {item['volume']:<10} | {item['expiry']}")

if __name__ == "__main__":
    asyncio.run(main())
