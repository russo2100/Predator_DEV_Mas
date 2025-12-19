import os
import uuid
import asyncio
from tinkoff.invest import AsyncClient, OrderDirection, OrderType

SANDBOX_TARGET = "sandbox-invest-public-api.tinkoff.ru:443"
FIGI = "FUTNGM122500"
QTY = 1

async def post(client, account_id: str, direction, qty: int):
    r = await client.orders.post_order(
        figi=FIGI,
        quantity=qty,
        direction=direction,
        account_id=account_id,
        order_type=OrderType.ORDER_TYPE_MARKET,
        order_id=str(uuid.uuid4()),
    )
    print("ORDER:", direction, "qty", qty, "=>", r)
    return r

async def main():
    token = os.getenv("TINKOFFTOKEN")
    if not token:
        raise RuntimeError("Set TINKOFFTOKEN env var")

    async with AsyncClient(token, target=SANDBOX_TARGET) as client:
        acc = await client.users.get_accounts()
        account_id = acc.accounts[0].id
        print("account_id:", account_id)

        # OPEN LONG
        await post(client, account_id, OrderDirection.ORDER_DIRECTION_BUY, QTY)

        # CLOSE LONG
        await post(client, account_id, OrderDirection.ORDER_DIRECTION_SELL, QTY)

        # OPEN SHORT
        await post(client, account_id, OrderDirection.ORDER_DIRECTION_SELL, QTY)

asyncio.run(main())
