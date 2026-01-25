import json
from pathlib import Path
from typing import Dict, Any, List
import math

class KAGSystem:
    def __init__(self, log_file: str = "shadow_agents_log.jsonl"):
        self.log_file = Path(log_file)
        self.similarity_threshold = 0.75  # Порог схожести для активации
    
    def get_recommendation(self, current_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        ОПТИМИЗИРОВАННАЯ ВЕРСИЯ: Парсит JSONL локально, БЕЗ вызова LLM.
        LLM вызывается только если найден прибыльный паттерн.
        """
        try:
            # 1. Читаем историю локально (JSONL)
            history = self._load_history()
            
            if len(history) < 5:
                return {
                    "status": "LEARNING",
                    "confidence": 0.0,
                    "reason": f"Недостаточно данных: {len(history)} записей. Требуется минимум 5."
                }
            
            # 2. Ищем похожие паттерны (математически, БЕЗ LLM)
            similar_patterns = self._find_similar_patterns(current_state, history)
            
            if not similar_patterns:
                return {
                    "status": "HOLD",
                    "confidence": 0.0,
                    "reason": "Похожих паттернов не найдено в истории."
                }
            
            # 3. Считаем статистику по найденным паттернам
            profitable = [p for p in similar_patterns if p.get('pnl_pct', 0) > 1.5]
            losing = [p for p in similar_patterns if p.get('pnl_pct', 0) < -1.0]
            
            win_rate = len(profitable) / len(similar_patterns) if similar_patterns else 0
            avg_pnl = sum(p.get('pnl_pct', 0) for p in similar_patterns) / len(similar_patterns)
            
            # 4. Решение БЕЗ LLM (чисто статистическое)
            if win_rate > 0.60 and avg_pnl > 2.0:
                status = "STRONG_BUY"
                reason = f"Найдено {len(profitable)} прибыльных паттернов (Win Rate: {win_rate*100:.0f}%, Avg PnL: {avg_pnl:.2f}%)"
            elif win_rate > 0.50:
                status = "ACTIVE"
                reason = f"Умеренная прибыльность: {len(similar_patterns)} паттернов, Win Rate {win_rate*100:.0f}%"
            elif win_rate < 0.40:
                status = "AVOID"
                reason = f"Низкая прибыльность: {len(losing)} убыточных из {len(similar_patterns)}"
            else:
                status = "HOLD"
                reason = f"Нейтральная история: {len(similar_patterns)} паттернов, Win Rate {win_rate*100:.0f}%"
            
            return {
                "status": status,
                "confidence": win_rate * 100,
                "similarity_count": len(similar_patterns),
                "win_rate": win_rate,
                "avg_pnl_pct": avg_pnl,
                "reason": reason
            }
        
        except Exception as e:
            print(f"⚠️ KAG Error: {e}")
            return {"status": "ERROR", "confidence": 0, "reason": str(e)}
    
    def _load_history(self) -> List[Dict[str, Any]]:
        """Загружает последние 200 записей из JSONL (локально, без LLM)."""
        if not self.log_file.exists():
            return []
        
        history = []
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        history.append(json.loads(line))
        except Exception as e:
            print(f"⚠️ KAG: Ошибка чтения {self.log_file}: {e}")
            return []
        
        # Возвращаем только последние 200 записей (оптимизация памяти)
        return history[-200:]
    
    def _find_similar_patterns(
        self, 
        current: Dict[str, Any], 
        history: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Поиск похожих паттернов по RSI, цене, тренду (математически).
        Возвращает список записей с similarity > threshold.
        """
        similar = []
        
        current_rsi = current.get('RSI', 50)
        current_price = current.get('close', 0)
        current_trend = current.get('trend5m', 'FLAT')
        
        for entry in history:
            hist_rsi = entry.get('rsi', 50)
            hist_price = entry.get('price', 0)
            hist_trend = entry.get('trend', 'FLAT')
            
            # Проверка схожести (упрощённая метрика)
            rsi_diff = abs(current_rsi - hist_rsi)
            price_diff = abs(current_price - hist_price) / max(current_price, 0.01)
            
            # Схожесть: чем меньше разница, тем выше схожесть
            rsi_similarity = max(0, 1 - rsi_diff / 50)  # RSI может варьироваться 0-100
            price_similarity = max(0, 1 - price_diff)
            trend_match = 1.0 if current_trend == hist_trend else 0.5
            
            # Общая схожесть (средневзвешенная)
            overall_similarity = (rsi_similarity * 0.4 + price_similarity * 0.4 + trend_match * 0.2)
            
            if overall_similarity >= self.similarity_threshold:
                entry['similarity'] = overall_similarity
                similar.append(entry)
        
        # Сортируем по убыванию схожести
        return sorted(similar, key=lambda x: x.get('similarity', 0), reverse=True)[:10]
