import uuid
import asyncio
from typing import Dict, Union
from t_tech.invest import AsyncClient, OrderDirection, OrderType
from src.config.settings import settings


class MainOrderExecutor:
    def __init__(self, token: str):
        self.token = token
        self.account_id = None

    async def _get_account_id(self, client):
        if self.account_id:
            return self.account_id

        if settings.SANDBOX_MODE:
            accounts = await client.sandbox.get_sandbox_accounts()
        else:
            accounts = await client.users.get_accounts()

        if not accounts.accounts:
            raise ValueError("❌ Нет доступных счетов!")

        self.account_id = accounts.accounts[0].id
        return self.account_id

    async def get_position_data(self, figi: str) -> Dict[str, Union[int, float]]:
        """
        Возвращает полную информацию о позиции:
        {
            "lots": int (количество, +Long, -Short),
            "average_price": float (средняя цена входа),
            "current_price": float (текущая цена биржи),
            "expected_yield": float (текущий профит в валюте)
        }
        """
        try:
            async with AsyncClient(self.token) as client:
                account_id = await self._get_account_id(client)

                if settings.SANDBOX_MODE:
                    portfolio = await client.sandbox.get_sandbox_portfolio(account_id=account_id)
                else:
                    portfolio = await client.operations.get_portfolio(account_id=account_id)

                for p in portfolio.positions:
                    if p.figi == figi:
                        # Извлекаем данные
                        lots = int(p.quantity.units)

                        # Цена входа (средняя)
                        # В API v2 цена может быть в единицах и нано
                        avg_price = self._money_to_float(
                            p.average_position_price)
                        curr_price = self._money_to_float(p.current_price)
                        yield_val = self._money_to_float(p.expected_yield)

                        return {
                            "lots": lots,
                            "average_price": avg_price,
                            "current_price": curr_price,
                            "expected_yield": yield_val
                        }

                # Если позиции нет
                return {"lots": 0, "average_price": 0.0, "current_price": 0.0, "expected_yield": 0.0}

        except Exception as e:
            print(f"⚠️ Ошибка чтения портфеля: {e}")
            return {"lots": 0, "average_price": 0.0, "current_price": 0.0, "expected_yield": 0.0}

    def _money_to_float(self, money_value) -> float:
        """Конвертирует MoneyValue/Quotation в float"""
        if money_value is None:
            return 0.0
        units = money_value.units
        nano = money_value.nano
        return units + nano / 1e9

    async def place_order(self, figi: str, direction: str, quantity: int = 1):
        """Выставляет РЫНОЧНУЮ заявку"""
        if quantity <= 0:
            print(f"⚠️ Ошибка: попытка выставить ордер с quantity={quantity}")
            return None

        for attempt in range(3):
            try:
                async with AsyncClient(self.token) as client:
                    account_id = await asyncio.wait_for(self._get_account_id(client), timeout=5.0)

                    tinkoff_direction = (
                        OrderDirection.ORDER_DIRECTION_BUY if direction == 'BUY'
                        else OrderDirection.ORDER_DIRECTION_SELL
                    )

                    print(f"💸 Выставляю ордер {direction} на {quantity} лот(ов)... (попытка {attempt+1}/3)")

                    post_method = client.sandbox.post_sandbox_order if settings.SANDBOX_MODE else client.orders.post_order

                    order = await asyncio.wait_for(post_method(
                        figi=figi,
                        quantity=quantity,
                        price=None,
                        direction=tinkoff_direction,
                        account_id=account_id,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                        order_id=str(uuid.uuid4())
                    ), timeout=10.0)
                    print(f"✅ Ордер исполнен. ID: {order.order_id}")
                    return order
            except asyncio.TimeoutError:
                print(f"⚠️ Timeout при выставлении ордера (попытка {attempt+1}/3)")
            except Exception as e:
                print(f"⚠️ Ошибка при выставлении ордера: {e}")
            await asyncio.sleep(2)
        print("❌ Не удалось выставить ордер после 3 попыток")
        return None
