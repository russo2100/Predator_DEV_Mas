import json
import re
from typing import Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config.settings import settings


class AnalystResult:
    """Структурированный результат анализа"""
    def __init__(self, signal: str, reason: str, confidence: int):
        self.signal = signal.upper()
        self.reason = reason
        self.confidence = min(100, max(0, confidence))  # Зажимаем 0-100
    
    def __repr__(self):
        return f"AnalystResult(signal={self.signal}, conf={self.confidence}%, reason='{self.reason[:50]}...')"


class MarketAnalyst:
    """
    Профессиональный анализатор рынка с поддержкой BIAS режимов
    """
    
    def __init__(self):
        """Инициализация LLM с параметрами"""
        self.llm = ChatOpenAI(
            model=settings.AI_MODEL_ANALYST,
            temperature=0.3,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
    
    def _clean_json_string(self, text: str) -> str:
        """
        ✅ Безопасное извлечение JSON из текста
        """
        if not text:
            return ""
        
        text = text.strip()
        
        # Ищем первую открывающую и последнюю закрывающую скобку
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0)
        
        return text
    
    def _get_adaptive_template(self, bias: str) -> str:
        """
        ✅ АДАПТИВНЫЙ ШАБЛОН в зависимости от BIAS
        
        BEARISH = только SELL и HOLD (BUY запрещены)
        NEUTRAL = BUY, SELL, HOLD (все разрешены)
        """
        
        bias_lower = bias.lower() if bias else "neutral"
        
        if bias_lower == "bearish":
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 🔴 МЕДВЕЖИЙ РЕЖИМ (SHORT_ONLY)
            return """\
SYSTEM: Ты профессиональный алготрейдер. РЕЖИМ: МЕДВЕЖИЙ (SHORT_ONLY).

⚠️ КРИТИЧНО: В медвежьем режиме разрешены ТОЛЬКО SELL и HOLD. BUY ПОЛНОСТЬЮ ЗАПРЕЩЕНЫ!

КОНТЕКСТ:
Тикер: {ticker} | Цена: {price}

ТРЕНД (KALMAN): {kalman_trend} (Цена vs Фильтр: {kalman_price})
RSI: {rsi} | SMA50: {sma_50} | BB Width: {bb_width}

Новости: {news}

ПРАВИЛА СТРАТЕГИИ (SHORT_ONLY):
1. ТРЕНД (KALMAN) == DOWN -> Приоритет SELL
2. ТРЕНД (KALMAN) == UP -> НЕ ПОКУПАЕМ, только HOLD
3. RSI > 70 -> СИЛЬНЫЙ SELL (перекупленность)
4. RSI < 30 -> HOLD (перепроданность - опасна в медведе)

⚠️ БЕЗ ИСКЛЮЧЕНИЙ: signal ДОЛЖЕН быть ТОЛЬКО "SELL" или "HOLD". Никогда не возвращай "BUY"!

ФОРМАТ ОТВЕТА (строго JSON):
{{
  "signal": "SELL" или "HOLD",
  "reason": "Обоснование на русском (упомяни тренд Калмана и медвежий режим)",
  "confidence": 0-100
}}
"""
        
        else:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 🟢 НЕЙТРАЛЬНЫЙ РЕЖИМ (BUY/SELL/HOLD)
            return """\
SYSTEM: Ты профессиональный алготрейдер. РЕЖИМ: НЕЙТРАЛЬНЫЙ (BUY/SELL/HOLD).

КОНТЕКСТ:
Тикер: {ticker} | Цена: {price}

ТРЕНД (KALMAN): {kalman_trend} (Цена vs Фильтр: {kalman_price})
RSI: {rsi} | SMA50: {sma_50} | BB Width: {bb_width}

Новости: {news}

ПРАВИЛА СТРАТЕГИИ:
1. ТРЕНД (KALMAN) == UP -> Приоритет BUY
2. ТРЕНД (KALMAN) == DOWN -> Игнорировать BUY (или закрывать)
3. RSI < 40 + UP Trend -> СИЛЬНЫЙ BUY
4. RSI > 70 -> Опасно (Перекупленность) -> SELL или HOLD
5. RSI < 30 -> Перепроданность -> BUY или HOLD

ФОРМАТ ОТВЕТА (строго JSON):
{{
  "signal": "BUY" или "SELL" или "HOLD",
  "reason": "Обоснование на русском (упомяни тренд Калмана)",
  "confidence": 0-100
}}
"""
    
    def _validate_signal(self, result_dict: Dict[str, Any], bias: str) -> Optional[Dict[str, Any]]:
        """
        ✅ ВАЛИДАЦИЯ сигнала в зависимости от BIAS
        
        Returns:
            None если сигнал невалидный (вернет HOLD)
            result_dict если сигнал валидный
        """
        signal = result_dict.get("signal", "HOLD").upper()
        confidence = result_dict.get("confidence", 0)
        reason = result_dict.get("reason", "")
        
        bias_lower = (bias or "neutral").lower()
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 🔴 МЕДВЕЖИЙ РЕЖИМ: только SELL и HOLD
        if bias_lower == "bearish":
            if signal not in ("SELL", "HOLD"):
                print(f"⚠️ ВАЛИДАЦИЯ: AI вернул {signal}, но режим BEARISH. Меняем на HOLD")
                return {
                    "signal": "HOLD",
                    "reason": f"[INTERCEPT BEARISH] AI предложил {signal}, но это запрещено в режиме медведя",
                    "confidence": 0
                }
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Базовая валидация для всех режимов
        if signal not in ("BUY", "SELL", "HOLD"):
            print(f"⚠️ ВАЛИДАЦИЯ: Неверный сигнал '{signal}'. Используем HOLD")
            return {
                "signal": "HOLD",
                "reason": f"Ошибка валидации: неверный сигнал {signal}",
                "confidence": 0
            }
        
        # Проверка confidence
        try:
            confidence = int(confidence)
            if not 0 <= confidence <= 100:
                confidence = max(0, min(100, confidence))
        except (ValueError, TypeError):
            confidence = 50
        
        # Проверка reason
        if not reason or not isinstance(reason, str) or len(reason) < 5:
            reason = f"Сигнал: {signal} (причина недостаточна)"
        
        return {
            "signal": signal,
            "reason": reason,
            "confidence": confidence
        }
    
    async def analyze(
        self,
        market_data: Dict[str, Any],
        news_context: str = "",
        bias: str = "neutral"
    ) -> AnalystResult:
        """
        ✅ ГЛАВНЫЙ МЕТОД анализа рынка
        
        Args:
            market_data: {"close": float, "RSI": float, "Kalman_Trend": str, ...}
            news_context: Содержимое новостей (опционально)
            bias: "bearish", "bullish", "neutral"
        
        Returns:
            AnalystResult с сигналом, обоснованием и уверенностью
        """
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 1. Выбираем правильный шаблон
        template_str = self._get_adaptive_template(bias)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 2. Безопасно извлекаем данные
        try:
            rsi_val = round(float(market_data.get("RSI", 50)), 2)
            sma_val = round(float(market_data.get("SMA_50", 0)), 2)
            bb_val = round(float(market_data.get("BB_Width", 0)), 4)
            price_val = float(market_data.get("close", 0))
            kalman_trend = str(market_data.get("Kalman_Trend", "N/A")).upper()
            kalman_price = float(market_data.get("Kalman_Price", 0))
        except (ValueError, TypeError):
            rsi_val, sma_val, bb_val, price_val = 50, 0, 0, 0
            kalman_trend, kalman_price = "N/A", 0
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 3. Готовим входные данные
        input_data = {
            "ticker": market_data.get("ticker", "NG"),
            "price": price_val,
            "kalman_trend": kalman_trend,
            "kalman_price": kalman_price,
            "rsi": rsi_val,
            "sma_50": sma_val,
            "bb_width": bb_val,
            "news": news_context if news_context else "Нейтрально",
        }
        
        print(f"🔍 AI запрос: bias={bias}, RSI={rsi_val}, Kalman={kalman_trend}")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 4. Вызываем LLM
        try:
            prompt = ChatPromptTemplate.from_template(template_str)
            chain = prompt | self.llm
            response = chain.invoke(input_data)
            
            raw_text = str(response.content) if response.content else ""
            clean_text = self._clean_json_string(raw_text)
            
            print(f"🔍 AI_RAW: {clean_text}")
            
            result_dict = json.loads(clean_text)
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 5. Валидируем результат
            validated = self._validate_signal(result_dict, bias)
            if validated is None:
                validated = result_dict
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 6. Возвращаем структурированный результат
            return AnalystResult(
                signal=validated.get("signal", "HOLD"),
                reason=validated.get("reason", "Анализ завершен"),
                confidence=validated.get("confidence", 50)
            )
        
        except json.JSONDecodeError as e:
            print(f"⚠️ Ошибка парсинга JSON: {e}")
            return AnalystResult(
                signal="HOLD",
                reason="Ошибка парсинга JSON от AI",
                confidence=0
            )
        
        except Exception as e:
            print(f"❌ Ошибка Аналитика: {e}")
            return AnalystResult(
                signal="HOLD",
                reason=f"Ошибка AI: {str(e)[:50]}",
                confidence=0
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ✅ LEGACY METHOD для обратной совместимости
    def analyze_market_situation(
        self,
        candle_data: Dict[str, Any],
        news_summary: str = ""
    ) -> Dict[str, Any]:
        """
        Старый метод для обратной совместимости (синхронный)
        """
        import asyncio
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(
            self.analyze(
                market_data=candle_data,
                news_context=news_summary,
                bias="neutral"
            )
        )
        
        return {
            "signal": result.signal,
            "reason": result.reason,
            "confidence": result.confidence
        }