from __future__ import annotations

import re
from typing import Any

_ALLOWED_BIAS = {"bearish", "neutral", "bullish"}

def normalize_bias(raw: Any) -> str:
    """
    Приводит любые входные формы bias/режима к одному из:
    'bearish' | 'neutral' | 'bullish'
    """
    if raw is None:
        return "neutral"

    s = str(raw).strip().lower()
    if not s:
        return "neutral"

    # Частые варианты, которые встречаются в новостях/логике:
    # 1) "BEARISH CONFIRMED", "bearish mode", "bias=bearish"
    if "bearish" in s:
        return "bearish"
    if "bullish" in s:
        return "bullish"
    if "neutral" in s:
        return "neutral"

    # 2) Режимы стратегии (синонимы)
    # SHORT_ONLY -> bearish
    if "short_only" in s or "shortonly" in s or "short" == s:
        return "bearish"

    # LONG_ONLY / LONGONLY -> bullish (если ты так интерпретируешь)
    if "long_only" in s or "longonly" in s or "long" == s:
        return "bullish"

    # 3) Иногда попадает "BIAS: BEARISH" или "BIAS=BEARISH"
    m = re.search(r"\bbias\s*[:=]\s*(bearish|bullish|neutral)\b", s)
    if m:
        return m.group(1)

    # Если прилетело что-то неизвестное — безопасно считаем нейтралом
    return "neutral"
