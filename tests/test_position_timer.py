# tests/test_position_timer.py
import pytest
from datetime import datetime, timezone, timedelta
from src.main import PositionTimer


class TestPositionTimer:
    def setup_method(self):
        self.timer = PositionTimer()

    def test_initial_state(self):
        assert self.timer.entry_time is None
        assert self.timer.get_holding_hours() == 0.0
        assert not self.timer.is_active()

    def test_start_sets_entry_time(self):
        self.timer.start()
        assert isinstance(self.timer.entry_time, datetime)
        assert self.timer.entry_time.tzinfo == timezone.utc
        assert self.timer.is_active()

    def test_set_entry_time_manually(self):
        test_time = datetime(2023, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.timer.set_entry_time(test_time)
        assert self.timer.entry_time == test_time
        assert self.timer.is_active()

    def test_set_entry_time_converts_naive_to_utc(self):
        naive_time = datetime(2023, 10, 1, 12, 0, 0)
        self.timer.set_entry_time(naive_time)
        assert self.timer.entry_time == naive_time.replace(tzinfo=timezone.utc)

    def test_get_holding_hours_no_entry(self):
        self.timer.entry_time = None
        assert self.timer.get_holding_hours() == 0.0

    def test_get_holding_hours_with_entry(self):
        past_time = datetime.now(timezone.utc) - timedelta(hours=2, minutes=30)
        self.timer.set_entry_time(past_time)
        holding = self.timer.get_holding_hours()
        assert 2.49 < holding < 2.51

    def test_reset_clears_entry_time(self):
        self.timer.start()
        assert self.timer.is_active()
        self.timer.reset()
        assert self.timer.entry_time is None
        assert not self.timer.is_active()