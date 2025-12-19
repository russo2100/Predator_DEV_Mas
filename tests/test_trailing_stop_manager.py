# tests/test_trailing_stop_manager.py
import pytest
from datetime import datetime, timezone
from src.main import TrailingStopManager


class TestTrailingStopManager:
    def setup_method(self):
        self.manager = TrailingStopManager(
            entry_price=100.0,
            atr=2.0,
            trend="UPTREND",
        )

    def test_initialization_uptrend(self):
        assert self.manager.entry_price == 100.0
        assert self.manager.atr == 2.0
        assert self.manager.trend == "UPTREND"
        assert self.manager.offset == 2.0 * 1.2  # 2.4
        assert self.manager.trailing_stop == 100.0 - 2.4  # 97.6

    def test_initialization_flat(self):
        manager = TrailingStopManager(entry_price=100.0, atr=2.0, trend="FLAT")
        assert manager.offset == 2.0 * 0.8  # 1.6
        assert manager.trailing_stop == 98.4

    def test_update_price_increases_max_and_trailing_stop(self):
        self.manager.update(101.0, "UPTREND")
        assert self.manager.max_price == 101.0
        assert self.manager.trailing_stop == 101.0 - 2.4  # 98.6

    def test_update_price_below_entry_no_stop(self):
        self.manager.update(103.0, "UPTREND")  # Подняли профит
        result = self.manager.update(98.0, "UPTREND")  # Уронили ниже стопа
        assert result  # Должно сработать

    def test_update_price_triggers_stop_when_profit_above_0_5(self):
        self.manager.update(103.0, "UPTREND")
        result = self.manager.update(98.0, "UPTREND")
        assert result