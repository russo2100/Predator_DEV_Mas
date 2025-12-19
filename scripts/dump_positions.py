import os
import json
import asyncio
import dataclasses
from enum import Enum
from typing import Any

from tinkoff.invest import AsyncClient


def to_plain(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, (list, tuple)):
        return [to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}

    if dataclasses.is_dataclass(obj):
        return {k: to_plain(v) for k, v in dataclasses.asdict(obj).items()}

    if hasattr(obj, "__dict__"):
        return {k: to_plain(v) for k, v in vars(obj).items() if not k.startswith("_")}

    return str(obj)


def jprint(title: str, payload: Any) -> None:
    print("\n" + "=" * 20 + f" {title} " + "=" * 20)
    print(json.dumps(to_plain(payload), ensure_ascii=False, indent=2))


async def main() -> None:
    token = os.getenv("TINKOFFTOKEN")
    if not token:
        raise RuntimeError("Не найден TINKOFFTOKEN в env. Задай $env:TINKOFFTOKEN и повтори запуск.")

    async with AsyncClient(token, target="sandbox-invest-public-api.tinkoff.ru:443") as client:
        accounts = await client.users.get_accounts()
        jprint("ACCOUNTS", accounts)

        if not getattr(accounts, "accounts", None):
            raise RuntimeError("accounts.accounts пуст — нет доступных счетов.")

        account_id = accounts.accounts[0].id
        print(f"\nSelected account_id = {account_id}")

        positions = await client.operations.get_positions(account_id=account_id)
        jprint("GET_POSITIONS", positions)

        portfolio = await client.operations.get_portfolio(account_id=account_id)
        jprint("GET_PORTFOLIO", portfolio)

        # Удобные выжимки
        jprint("POSITIONS.futures", getattr(positions, "futures", []))
        jprint("POSITIONS.securities", getattr(positions, "securities", []))
        jprint("POSITIONS.money", getattr(positions, "money", []))
        jprint("POSITIONS.options", getattr(positions, "options", []))


if __name__ == "__main__":
    asyncio.run(main())
