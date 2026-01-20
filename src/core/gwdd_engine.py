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
    sigma_rsi: float = 10.0
    sigma_prob: float = 0.15
    
    # Global minimum weight to SKIP
    global_min_weight: float = 0.50
    
    # Base thresholds for entry
    min_weight_conservative: float = 0.60
    min_weight_moderate: float = 0.50
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
        """
        
        # 1. Basic weights from existing logic
        signal_weight = 1.0 if ai_signal in ("BUY", "SELL") else 0.3
        
        conf_weight = self.gaussian_weight(
            confidence, ideal=70, sigma=self.config.sigma_confidence
        )
        
        if ai_signal == "BUY":
            rsi_ideal = 30
        elif ai_signal == "SELL":
            rsi_ideal = 70
        else:
            rsi_ideal = 50
            
        rsi_weight = self.gaussian_weight(
            rsi, ideal=rsi_ideal, sigma=self.config.sigma_rsi
        )
        
        prob_value = bullish_prob if ai_signal == "BUY" else bearish_prob
        prob_ideal = 0.60 if ai_signal in ("BUY", "SELL") else 0.50
        prob_weight = self.gaussian_weight(
            prob_value, ideal=prob_ideal, sigma=self.config.sigma_prob
        )
        
        # 2. Base component weight (unchanged)
        components_weight = np.mean([signal_weight, conf_weight, rsi_weight, prob_weight])
        risk_adjustment = self.config.risk_mode_adjustments.get(risk_mode, 1.0)
        total_weight = components_weight * risk_adjustment
        
        # 3. Market state multiplier (unchanged)
        if market_state == "IMPULSE":
            total_weight *= 1.2
        elif market_state == "CHOPPY":
            total_weight *= 0.7
        
        # 4. Population weight (NEW)
        population_weight = calculate_population_weight(GAS_WEIGHTS)
        total_weight *= (population_weight / 50.0)  # Normalize
        
        # 5. Seasonal weight (NEW)
        seasonal_weight = self.get_seasonal_weight()
        total_weight *= seasonal_weight
        
        # 6. EIA data: Storage level (NEW)
        storage_level = await self.eia_source.get_storage_level()
        if storage_level < 0.3:  # Low storage
            total_weight *= 1.3
        elif storage_level > 0.7:  # High storage
            total_weight *= 0.9
        
        # 7. EIA data: Production level (NEW)
        production_level = await self.eia_source.get_production_level()
        if production_level > 0.7:  # High production
            total_weight *= 0.8
        elif production_level < 0.3:  # Low production
            total_weight *= 1.2
        
        # 8. Geopolitical impact from news (NEW)
        geopolitical_impact = self.analyze_geopolitical_impact(news_text)
        if geopolitical_impact > 0:
            total_weight *= (1 + geopolitical_impact)
        elif geopolitical_impact < 0:
            total_weight *= max(0.7, 1 + geopolitical_impact)  # Don't go below 0.7
        
        # 9. Global minimum weight (unchanged)
        total_weight = max(self.config.global_min_weight, total_weight)
        
        # 10. Cap at 1.0
        total_weight = min(1.0, total_weight)
        
        # 11. Breakdown for logging
        breakdown = {
            "signal_weight": signal_weight,
            "confidence_weight": conf_weight,
            "rsi_weight": rsi_weight,
            "probability_weight": prob_weight,
            "risk_adjustment": risk_adjustment,
            "market_state": market_state,
            "population_weight": population_weight / 50.0,
            "seasonal_weight": seasonal_weight,
            "storage_level": storage_level,
            "production_level": production_level,
            "geopolitical_impact": geopolitical_impact,
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
        """Analyze geopolitical impact from news text (-1.0 to +1.0)"""
        if not news_text:
            return 0.0
            
        keywords = {
            # Positive impact (increase demand/supply disruption)
            "cold snap": 0.5,
            "freeze": 0.5,
            "hurricane": 0.4,
            "pipeline": 0.3,
            "shutdown": 0.3,
            "maintenance": 0.2,
            "disruption": 0.4,
            "strike": 0.3,
            "conflict": 0.5,
            "war": 0.5,
            "military": 0.4,
            "terrorism": 0.5,
            "attack": 0.5,
            "explosion": 0.5,
            "accident": 0.4,
            "disaster": 0.5,
            "emergency": 0.4,
            "outage": 0.4,
            "blackout": 0.4,
            "storm": 0.3,
            "heat wave": 0.3,
            "drought": 0.2,
            "flood": 0.2,
            "wildfire": 0.2,
            "earthquake": 0.2,
            "tornado": 0.2,
            "cyclone": 0.2,
            "blizzard": 0.4,
            "snowstorm": 0.4,
            "ice storm": 0.4,
            "extreme cold": 0.5,
            "extreme heat": 0.3,
            "record low": 0.5,
            "record high": 0.3,
            "unseasonable": 0.3,
            "early": 0.2,
            "late": 0.2,
            "delay": 0.2,
            "accelerate": 0.2,
            "advance": 0.2,
            "recovery": 0.2,
            "restoration": 0.2,
            "repair": 0.2,
            "completion": 0.2,
            "start": 0.2,
            "begin": 0.2,
            "launch": 0.2,
            "opening": 0.2,
            "commissioning": 0.2,
            "startup": 0.2,
            "resumption": 0.2,
            
            # Negative impact (decrease demand/increase supply)
            "sanctions": -0.3,
            "export": 0.2,
            "production cut": -0.4,
            "ban": -0.4,
            "quota": -0.3,
            "supply chain": -0.3,
            "logistics": -0.3,
            "transport": -0.3,
            "terminal": -0.2,
            "port": -0.2,
            "export ban": -0.5,
            "import restriction": -0.4,
            "trade war": -0.4,
            "tariff": -0.3,
            "embargo": -0.5,
            "blockade": -0.5,
            "regulation": -0.2,
            "tax": -0.2,
            "strike": -0.3,
            "sanction": -0.3,
            "disruption": -0.4,
            "shutdown": -0.4,
            "maintenance": -0.3
        }
        
        impact = 0.0
        news_lower = news_text.lower()
        
        for word, weight in keywords.items():
            if word in news_lower:
                impact += weight
                
        return max(-1.0, min(1.0, impact))
    
    def decide_entry(
        self,
        entry_weight: float,
        risk_mode: str = "MODERATE",
        ai_signal: str = "HOLD",
        rsi: float | None = None
    ) -> Tuple[bool, float, str]:
        """
        Decide whether to enter position based on entry weight
        Preserves existing logic with added global min weight check
        """
        
        # HOLD signal - no entry
        if ai_signal == "HOLD":
            return False, float(entry_weight), f"HOLD signal, weight={entry_weight:.2f}"
        
        # Global minimum weight check (unchanged)
        if entry_weight < self.config.global_min_weight:
            return False, float(entry_weight), (
                f"SKIP: global GWDD filter, weight={entry_weight:.2f} "
                f"< {self.config.global_min_weight:.2f}"
            )
        
        # Mode-specific thresholds (unchanged)
        mode = risk_mode or "MODERATE".upper()
        
        if mode == "CONSERVATIVE":
            min_weight = self.config.min_weight_conservative
        elif mode == "AGGRESSIVE":
            min_weight = self.config.min_weight_aggressive
        else:  # MODERATE
            min_weight = self.config.min_weight_moderate
        
        # MODERATE mode RSI check (unchanged)
        if mode == "MODERATE" and rsi is not None:
            if self.config.global_min_weight <= entry_weight < min_weight and rsi >= 90:
                return False, float(entry_weight), (
                    f"SKIP: MODERATE scout blocked by RSI={rsi:.1f} "
                    f">= 90 at weight={entry_weight:.2f}"
                )
        
        # Final decision (unchanged)
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
        rsi: float | None = None
    ) -> int:
        """
        Calculate position size based on entry weight and risk mode
        Preserves existing logic with added global min weight check
        """
        
        # Global minimum weight check
        if entry_weight < self.config.global_min_weight:
            return 0
            
        lots = max(1, int(max_lots * 0.25))
        mode = risk_mode or "MODERATE".upper()
        
        # CONSERVATIVE mode
        if mode == "CONSERVATIVE":
            if entry_weight < self.config.min_weight_conservative:
                lots = max(1, int(max_lots * 0.25))
            elif entry_weight >= 0.80:
                lots = 1
            else:
                lots = min(2, max_lots)
                
        # AGGRESSIVE mode
        elif mode == "AGGRESSIVE":
            if entry_weight < self.config.min_weight_aggressive:
                lots = max(1, int(max_lots * 0.25))
            elif entry_weight >= 0.60:
                lots = min(2, max_lots)
            else:
                lots = min(4, max_lots)
                
        # MODERATE mode (unchanged)
        else:  # MODERATE
            if entry_weight < self.config.min_weight_moderate:
                lots = max(1, int(max_lots * 0.25))
            elif entry_weight >= 0.70:
                # RSI >= 85: only scout position
                if rsi is not None and rsi >= 85:
                    lots = max(1, int(max_lots * 0.25))
                else:
                    lots = min(3, max_lots)
            else:
                # 1 lot for moderate scout
                lots = 1
                
        return int(lots)
