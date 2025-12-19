import os
import asyncio
from tinkoff.invest import AsyncClient, InstrumentIdType

SANDBOX_TARGET = "sandbox-invest-public-api.tinkoff.ru:443"
FIGI = "FUTNGM122500"

async def main():
    print("START sandbox_check_future")
    token = os.getenv("TINKOFFTOKEN")
    if not token:
        raise RuntimeError("Set TINKOFFTOKEN env var")

    async with AsyncClient(token, target=SANDBOX_TARGET) as client:
        print("CLIENT OK (sandbox)")
        r = await asyncio.wait_for(
            client.instruments.future_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI,
                id=FIGI,
            ),
            timeout=15,
        )
        print("RESPONSE:", r)

    print("DONE")

if __name__ == "__main__":
    asyncio.run(main())
