# tests/test_time_utils.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from src.main import get_minutes_to_clearing


@patch("src.main.datetime")
def test_get_minutes_to_clearing_before_10am(mock_datetime):
    now = datetime(2023, 10, 5, 9, 0, tzinfo=timezone.utc)  # 12:00 UTC = 15:00 MSK
    mock_datetime.now.return_value = now
    mock_datetime.side_effect = datetime

    result = get_minutes_to_clearing()
    assert result == 60  # 10:00 - 9:00 = 60 минут


@patch("src.main.datetime")
def test_get_minutes_to_clearing_during_evening_clearing(mock_datetime):
    now = datetime(2023, 10, 5, 15, 45, tzinfo=timezone.utc)  # 18:45 MSK
    mock_datetime.now.return_value = now
    mock_datetime.side_effect = datetime

    result = get_minutes_to_clearing()
    assert result == 5  # 18:50 - 18:45 = 5 минут


@patch("src.main.datetime")
def test_get_minutes_to_clearing_after_trading(mock_datetime):
    now = datetime(2023, 10, 5, 20, 55, tzinfo=timezone.utc)  # 23:55 MSK
    mock_datetime.now.return_value = now
    mock_datetime.side_effect = datetime

    result = get_minutes_to_clearing()
    assert result == 999  # Торги закрыты