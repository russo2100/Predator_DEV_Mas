from __future__ import annotations

from typing import Any, Dict


class RiskAgent:
    """
    Агент риск-менеджмента.

    Совместимость:
    - assess_risk(alpha_signal, market_data) -> для mainloop/решателя
    - assessrisk(agent_state) -> для ShadowAdapter (risk.assessrisk(agent_state))
    - evaluate_trade(alpha_signal, market_data) -> базовый метод

    ВАЖНО: Shadow-лог печатает Risk по ключу "risk_score".
    """

    def __init__(self) -> None:
        # Hard rules (entry filters)
        self.MAX_ATR: float = 0.50
        self.MIN_CONFIDENCE: float = 60.0

        # RSI filters (симметрично для LONG и SHORT)
        self.RSI_OVERBOUGHT: float = 70.0   # блокируем вход в LONG
        self.RSI_OVERSOLD: float = 30.0     # блокируем вход в SHORT

        # Exit policy
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

        atr = agent_state.get("ATR", agent_state.get("atr", 0))
        rsi = agent_state.get("RSI", agent_state.get("rsi", 50))

        # ВАЖНО: в main.py ключи ATRSL/ATRTP (без подчёркивания),
        # а в некоторых местах у тебя встречается ATR_SL/ATR_TP — читаем оба.
        market_data = {
            "ATR": self._to_float(atr, default=0.0),
            "RSI": self._to_float(rsi, default=50.0),
            "ticker": agent_state.get("ticker", "UNKNOWN"),
            "ATRSL": agent_state.get("ATRSL", agent_state.get("ATR_SL")),
            "ATRTP": agent_state.get("ATRTP", agent_state.get("ATR_TP")),
        }

        return self.evaluate_trade(alpha_signal=alpha_signal, market_data=market_data)

    # ---- базовая логика ----
    def evaluate_trade(self, alpha_signal: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        raw_signal = str(alpha_signal.get("signal", "HOLD") or "HOLD").upper().strip()
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

        # 0) HOLD/NOOP/WAIT
        if raw_signal in {"HOLD", "NOOP", "WAIT"}:
            verdict["allowed"] = False
            verdict["reason"] = f"Signal is {raw_signal}"
            verdict["risk_score"] = 5
            return verdict

        # Нормализуем action-сигналы движка:
        # BUY*, SELL* - это ВХОД (long/short) в твоём main.py
        # CLOSE* / EXIT - это ВЫХОД
        if raw_signal.startswith("BUY"):
            side = "LONG"
            intent = "ENTRY"
        elif raw_signal.startswith("SELL"):
            side = "SHORT"
            intent = "ENTRY"
        elif raw_signal.startswith("CLOSE") or raw_signal == "EXIT":
            side = "UNKNOWN"
            intent = "EXIT"
        else:
            side = "UNKNOWN"
            intent = "UNKNOWN"

        # 1) EXIT: по умолчанию разрешаем (снижение риска)
        if intent == "EXIT":
            if self.ALLOW_EXIT_ALWAYS:
                verdict["allowed"] = True
                verdict["reason"] = f"Exit allowed for signal={raw_signal}. {reason}".strip()
                verdict["risk_score"] = 10
                verdict["modified_sl"] = 0.0
                verdict["modified_tp"] = 0.0
                return verdict

            verdict["allowed"] = False
            verdict["reason"] = f"Exit blocked by policy for signal={raw_signal}"
            verdict["risk_score"] = 40
            return verdict

        # 2) ENTRY: строгие фильтры
        if intent == "ENTRY":
            # 2.1 Confidence gate (качество сигнала)
            if confidence < self.MIN_CONFIDENCE:
                verdict["allowed"] = False
                verdict["reason"] = (
                    f"Low AI confidence: {confidence:.1f}% < {self.MIN_CONFIDENCE:.1f}%"
                )
                verdict["risk_score"] = 25
                return verdict

            # 2.2 ATR gate
            if atr > self.MAX_ATR:
                verdict["allowed"] = False
                verdict["reason"] = f"Extreme Volatility! ATR {atr:.4f} > {self.MAX_ATR:.4f}"
                verdict["risk_score"] = 85
                return verdict

            # 2.3 RSI gate (симметрия)
            if side == "LONG" and rsi > self.RSI_OVERBOUGHT:
                verdict["allowed"] = False
                verdict["reason"] = f"RSI Overbought (block LONG): {rsi:.2f} > {self.RSI_OVERBOUGHT:.2f}"
                verdict["risk_score"] = 65
                return verdict

            if side == "SHORT" and rsi < self.RSI_OVERSOLD:
                verdict["allowed"] = False
                verdict["reason"] = f"RSI Oversold (block SHORT): {rsi:.2f} < {self.RSI_OVERSOLD:.2f}"
                verdict["risk_score"] = 65
                return verdict

            verdict["allowed"] = True
            verdict["reason"] = f"All entry risk checks passed ({side})"
            verdict["risk_score"] = 10

            # 2.4 SL/TP (по ATR)
            atr_sl = market_data.get("ATRSL", market_data.get("ATR_SL"))
            atr_tp = market_data.get("ATRTP", market_data.get("ATR_TP"))
            verdict["modified_sl"] = self._to_float(atr_sl, default=atr * 2)
            verdict["modified_tp"] = self._to_float(atr_tp, default=atr * 4)
            return verdict

        # 3) Неизвестный сигнал
        verdict["allowed"] = False
        verdict["reason"] = f"Unknown signal: {raw_signal}"
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
