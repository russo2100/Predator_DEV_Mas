# Predator ML & Historical Pattern Implementation Plan

This document outlines the step-by-step implementation plan for integrating the XGBoost + LLM hybrid predictive model and the Cosine Similarity historical pattern matching into the Predator v2.0 Trading System, resolving the technical debt outlined in `TECH_DEBT.md`.

## Architectural Overview

The new components will introduce a dedicated Machine Learning pipeline to enhance the existing Bayesian Decision Engine (`GWDD`).

*   **LLM Sentiment Parser:** Processes incoming news via fast, cheap models (Claude Haiku / Gemini Flash) to extract sentiment and macroeconomic bias.
*   **XGBoost Predictive Engine:** Evaluates a 31+ feature vector (technical indicators + LLM sentiment scores) to produce an entry/exit confidence probability.
*   **Cosine Similarity Pattern Matcher:** Finds the top 3-5 historical market days most structurally similar to the current market state (price action + news sentiment) to filter false signals.
*   **Integration:** These models act as advanced inputs to `PlannerAgent` and `gwdd_engine.py`.

---

## Phase 1: Data Collection & Feature Engineering Pipeline
*Objective: Build the dataset and pipeline required to feed both XGBoost and the Pattern Matcher.*

1.  **News & Sentiment Aggregation (`src/services/news_scraper.py`):**
    *   Implement reliable data gathering for Natural Gas news (EIA, Bloomberg, Reuters, etc.).
    *   Maintain a rolling window of news over the last 3, 5, and 10 days for *Sentiment Momentum* calculation.
2.  **Feature Vector Construction (`src/ml/feature_builder.py`):**
    *   Create a pipeline to generate the 31+ feature vector required for XGBoost.
    *   *Features to include:* Kalman filter states, RSI, ATR, Moving Averages (EMA), Volume spikes, order book imbalance, and LLM-derived sentiment scores.
3.  **Historical State Database (`src/ml/history_db.py`):**
    *   Design a lightweight vector store or Pandas-based historical database storing past trading days as normalized vectors (price action features + news context).

## Phase 2: Hybrid Model Integration (LLM + XGBoost)
*Objective: Deploy the scoring and classification models.*

1.  **LLM Parsing Module (`src/ml/llm_parser.py`):**
    *   Update `PlannerAgent` or create a standalone parser to use lightweight LLMs (Claude Haiku / Gemini Flash).
    *   Define strict prompt templates returning JSON: `{ "sentiment_score": [-1 to 1], "event_category": "weather/inventory/macro", "bullish_catalyst": bool, "bearish_catalyst": bool }`.
2.  **XGBoost Model Development (`src/ml/xgboost_model.py`):**
    *   *Offline Training:* Train an XGBoost Classifier on historical data to predict profitable market directions over the next 1-4 hours.
    *   *Inference Engine:* Implement an inference class that takes the 31-feature vector and outputs a probability score (e.g., `0.75 for Long`).

## Phase 3: Historical Pattern Matching (Cosine Similarity)
*Objective: Implement the pattern recognition system to filter false entries.*

1.  **Vectorization (`src/ml/vectorizer.py`):**
    *   Concatenate price action metrics and the numerical sentiment scores into a single fixed-length daily state vector.
2.  **Similarity Engine (`src/ml/pattern_matcher.py`):**
    *   Calculate the Cosine Similarity between the current day's vector and the historical database.
    *   Retrieve the Top 3-5 most similar days.
    *   Analyze the subsequent price action of those historical days (e.g., did they trend up, chop, or crash?).
    *   Output a `Pattern Score` penalty or bonus based on historical outcomes.

## Phase 4: Core Integration (GWDD & Planner Agent)
*Objective: Inject the ML predictions into the actual trading logic.*

1.  **Update `src/core/gwdd_engine.py`:**
    *   Add new weight nodes for the XGBoost probability and the Pattern Matcher score.
    *   Adjust the Bayesian probability calculation to heavily weight the XGBoost output if it aligns with the Kalman Filter trends from `analyst.py`.
2.  **Sentiment Momentum Integration:**
    *   Inject the 3/5/10-day moving average of news sentiment into the `RiskAgent` or `GWDD`.
    *   Rule: If 5-day Sentiment Momentum is deeply negative, block all Long entries despite technical signals.

## Phase 5: Explainability & Alerting
*Objective: Make the bot's decisions transparent and human-readable.*

1.  **Explainability Engine (`src/services/explainability.py`):**
    *   Before trade execution, compile the contributing factors: XGBoost confidence, Top 1 historical match, and the dominant LLM news trigger.
    *   Generate a short summary (e.g., "Long entry: 82% XGBoost confidence. Strong EIA draw + technical breakout. Similar to pattern from 2024-11-12.").
2.  **Telegram Integration:**
    *   Modify `main.py` or the notification service to push this human-readable summary alongside standard execution logs.

---

## Rollout Strategy
*   **Step 1:** Implement Phase 1 & 2 in "shadow mode" (logging predictions without executing trades).
*   **Step 2:** Collect metrics on XGBoost accuracy and Cosine Similarity usefulness.
*   **Step 3:** Gradually increase the GWDD weights for the ML modules.
*   **Step 4:** Fully enable Sentiment Momentum blocks and Explainability alerts.
