from __future__ import annotations

from typing import Any, Dict, Optional


class RiskAgent:
    """
    Агент риск-менеджмента.

    Совместимость:
    - assess_risk(alpha_signal, market_data) -> для mainloop/решателя
    - assessrisk(agent_state) -> для ShadowAdapter (risk.assessrisk(agent_state))
    - evaluate_trade(alpha_signal, market_data) -> базовый метод

    ВАЖНО: Shadow-лог печатает Risk по ключу "risk_score".
    """

    # Синонимы сигналов, чтобы не зависеть от конкретного нейминга в main.py
    _ENTRY_SIGNALS = {"BUY"}
    _HOLD_SIGNALS = {"HOLD", "NOOP", "WAIT"}
    _EXIT_SIGNALS = {"SELL", "EXIT", "CLOSE", "CLOSEALL", "SELLALL", "SELL1", "SELLHALF"}

    def __init__(self) -> None:
        # Hard rules (entry filters)
        self.MAX_ATR: float = 0.50
        self.MIN_CONFIDENCE: float = 60.0
        self.RSI_OVERBOUGHT: float = 70.0

        # Exit policy
        # Важно: выходы обычно НЕ блокируем (снижение риска).
        # Можно "смягчать" выход (частичный) только если захочешь — пока просто разрешаем.
        self.ALLOW_EXIT_ALWAYS: bool = True

    # ---- алиас под mainloop ----
    def assess_risk(self, alpha_signal: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        return self.evaluate_trade(alpha_signal=alpha_signal, market_data=market_data)

    # ---- алиас под shadow-вызов: risk.assessrisk(agent_state) ----
    def assessrisk(self, agent_state: Dict[str, Any]) -> Dict[str, Any]:
        # Поддержка обоих наборов ключей (на всякий)
        signal = agent_state.get("ai_signal", agent_state.get("aisignal", "HOLD"))
        conf = agent_state.get("ai_confidence", agent_state.get("aiconfidence", 0))
        reason = agent_state.get("ai_reason", agent_state.get("aireason", "Shadow mode default"))

        alpha_signal = {
            "signal": str(signal).upper(),
            "confidence": self._to_float(conf, default=0.0),
            "reason": str(reason or ""),
        }

        # В твоём agent_state ключи сейчас "ATR/RSI" (верхний регистр) в JSONL inputstate,
        # но на всякий читаем и нижний регистр тоже.
        atr = agent_state.get("ATR", agent_state.get("atr", 0))
        rsi = agent_state.get("RSI", agent_state.get("rsi", 50))

        market_data = {
            "ATR": self._to_float(atr, default=0.0),
            "RSI": self._to_float(rsi, default=50.0),
            "ticker": agent_state.get("ticker", "UNKNOWN"),
            "ATR_SL": agent_state.get("ATR_SL"),
            "ATR_TP": agent_state.get("ATR_TP"),
        }

        return self.evaluate_trade(alpha_signal=alpha_signal, market_data=market_data)

    # ---- базовая логика ----
    def evaluate_trade(self, alpha_signal: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        signal_type = str(alpha_signal.get("signal", "HOLD") or "HOLD").upper().strip()
        confidence = self._to_float(alpha_signal.get("confidence", 0), default=0.0)
        reason = str(alpha_signal.get("reason", "") or "")

        atr = self._to_float(market_data.get("ATR", 0), default=0.0)
        rsi = self._to_float(market_data.get("RSI", 50), default=50.0)

        verdict: Dict[str, Any] = {
            "allowed": False,
            "reason": "Initial check",
            "modified_sl": 0.0,
            "modified_tp": 0.0,
            "risk_score": 0,  # критично для ShadowAgents Risk=...
        }

        # 0) HOLD / NOOP
        if signal_type in self._HOLD_SIGNALS:
            verdict["allowed"] = False
            verdict["reason"] = f"Signal is {signal_type}"
            verdict["risk_score"] = 5
            return verdict

        # 1) EXIT / SELL: по умолчанию разрешаем (это снижение риска)
        if signal_type in self._EXIT_SIGNALS:
            if self.ALLOW_EXIT_ALWAYS:
                verdict["allowed"] = True
                verdict["reason"] = f"Exit allowed for signal={signal_type}. {reason}".strip()
                verdict["risk_score"] = 10

                # SL/TP для выхода не навязываем (пусть решатель/исполнитель решает),
                # но оставим нули как явный "не модифицировать".
                verdict["modified_sl"] = 0.0
                verdict["modified_tp"] = 0.0
                return verdict

            # Если когда-нибудь захочешь "блокировать выход" (не рекомендую) — вот ветка:
            verdict["allowed"] = False
            verdict["reason"] = f"Exit blocked by policy for signal={signal_type}"
            verdict["risk_score"] = 40
            return verdict

        # 2) ENTRY / BUY: применяем строгие фильтры
        if signal_type in self._ENTRY_SIGNALS:
            # 2.1 Confidence
            if confidence < self.MIN_CONFIDENCE:
                verdict["allowed"] = False
                verdict["reason"] = f"Low AI confidence: {confidence:.1f}% < {self.MIN_CONFIDENCE:.1f}%"
                verdict["risk_score"] = 25
                return verdict

            # 2.2 ATR
            if atr > self.MAX_ATR:
                verdict["allowed"] = False
                verdict["reason"] = f"Extreme Volatility! ATR {atr:.4f} > {self.MAX_ATR:.4f}"
                verdict["risk_score"] = 85
                return verdict

            # 2.3 RSI (для входа в LONG: если перекуплен — не входим)
            if rsi > self.RSI_OVERBOUGHT:
                verdict["allowed"] = False
                verdict["reason"] = f"RSI Overbought: {rsi:.2f} > {self.RSI_OVERBOUGHT:.2f}"
                verdict["risk_score"] = 65
                return verdict

            verdict["allowed"] = True
            verdict["reason"] = "All entry risk checks passed"
            verdict["risk_score"] = 10

            # 2.4 SL/TP (по ATR)
            verdict["modified_sl"] = self._to_float(market_data.get("ATR_SL"), default=atr * 2)
            verdict["modified_tp"] = self._to_float(market_data.get("ATR_TP"), default=atr * 4)
            return verdict

        # 3) Неизвестный сигнал
        verdict["allowed"] = False
        verdict["reason"] = f"Unknown signal: {signal_type}"
        verdict["risk_score"] = 30
        return verdict

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
