# tests/test_main_logic.py
import pytest
from src.main import decide_action


def test_decide_action_no_position_uptrend_buy():
    action = decide_action(
        lots=0,
        avg_entry_price=100.0,
        current_price=99.0,
        atr=1.0,
        ai_signal="BUY",
        ai_confidence=70,
        daily_change_abs=5.0,
        days_to_expiration=10.0,
        trend_5m="UPTREND",
        rsi=40,
    )
    assert action == "BUY_1"


def test_decide_action_no_position_neutral_strong_buy():
    action = decide_action(
        lots=0,
        avg_entry_price=100.0,
        current_price=99.0,
        atr=1.0,
        ai_signal="BUY",
        ai_confidence=80,
        daily_change_abs=5.0,
        days_to_expiration=10.0,
        trend_5m="FLAT",
        rsi=50,
    )
    assert action == "BUY_1"


def test_decide_action_no_action_low_confidence():
    action = decide_action(
        lots=0,
        avg_entry_price=100.0,
        current_price=99.0,
        atr=1.0,
        ai_signal="BUY",
        ai_confidence=50,
        daily_change_abs=5.0,
        days_to_expiration=10.0,
        trend_5m="FLAT",
        rsi=50,
    )
    assert action == "NOOP"


def test_decide_action_sell_on_ai_signal():
    action = decide_action(
        lots=2,
        avg_entry_price=100.0,
        current_price=101.0,
        atr=1.0,
        ai_signal="SELL",
        ai_confidence=75,
        daily_change_abs=5.0,
        days_to_expiration=10.0,
        trend_5m="UPTREND",
        rsi=60,
    )
    assert action == "SELL_ALL"