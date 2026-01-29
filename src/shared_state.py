from typing import Optional
from dataclasses import dataclass

@dataclass
class SharedTradingState:
    """
    Nightjar Shared Program State для Predator Bot.
    Все агенты читают и пишут в этот объект напрямую (zero-copy).
    """
    # Market Data (updated каждый tick)
    current_price: float = 0.0
    rsi: float = 50.0
    atr: float = 0.0
    atr_sl: float = 0.0
    atr_tp: float = 0.0
    market_state: str = "RANGE"
    
    # Probabilities (from NewsAgent)
    bullish_prob: float = 0.5
    bearish_prob: float = 0.5
    
    # Signal (from NewsAgent)
    ai_signal: str = "HOLD"
    ai_confidence: int = 0
    signal_reason: str = ""
    
    # Strategy (from Planner)
    risk_mode: str = "MODERATE"
    bias: str = "NEUTRAL"
    
    # Positions (managed by ExecutionAgent)
    lots: int = 0
    entry_price: float = 0.0
    unrealized_pnl: float = 0.0
    
    # Risk Verdict (ранее писал RiskAgent, сейчас может быть неиспользовано)
    risk_allowed: bool = False
    risk_reason: str = ""
    risk_score: int = 0
    modified_sl: float = 0.0
    modified_tp: float = 0.0
    
    # GWDD (Gaussian Weight Distribution Dynamics)
    gwdd_weight: float = 0.0  # итоговый вес входа от GWDD
    suggested_lots: int = 0  # размер позиции, предложенный GWDD
    
    # ATR-based dynamic stop state
    atr_at_entry: float = 0.0
    p_high_since_entry: float = 0.0
    p_low_since_entry: float = 0.0
    sl_level: float = 0.0
    position_direction: str = ""  # "LONG" / "SHORT" / ""
    entry_time: Optional[float] = None  # Unix timestamp открытия позиции
    
    # ✅ НОВОЕ: Флаг SLEEPING MARKET (блокировка активных входов)
    sleeping_market: bool = False
