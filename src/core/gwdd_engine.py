import numpy as np
import dataclasses
import os
from typing import Dict, Any, Tuple, Optional
from datetime import datetime

# Данные по населению и доле газа в потреблении
GAS_WEIGHTS = {
    "Louisiana": {"population": 4.65, "gas_share": 0.35},
    "Texas": {"population": 4.5, "gas_share": 0.28},
    "Mississippi": {"population": 2.9, "gas_share": 0.40},
    "Alabama": {"population": 4.9, "gas_share": 0.30},
    "Georgia": {"population": 10.7, "gas_share": 0.25},
    "Florida": {"population": 21.5, "gas_share": 0.20},
    "Arkansas": {"population": 3.0, "gas_share": 0.35},
    "Oklahoma": {"population": 3.9, "gas_share": 0.30}
}

def calculate_population_weight(weights: Dict[str, Dict[str, float]]) -> float:
    """Суммирует взвешенное население по штатам"""
    total_weight = 0.0
    for state, data in weights.items():
        total_weight += data["population"] * data["gas_share"]
    return total_weight

# Импорт EIADataSource
from ..services.eia_source import EIADataSource


@dataclasses.dataclass
class GWDDConfig:
    """Configuration for Gaussian Weight Distribution Dynamics"""
    
    # Gaussian widths for signal components
    sigma_confidence: float = 15.0
    sigma_rsi: float = 20.0
    sigma_prob: float = 0.15
    
    # Global minimum weight to SKIP
    global_min_weight: float = 0.40
    
    # Base thresholds for entry
    min_weight_conservative: float = 0.60
    min_weight_moderate: float = 0.53
    min_weight_aggressive: float = 0.45
    
    # Mode-specific entry thresholds
    risk_mode_adjustments: Dict[str, float] = dataclasses.field(
        default_factory=lambda: {
            "CONSERVATIVE": 0.5,
            "MODERATE": 1.0,
            "AGGRESSIVE": 1.5
        }
    )

class GWDDEngine:
    """Gaussian Weight Distribution Dynamics Engine"""
    
    def __init__(self, config: GWDDConfig | None = None) -> None:
        self.config = config or GWDDConfig()
        self.eia_source = EIADataSource(os.getenv("EIA_API_KEY"))
        
        # GWDD v2: История весов для MA5
        from collections import deque
        self.weight_history: deque = deque(maxlen=30)

    
    @staticmethod
    def gaussian_weight(value: float, ideal: float, sigma: float) -> float:
        """Calculate Gaussian weight based on deviation from ideal value"""
        deviation = abs(value - ideal)
        weight = np.exp(-deviation**2 / (2 * sigma**2))
        return float(np.clip(weight, 0.0, 1.0))
    
    async def calculate_entry_weight(
        self,
        ai_signal: str,
        confidence: int,
        bullish_prob: float,
        bearish_prob: float,
        rsi: float,
        market_state: str = "RANGE",
        risk_mode: str = "MODERATE",
        news_text: str = ""
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calculate final entry weight with all factors including EIA data
        UPDATED: Additive model instead of multiplicative (prevents component collapse)
        """
        
        # 1. Basic weights from existing logic
        
    # HOLD penalty: reduce signal_weight significantly
        if ai_signal == "HOLD":
            signal_weight = 0.3  # Low weight for uncertain signal
        elif ai_signal in ("BUY", "SELL"):
            signal_weight = 1.0  # Full weight for clear signal
        else:
            signal_weight = 0.5  # Medium weight for other signals
        
        conf_weight = self.gaussian_weight(
            confidence, ideal=70, sigma=self.config.sigma_confidence
        )
        
        # Dynamic RSI ideal based on market state
        if ai_signal == "BUY":
            if market_state == "IMPULSE":
                rsi_ideal = 60  # В импульсе допустима перекупленность
            else:
                rsi_ideal = 45  # В обычном рынке - mid-range
        elif ai_signal == "SELL":
            if market_state == "IMPULSE":
                rsi_ideal = 40  # В нисходящем импульсе допустима перепроданность
            else:
                rsi_ideal = 55
        else:
            rsi_ideal = 50

        rsi_weight = self.gaussian_weight(
            rsi, ideal=rsi_ideal, sigma=20  # Увеличен sigma с 10 до 20
        )
        
        # RSI override: protect from extreme values in IMPULSE
        if rsi_weight < 0.3 and market_state == "IMPULSE":
            rsi_weight = 0.6  # Override низкого RSI в импульсе
        
        prob_value = bullish_prob if ai_signal == "BUY" else bearish_prob
        prob_ideal = 0.60 if ai_signal in ("BUY", "SELL") else 0.50
        prob_weight = self.gaussian_weight(
            prob_value, ideal=prob_ideal, sigma=self.config.sigma_prob
        )
        
        # === ADDITIVE MODEL (replaces multiplicative) ===
        
        # Level 1: Core signals (50% weight)
        core_score = 0.5 * (
            0.4 * signal_weight +
            0.3 * conf_weight +
            0.3 * prob_weight
        )
        
        # Level 2: Technical (20% weight)
        tech_score = 0.2 * rsi_weight
        
        # Level 3: Risk mode adjustment (applied to core+tech)
        risk_adjustment = self.config.risk_mode_adjustments.get(risk_mode, 1.0)
        base_score = (core_score + tech_score) * risk_adjustment
        
        # Level 4: Market state bonus
        market_bonus = 0.0
        if market_state == "IMPULSE":
            market_bonus = 0.10  # +10% в импульсе
        elif market_state == "CHOPPY":
            market_bonus = -0.10  # -10% в чоппи
        
        # Level 5: Fundamental factors (30% weight)
        
        # 5a. Seasonal weight
        seasonal_weight = self.get_seasonal_weight()
        seasonal_score = (seasonal_weight - 1.0) * 0.10  # Normalize: 1.2 → +0.02, 0.8 → -0.02
        
        # 5b. Population weight (minor)
        population_weight = calculate_population_weight(GAS_WEIGHTS)
        population_score = (population_weight / 50.0 - 1.0) * 0.05  # Normalize
        
        # 5c. EIA Storage level
        storage_level = await self.eia_source.get_storage_level()
        if storage_level < 0.3:  # Low storage
            storage_score = 0.08
        elif storage_level > 0.7:  # High storage
            storage_score = -0.05
        else:
            storage_score = 0.0
        
        # 5d. EIA Production level
        production_level = await self.eia_source.get_production_level()
        if production_level > 0.7:  # High production
            production_score = -0.05
        elif production_level < 0.3:  # Low production
            production_score = 0.08
        else:
            production_score = 0.0
        
        # 5e. Geopolitical impact (capped at ±0.30)
        geopolitical_impact = self.analyze_geopolitical_impact(news_text)
        
        # Combine all scores
        fundamental_score = seasonal_score + population_score + storage_score + production_score + geopolitical_impact
        fundamental_score = max(-0.20, min(0.30, fundamental_score))  # Cap fundamentals
        
        # === FINAL WEIGHT ===
        total_weight = base_score + market_bonus + fundamental_score
        
        # Global bounds
        total_weight = max(0.0, min(1.0, total_weight))
        
        # HOLD signal cap: limit weight to 0.45 (scout entry only)
        if ai_signal == "HOLD":
            total_weight = min(total_weight, 0.45)
        
        # Breakdown for logging
        breakdown = {
            "signal_weight": signal_weight,
            "confidence_weight": conf_weight,
            "rsi_weight": rsi_weight,
            "probability_weight": prob_weight,
            "core_score": core_score,
            "tech_score": tech_score,
            "risk_adjustment": risk_adjustment,
            "base_score": base_score,
            "market_state": market_state,
            "market_bonus": market_bonus,
            "seasonal_score": seasonal_score,
            "population_score": population_score,
            "storage_score": storage_score,
            "production_score": production_score,
            "geopolitical_impact": geopolitical_impact,
            "fundamental_score": fundamental_score,
            "total_weight": total_weight
        }
        
        return float(total_weight), breakdown

    
    def get_seasonal_weight(self) -> float:
        """Return seasonal weight based on current month"""
        month = datetime.now().month
        if month in [11, 12, 1, 2, 3]:  # Winter - high heating demand
            return 1.2
        elif month in [4, 5, 6, 7, 8, 9, 10]:  # Summer - moderate cooling demand
            return 0.8
        return 1.0
    
    def analyze_geopolitical_impact(self, news_text: str) -> float:
        """
        Analyze geopolitical impact from news text (-1.0 to 1.0)
        FIXED: removed keyword duplicates, added cap, using set for unique matches
        """
        if not news_text:
            return 0.0
        
        # Bullish keywords (demand increase / supply disruption)
        keywords_bullish = {
            "freeze": 0.15,
            "vortex": 0.15,
            "cold snap": 0.15,
            "hurricane": 0.10,
            "shutdown": 0.10,  # FIXED: removed duplicate
            "disruption": 0.10,
            "ukraine": 0.10,
            "pipeline": 0.08,
            "storm": 0.08,
            "strike": 0.08,
            "eia": 0.05,
            "extreme cold": 0.12,
            "record low": 0.12,
            "blackout": 0.10,
            "outage": 0.08,
            "заморозки": 0.15,
            "похолодание": 0.15,
            "ураган": 0.10,
            "перебои": 0.10,
            "украина": 0.10,
            "газопровод": 0.08,
            "шторм": 0.08,
            "забастовка": 0.08,
            "отключение": 0.10,
            "экстремальный холод": 0.12,
        }
        
        # Bearish keywords (demand decrease / supply increase)
        keywords_bearish = {
            "warm": -0.10,
            "production surge": -0.15,
            "export ban": -0.12,
            "oversupply": -0.10,
            "потепление": -0.10,
            "рост добычи": -0.15,
            "запрет экспорта": -0.12,
            "избыток": -0.10,
        }
        
        news_lower = news_text.lower()
        impact = 0.0
        
        # Count UNIQUE matched keywords (no duplicates)
        matched_bullish = set()
        for word in keywords_bullish:
            if word.lower() in news_lower:
                matched_bullish.add(word)
        
        matched_bearish = set()
        for word in keywords_bearish:
            if word.lower() in news_lower:
                matched_bearish.add(word)
        
        # Sum impacts
        for word in matched_bullish:
            impact += keywords_bullish[word]
        
        for word in matched_bearish:
            impact += keywords_bearish[word]
        
        # Cap at ±0.30
        impact = max(-0.30, min(0.30, impact))
        
        return impact

    
    def decide_entry(
    self,
    entry_weight: float,
    risk_mode: str = "MODERATE",
    ai_signal: str = "HOLD",
    rsi: Optional[float] = None,
    market_state: str = "RANGE"  # ✅ ДОБАВЛЕНО
    ) -> Tuple[bool, float, str]:

        """
        Decide whether to enter position based on entry weight
        GWDD v2: Adds MA5 and momentum checks
        """
        # GWDD v2: Добавляем текущий вес в историю
        self.weight_history.append(entry_weight)
        
        # GWDD v2: Расчёт MA5 (скользящая средняя за 5 циклов)
        if len(self.weight_history) >= 5:
            ma5 = sum(list(self.weight_history)[-5:]) / 5  # Только последние 5 значений
            momentum = entry_weight - ma5
        else:
            ma5 = entry_weight
            momentum = 0.0

        
        # HOLD signal - no entry
        #if ai_signal == "HOLD":
        #    return False, float(entry_weight), f"HOLD signal, weight={entry_weight:.2f}"
        
        # GWDD v2: Блокировка при резком падении веса (momentum < -0.05)
        # Для импульсов допускаем более глубокую коррекцию momentum
        
        momentum_threshold = -0.05 if market_state == "IMPULSE" else -0.02
        if momentum < momentum_threshold:
            return False, float(entry_weight), f"⛔ SKIP GWDD momentum collapse, momentum={momentum:.3f} <{momentum_threshold:.2f} (weight={entry_weight:.2f}, MA5={ma5:.2f}, state={market_state})"
                
        
        # GWDD v2: Drift check (30-minute trend fading protection)
        if len(self.weight_history) >= 30:
            weight_30min_ago = self.weight_history[0]  # Самое старое значение в истории
            drift = weight_30min_ago - entry_weight
            
            if drift > 0.07:  # Снижение более 7% за 30 минут
                return False, float(entry_weight), f"⛔ SKIP GWDD drift (trend fading), drift={drift:.3f} >0.07 (30min ago: {weight_30min_ago:.2f}, now: {entry_weight:.2f})"

        
        # Global minimum weight check
        if entry_weight < self.config.global_min_weight:
            return False, float(entry_weight), (
                f"SKIP: global GWDD filter, weight={entry_weight:.2f} "
                f"< {self.config.global_min_weight:.2f}"
            )
        
        # GWDD v2: Альтернативный вход через восходящий тренд MA5
        
        gwdd_v2_pass = entry_weight >= self.config.global_min_weight and momentum >= -0.02
        
        # Mode-specific thresholds
        mode = risk_mode or "MODERATE"
        mode = mode.upper()
        
        if mode == "CONSERVATIVE":
            min_weight = self.config.min_weight_conservative
        elif mode == "AGGRESSIVE":
            min_weight = self.config.min_weight_aggressive
        else:  # MODERATE
            min_weight = self.config.min_weight_moderate
        
        # MODERATE mode RSI check
        if mode == "MODERATE" and rsi is not None:
            if self.config.global_min_weight <= entry_weight < min_weight and rsi >= 90:
                return False, float(entry_weight), (
                    f"SKIP: MODERATE scout blocked by RSI={rsi:.1f} "
                    f">= 90 at weight={entry_weight:.2f}"
                )
        
        # GWDD v2: Финальное решение с учётом MA5/momentum
        if gwdd_v2_pass and entry_weight >= min_weight:
            should_enter = True
            reason = (
                f"ENTER: weight={entry_weight:.2f}, MA5={ma5:.2f}, "
                f"momentum={momentum:.3f}, threshold={min_weight:.2f}, mode={mode}"
            )
        else:
            should_enter = False
            reason = (
                f"SKIP: weight={entry_weight:.2f} < {min_weight:.2f}, "
                f"MA5={ma5:.2f}, momentum={momentum:.3f}, mode={mode}"
            )
        
        return should_enter, float(entry_weight), reason

    
    def get_position_sizing(
        self,
        entry_weight: float,
        max_lots: int = 10,
        risk_mode: str = "MODERATE",
        rsi: Optional[float] = None,
        current_lots: int = 0  # ✅ Добавили текущую позицию
    ) -> int:
        """
        Calculate position size based on entry weight and risk mode.
        Uses scaling: scout entry (3 lots) → add up to max_lots.
        
        Args:
            current_lots: Current position size (0 for new entry)
        """

        # Global minimum weight check
        if entry_weight < self.config.global_min_weight:
            return 0

        mode = (risk_mode or "MODERATE").upper()

        # ==================== CONSERVATIVE MODE ====================
        if mode == "CONSERVATIVE":
            if entry_weight < self.config.min_weight_conservative:  # < 0.60
                return 0  # No entry
            elif entry_weight < 0.65:
                return 3  # Scout entry: 3 lots
            elif entry_weight < 0.75:
                return 6  # Moderate conviction
            elif entry_weight < 0.85:
                return 8  # High conviction
            else:  # >= 0.85
                return 10  # Maximum conviction

        # ==================== AGGRESSIVE MODE ====================
        elif mode == "AGGRESSIVE":
            if entry_weight < self.config.min_weight_aggressive:  # < 0.45
                return 0  # No entry
            elif entry_weight < 0.55:
                return 3  # Scout
            elif entry_weight < 0.70:
                return 6
            elif entry_weight < 0.85:
                return 8
            else:  # >= 0.85
                return 10

        # ==================== MODERATE MODE ====================
        else:  # MODERATE
            if entry_weight < self.config.min_weight_moderate:
                return 0  # No entry
            
            # === SCALING LOGIC: First entry vs Add-on ===
            
            # First entry (scout): max 3-5 lots regardless of GWDD
            if current_lots == 0:
                if entry_weight < 0.60:
                    return 3  # Low confidence scout
                elif entry_weight < 0.75:
                    return 3  # Medium confidence scout
                else:  # >= 0.75
                    return 5  # High confidence scout (still conservative)
            
            # Add-on to existing position: can scale up to max_lots
            else:
                target_lots = 0
                
                if entry_weight < 0.60:
                    target_lots = 3
                elif entry_weight < 0.70:
                    target_lots = 6
                elif entry_weight < 0.80:
                    target_lots = 8
                else:  # >= 0.80
                    target_lots = 10
                
                # RSI safety: reduce target if overbought
                if rsi is not None and rsi > 85:
                    target_lots = min(target_lots, 6)
                
                # Calculate delta (how many lots to add)
                delta = target_lots - current_lots
                
                # Return delta (will be added to current position)
                return max(0, delta)


