from __future__ import annotations
from typing import Any, Dict


class RiskAgent:
    """
    Shadow Agent: Risk Management.
    Оценивает риски для каждого сигнала (alpha_signal).
    """

    def __init__(self) -> None:
        # === ИЗМЕНЕНО: RSI пороги снижены для Range Trading ===
        self.RSI_OVERBOUGHT: float = 65.0  # Было 70.0 → снижено до 65
        self.RSI_OVERSOLD: float = 35.0    # Было 30.0 → повышено до 35
        # ========================================================

        # Hard rules для входа
        self.MAX_ATR: float = 0.50
        self.MIN_CONFIDENCE: float = 60.0

        # Вариант B: разрешить покупку в IMPULSE_UP даже при высоком RSI
        self.ALLOW_IMPULSE_RSI_OVERRIDE: bool = True
        self.IMPULSE_UP_RSI_MAX: float = 85.0
        self.IMPULSE_UP_MIN_CONFIDENCE: float = 72.0
        self.IMPULSE_UP_MAX_ATR: float = 0.70

        # Exit policy
        self.ALLOW_EXIT_ALWAYS: bool = True

    def assess_risk(self, alpha_signal: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Основной метод для main loop"""
        return self.evaluate_trade(alpha_signal=alpha_signal, market_data=market_data)

    def assess_risk_shadow(self, agent_state: Dict[str, Any]) -> Dict[str, Any]:
        """Альтернативный метод для Shadow mode (risk.assess_risk(agent_state))"""
        signal = agent_state.get("ai_signal", agent_state.get("ai_signal", "HOLD"))
        conf = agent_state.get("ai_confidence", agent_state.get("ai_confidence", 0))
        reason = agent_state.get("ai_reason", agent_state.get("ai_reason", "Shadow mode default"))

        alpha_signal = {
            "signal": str(signal).upper(),
            "confidence": self.to_float(conf, default=0.0),
            "reason": str(reason) or "",
        }

        atr = agent_state.get("ATR", agent_state.get("atr", 0))
        rsi = agent_state.get("RSI", agent_state.get("rsi", 50))

        market_data = {
            "ATR": self.to_float(atr, default=0.0),
            "RSI": self.to_float(rsi, default=50.0),
            "ticker": agent_state.get("ticker", "UNKNOWN"),
            "ATR_SL": agent_state.get("ATR_SL", agent_state.get("ATR_SL")),
            "ATR_TP": agent_state.get("ATR_TP", agent_state.get("ATR_TP")),
        }

        return self.evaluate_trade(alpha_signal=alpha_signal, market_data=market_data)

    def evaluate_trade(self, alpha_signal: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Оценка сделки по жёстким правилам"""
        raw_signal = str(alpha_signal.get("signal", "HOLD") or "HOLD").upper().strip()
        confidence = self.to_float(alpha_signal.get("confidence", 0), default=0.0)
        reason = str(alpha_signal.get("reason", "") or "")

        atr = self.to_float(market_data.get("ATR", 0), default=0.0)
        rsi = self.to_float(market_data.get("RSI", 50), default=50.0)
        market_state = str(market_data.get("market_state", market_data.get("market_state", "RANGE")) or "RANGE").upper().strip()

        verdict: Dict[str, Any] = {
            "allowed": False,
            "reason": "Initial check",
            "modified_sl": 0.0,
            "modified_tp": 0.0,
            "risk_score": 0,
        }

        # 0. HOLD/NOOP/WAIT → блокировка
        if raw_signal in ("HOLD", "NOOP", "WAIT"):
            verdict["allowed"] = False
            verdict["reason"] = f"Signal is {raw_signal}"
            verdict["risk_score"] = 5
            return verdict

        # Определение направления
        if raw_signal.startswith("BUY"):
            side = "LONG"
            intent = "ENTRY"
        elif raw_signal.startswith("SELL"):
            side = "SHORT"
            intent = "ENTRY"
        elif raw_signal.startswith("CLOSE") or "EXIT" in raw_signal:
            side = "UNKNOWN"
            intent = "EXIT"
        else:
            side = "UNKNOWN"
            intent = "UNKNOWN"

        # 1. EXIT всегда разрешён
        if intent == "EXIT":
            if self.ALLOW_EXIT_ALWAYS:
                verdict["allowed"] = True
                verdict["reason"] = f"Exit allowed for signal={raw_signal}. {reason.strip()}"
                verdict["risk_score"] = 10
                verdict["modified_sl"] = 0.0
                verdict["modified_tp"] = 0.0
                return verdict
            verdict["allowed"] = False
            verdict["reason"] = f"Exit blocked by policy for signal={raw_signal}"
            verdict["risk_score"] = 40
            return verdict

        # 2. ENTRY проверки
        if intent == "ENTRY":
            # 2.1 Confidence gate
            if confidence < self.MIN_CONFIDENCE:
                verdict["allowed"] = False
                verdict["reason"] = f"Low AI confidence: {confidence:.1f}% < {self.MIN_CONFIDENCE:.1f}%"
                verdict["risk_score"] = 25
                return verdict

            # 2.2 ATR gate
            if atr > self.MAX_ATR:
                verdict["allowed"] = False
                verdict["reason"] = f"Extreme Volatility! ATR={atr:.4f} > {self.MAX_ATR:.4f}"
                verdict["risk_score"] = 85
                return verdict

            # 2.3 RSI gate (ИЗМЕНЕНО: новые пороги 65/35)
            if side == "LONG" and rsi >= self.RSI_OVERBOUGHT:
                # Вариант B: разрешить LONG в IMPULSE_UP даже при RSI > 65
                if (self.ALLOW_IMPULSE_RSI_OVERRIDE and
                    market_state == "IMPULSE_UP" and
                    confidence >= self.IMPULSE_UP_MIN_CONFIDENCE and
                    atr <= self.IMPULSE_UP_MAX_ATR and
                    rsi <= self.IMPULSE_UP_RSI_MAX):
                    verdict["allowed"] = True
                    verdict["reason"] = (
                        f"IMPULSE_UP override: allow LONG despite RSI={rsi:.2f} >= {self.RSI_OVERBOUGHT:.2f} "
                        f"(conf={confidence:.1f}%, atr={atr:.4f})"
                    )
                    verdict["risk_score"] = 20
                    verdict["modified_sl"] = self.to_float(market_data.get("ATR_SL"), default=atr * 1.5)
                    verdict["modified_tp"] = self.to_float(market_data.get("ATR_TP"), default=atr * 3.0)
                    return verdict
                
                verdict["allowed"] = False
                verdict["reason"] = f"RSI Overbought: block LONG (rsi={rsi:.2f} >= {self.RSI_OVERBOUGHT:.2f})"
                verdict["risk_score"] = 65
                return verdict

            if side == "SHORT" and rsi <= self.RSI_OVERSOLD:
                verdict["allowed"] = False
                verdict["reason"] = f"RSI Oversold: block SHORT (rsi={rsi:.2f} <= {self.RSI_OVERSOLD:.2f})"
                verdict["risk_score"] = 65
                return verdict

            # 2.4 Всё ОК
            verdict["allowed"] = True
            verdict["reason"] = f"All entry risk checks passed ({side})"
            verdict["risk_score"] = 10

            atr_sl = market_data.get("ATR_SL", market_data.get("ATR_SL"))
            atr_tp = market_data.get("ATR_TP", market_data.get("ATR_TP"))
            verdict["modified_sl"] = self.to_float(atr_sl, default=atr * 2)
            verdict["modified_tp"] = self.to_float(atr_tp, default=atr * 4)
            return verdict

        # 3. Неизвестный сигнал
        verdict["allowed"] = False
        verdict["reason"] = f"Unknown signal: {raw_signal}"
        verdict["risk_score"] = 30
        return verdict

    @staticmethod
    def to_float(value: Any, default: float) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
