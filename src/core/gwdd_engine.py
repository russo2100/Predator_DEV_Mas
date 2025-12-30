import numpy as np
import dataclasses
from typing import Dict, Any, Tuple


@dataclasses.dataclass
class GWDDConfig:
    # Gaussian widths
    sigma_confidence: float = 15.0
    sigma_rsi: float = 10.0
    sigma_prob: float = 0.15

    # Base thresholds
    global_min_weight: float = 0.50  # ниже почти всегда SKIP во всех режимах

    # Mode-specific entry thresholds (минимальный вес для входа)
    min_weight_conservative: float = 0.65
    min_weight_moderate: float = 0.55
    min_weight_aggressive: float = 0.45

    # Risk-mode multiplier для самого веса (как было)
    risk_mode_adjustments: Dict[str, float] = dataclasses.field(
        default_factory=lambda: {
            "CONSERVATIVE": 0.5,
            "MODERATE": 1.0,
            "AGGRESSIVE": 1.5,
        }
    )


class GWDDEngine:
    def __init__(self, config: GWDDConfig | None = None) -> None:
        self.config = config or GWDDConfig()

    @staticmethod
    def gaussian_weight(value: float, ideal: float, sigma: float) -> float:
        deviation = abs(value - ideal)
        weight = np.exp(- (deviation ** 2) / (2 * sigma ** 2))
        return float(np.clip(weight, 0.0, 1.0))

    def calculate_entry_weight(
        self,
        ai_signal: str,
        confidence: int,
        bullish_prob: float,
        bearish_prob: float,
        rsi: float,
        market_state: str = "RANGE",
        risk_mode: str = "MODERATE",
    ) -> Tuple[float, Dict[str, Any]]:
        signal_weight = 1.0 if ai_signal in ("BUY", "SELL") else 0.3

        conf_weight = self.gaussian_weight(
            confidence,
            ideal=70,
            sigma=self.config.sigma_confidence,
        )

        if ai_signal == "BUY":
            rsi_ideal = 30
        elif ai_signal == "SELL":
            rsi_ideal = 70
        else:
            rsi_ideal = 50

        rsi_weight = self.gaussian_weight(
            rsi,
            ideal=rsi_ideal,
            sigma=self.config.sigma_rsi,
        )

        prob_value = bullish_prob if ai_signal == "BUY" else bearish_prob
        prob_ideal = 0.60 if ai_signal in ("BUY", "SELL") else 0.50

        prob_weight = self.gaussian_weight(
            prob_value,
            ideal=prob_ideal,
            sigma=self.config.sigma_prob,
        )

        components_weight = np.mean(
            [signal_weight, conf_weight, rsi_weight, prob_weight]
        )

        risk_adjustment = self.config.risk_mode_adjustments.get(risk_mode, 1.0)
        total_weight = components_weight * risk_adjustment

        if market_state == "IMPULSE":
            total_weight *= 1.2
        elif market_state == "CHOPPY":
            total_weight *= 0.7

        breakdown = {
            "signal_weight": signal_weight,
            "confidence_weight": conf_weight,
            "rsi_weight": rsi_weight,
            "probability_weight": prob_weight,
            "risk_adjustment": risk_adjustment,
            "market_state": market_state,
            "total_weight": total_weight,
        }
        return float(total_weight), breakdown

    def decide_entry(
        self,
        entry_weight: float,
        risk_mode: str = "MODERATE",
        ai_signal: str = "HOLD",
        rsi: float | None = None,
    ) -> Tuple[bool, float, str]:
        # HOLD никогда не даёт вход
        if ai_signal == "HOLD":
            return False, float(entry_weight), f"HOLD signal, weight={entry_weight:.2f}"

        # Глобальный фильтр: ниже 0.5 почти всегда SKIP
        if entry_weight < self.config.global_min_weight:
            return False, float(entry_weight), (
                f"SKIP: global GWDD filter, weight={entry_weight:.2f} "
                f"< {self.config.global_min_weight:.2f}"
            )

        # Порог по режимам
        mode = (risk_mode or "MODERATE").upper()
        if mode == "CONSERVATIVE":
            min_weight = self.config.min_weight_conservative
        elif mode == "AGGRESSIVE":
            min_weight = self.config.min_weight_aggressive
        else:
            min_weight = self.config.min_weight_moderate

        # Доп. условие для MODERATE: разведка только при RSI < 70
        if mode == "MODERATE" and rsi is not None:
            if self.config.global_min_weight <= entry_weight < min_weight and rsi >= 70:
                return False, float(entry_weight), (
                    f"SKIP: MODERATE scout blocked by RSI={rsi:.1f} "
                    f"(>= 70) at weight={entry_weight:.2f}"
                )

        should_enter = entry_weight >= min_weight

        if should_enter:
            reason = (
                f"ENTER: weight={entry_weight:.2f}, "
                f"threshold={min_weight:.2f}, mode={mode}"
            )
        else:
            reason = (
                f"SKIP: weight={entry_weight:.2f}, "
                f"threshold={min_weight:.2f}, mode={mode}"
            )

        return should_enter, float(entry_weight), reason

    def get_position_sizing(
        self,
        entry_weight: float,
        max_lots: int = 8,
        risk_mode: str = "MODERATE",
        rsi: float | None = None,
    ) -> int:
        """
        Маппинг веса в лоты по твоей логике:
        CONSERVATIVE:
          <0.65  -> 0
          0.65–0.80 -> 1
          >=0.80 -> 2
        MODERATE:
          <0.55 -> 0
          0.55–0.70 -> 1 (и только если RSI < 70, иначе 0)
          >=0.70 -> 2-3
        AGGRESSIVE:
          <0.45 -> 0
          0.45–0.60 -> 1-2
          >=0.60 -> 3-4 (ограничено max_lots)
        """
        mode = (risk_mode or "MODERATE").upper()

        # Базовый глобальный фильтр
        if entry_weight < self.config.global_min_weight:
            return 0

        lots: int

        if mode == "CONSERVATIVE":
            if entry_weight < self.config.min_weight_conservative:
                lots = 0
            elif entry_weight < 0.80:
                lots = 1
            else:
                lots = min(2, max_lots)

        elif mode == "AGGRESSIVE":
            if entry_weight < self.config.min_weight_aggressive:
                lots = 0
            elif entry_weight < 0.60:
                lots = min(2, max_lots)
            else:
                # 3-4 лота, но не превышаем max_lots
                lots = min(4, max_lots)

        else:  # MODERATE
            if entry_weight < self.config.min_weight_moderate:
                lots = 0
            elif entry_weight < 0.70:
                # разведка 1 лотом только если RSI < 70
                if rsi is not None and rsi >= 70:
                    lots = 0
                else:
                    lots = 1
            else:
                # 2-3 лота
                lots = min(3, max_lots)

        return int(lots)
