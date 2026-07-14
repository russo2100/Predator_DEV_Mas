import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class StateVectorizer:
    """
    Concatenates price action metrics and numerical sentiment scores into a single 
    fixed-length daily state vector.
    """
    def __init__(self):
        logger.info("Initialized StateVectorizer")

    def vectorize(self, price_action: Dict[str, float], sentiment_scores: Dict[str, float]) -> List[float]:
        """
        Combines price action and sentiment into a unified state vector.
        
        Args:
            price_action: Dictionary of technical indicators (e.g., RSI, ATR, Moving Averages).
            sentiment_scores: Dictionary of LLM-derived sentiment features.
            
        Returns:
            A 1D list/array of floats representing the current market state.
        """
        # TODO: Implement correct ordering and normalization for the vector
        vector = []
        
        # Placeholder logic: just extract values
        for key in sorted(price_action.keys()):
            vector.append(price_action[key])
            
        for key in sorted(sentiment_scores.keys()):
            vector.append(sentiment_scores[key])
            
        return vector
