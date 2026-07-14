import pytest
import numpy as np

from src.ml.llm_parser import LLMSentimentParser
from src.ml.pattern_matcher import PatternMatcher
from src.ml.vectorizer import StateVectorizer
from src.ml.xgboost_model import XGBoostPredictor

def test_llm_parser_init():
    parser = LLMSentimentParser(model_name="test-model")
    assert parser.model_name == "test-model"

def test_llm_parser_parse_news():
    parser = LLMSentimentParser()
    result = parser.parse_news("Some news text")
    assert isinstance(result, dict)
    assert "sentiment_score" in result
    assert "event_category" in result
    assert "bullish_catalyst" in result
    assert "bearish_catalyst" in result
    assert result["sentiment_score"] == 0.0
    assert result["event_category"] == "macro"
    assert result["bullish_catalyst"] is False
    assert result["bearish_catalyst"] is False

def test_pattern_matcher_init():
    matcher = PatternMatcher(history_db_path="dummy_path.db")
    assert matcher.history_db_path == "dummy_path.db"

def test_pattern_matcher_cosine_similarity():
    matcher = PatternMatcher()
    
    # Identical vectors
    v1 = [1.0, 2.0, 3.0]
    v2 = [1.0, 2.0, 3.0]
    sim = matcher._cosine_similarity(v1, v2)
    assert pytest.approx(sim) == 1.0
    
    # Orthogonal vectors
    v3 = [1.0, 0.0]
    v4 = [0.0, 1.0]
    sim_ortho = matcher._cosine_similarity(v3, v4)
    assert pytest.approx(sim_ortho) == 0.0
    
    # Zero vector handling
    v5 = [0.0, 0.0]
    sim_zero = matcher._cosine_similarity(v1, v5)
    assert sim_zero == 0.0

def test_pattern_matcher_find_similar_days():
    matcher = PatternMatcher()
    # Empty DB
    assert matcher.find_similar_days([1.0, 0.0]) == []
    
    # Populate DB
    matcher.history_vectors = [[1.0, 0.0], [0.0, 1.0], [0.8, 0.2]]
    matcher.history_meta = [{"day": 1}, {"day": 2}, {"day": 3}]
    
    matches = matcher.find_similar_days([1.0, 0.0], top_k=2)
    assert len(matches) == 2
    # The highest similarity should be the first vector [1.0, 0.0]
    assert matches[0][1]["day"] == 1
    assert pytest.approx(matches[0][0]) == 1.0
    # The second highest similarity should be [0.8, 0.2]
    assert matches[1][1]["day"] == 3

def test_pattern_matcher_calculate_pattern_score():
    matcher = PatternMatcher()
    score = matcher.calculate_pattern_score([])
    assert score == 0.0
    
    matches = [(1.0, {"day": 1})]
    score2 = matcher.calculate_pattern_score(matches)
    assert score2 == 0.0

def test_vectorizer_vectorize():
    vectorizer = StateVectorizer()
    price_action = {"rsi": 55.0, "atr": 1.5, "ma": 100.0}
    sentiment = {"score_a": 0.5, "score_b": -0.2}
    
    # Expected: keys sorted
    # price_action sorted keys: atr, ma, rsi -> 1.5, 100.0, 55.0
    # sentiment sorted keys: score_a, score_b -> 0.5, -0.2
    # Combined: [1.5, 100.0, 55.0, 0.5, -0.2]
    expected = [1.5, 100.0, 55.0, 0.5, -0.2]
    
    vector = vectorizer.vectorize(price_action, sentiment)
    assert vector == expected

def test_xgboost_predictor_init():
    predictor = XGBoostPredictor(model_path="dummy_model.json")
    assert predictor.model_path == "dummy_model.json"

def test_xgboost_predictor_predict():
    predictor = XGBoostPredictor()
    
    # Test short vector
    short_vec = [1.0] * 10
    result = predictor.predict(short_vec)
    assert "long_probability" in result
    assert "short_probability" in result
    assert result["long_probability"] == 0.5
    assert result["short_probability"] == 0.5
    
    # Test vector > 31 features
    long_vec = [1.0] * 32
    result_long = predictor.predict(long_vec)
    assert result_long["long_probability"] == 0.5
    assert result_long["short_probability"] == 0.5
