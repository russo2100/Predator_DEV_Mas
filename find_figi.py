#!/usr/bin/env python3
"""
find_figi.py

Поиск FIGI/uid по тикеру или строке через метод FindInstrument T-Invest API (REST proxy).
Нужен рабочий токен T-Инвест v2 с правами на инструменты.

Примеры:
    python find_figi.py "NGM-1.26"
    python find_figi.py "NRF6"
    python find_figi.py "NGM"
"""

import os
import sys
import json
import requests


BASE_URL = "https://invest-public-api.tinkoff.ru/rest"
PATH = "/tinkoff.public.invest.api.contract.v1.InstrumentsService/FindInstrument"


def load_token() -> str:
    raw = os.getenv("TINKOFF_INVEST_TOKEN")
    if not raw:
        print("ERROR: env TINKOFF_INVEST_TOKEN is not set", file=sys.stderr)
        sys.exit(1)
    token = raw.strip()
    if not token.startswith("t."):
        print("WARNING: token does not look like T-API token (no 't.' prefix)", file=sys.stderr)
    # Важно: тут НИЧЕГО не меняем в самом токене, только trim.
    return token


def find_instrument(query: str) -> None:
    token = load_token()
    url = BASE_URL + PATH

    payload = {
        "query": query,
        # Можно убрать фильтр, если нужно искать вообще везде:
        # "instrumentKind": "INSTRUMENT_TYPE_UNSPECIFIED",
        "instrumentKind": "INSTRUMENT_TYPE_FUTURES",
        "apiTradeAvailableFlag": True,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
    except Exception as e:
        print(f"REQUEST ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"HTTP {resp.status_code}")

    # Показать тело ответа всегда — сейчас это помогает диагностике
    text = resp.text
    if text:
        print("RAW RESPONSE:")
        print(text)

    if resp.status_code != 200:
        # Для 4xx/5xx дальше нет смысла парсить
        return

    try:
        data = resp.json()
    except Exception as e:
        print(f"JSON PARSE ERROR: {e}", file=sys.stderr)
        return

    instruments = data.get("instruments") or data.get("payload", {}).get("instruments", [])
    if not instruments:
        print(f"No instruments found for query='{query}'")
        return

    print(f"\nFound {len(instruments)} instruments for query='{query}':")
    for i, inst in enumerate(instruments, start=1):
        print("-" * 60)
        print(f"#{i}")
        print(f"  name       : {inst.get('name')}")
        print(f"  ticker     : {inst.get('ticker')}")
        print(f"  class_code : {inst.get('classCode')}")
        print(f"  figi       : {inst.get('figi')}")
        print(f"  uid        : {inst.get('uid')}")
        print(f"  lot        : {inst.get('lot')}")
        print(f"  currency   : {inst.get('currency')}")
        print(f"  api_trade  : {inst.get('apiTradeAvailableFlag')}")
        print(f"  exchange   : {inst.get('exchange')}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python find_figi.py <query>", file=sys.stderr)
        sys.exit(1)
    query = sys.argv[1]
    find_instrument(query)


if __name__ == "__main__":
    main()
