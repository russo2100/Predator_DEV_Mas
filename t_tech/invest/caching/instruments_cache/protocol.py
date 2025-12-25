from typing import Protocol

from t_tech.invest import InstrumentStatus
from t_tech.invest.caching.instruments_cache.models import InstrumentsResponse


class InstrumentsResponseCallable(Protocol):
    def __call__(
        self, *, instrument_status: InstrumentStatus = InstrumentStatus(0)
    ) -> InstrumentsResponse:
        ...

    def __name__(self) -> str:
        ...
