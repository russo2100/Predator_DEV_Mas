import json
import os
from typing import List, Dict, Any
import datetime

class KnowledgeDatabase:
    def __init__(self, db_path: str = "kag_system/knowledge_db.jsonl"):
        self.db_path = db_path
        self._ensure_db_exists()
    
    def _ensure_db_exists(self):
        if not os.path.exists(self.db_path):
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            with open(self.db_path, 'w', encoding='utf-8') as f:
                pass
    
    def save_entry(self, entry: Dict[str, Any]) -> bool:
        """Сохраняет запись в базу знаний"""
        try:
            with open(self.db_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            return True
        except Exception as e:
            print(f"❌ KAG-DB: Ошибка сохранения записи: {e}")
            return False
    
    def load_all_entries(self) -> List[Dict[str, Any]]:
        """Загружает все записи из базы"""
        entries = []
        try:
            if os.path.exists(self.db_path):
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
        except Exception as e:
            print(f"❌ KAG-DB: Ошибка загрузки базы: {e}")
        return entries
    
    def find_similar_pattern(self, current_state: Dict[str, Any], 
                        min_confidence: float = 0.5) -> List[Dict[str, Any]]:
        """Находит похожие паттерны в базе знаний"""
        print("DEBUG: Входим в find_similar_pattern")
        print(f"DEBUG: current_state: {current_state}")
        
        all_entries = self.load_all_entries()
        print(f"DEBUG: Найдено записей в базе: {len(all_entries)}")
        
        if not all_entries:
            print("DEBUG: База знаний пуста")
            return []
        
        # Выводим ключи первой записи
        first_entry_keys = list(all_entries[0].get('input_state', {}).keys())
        print(f"DEBUG: Ключи первой записи: {first_entry_keys}")
        
        similar_patterns = []
        
        for entry in all_entries:
            if 'input_state' not in entry:
                print("DEBUG: Запись без input_state, пропускаем")
                continue
                
            similarity_score = self._calculate_similarity(entry, current_state)
            print(f"DEBUG: Схожесть с записью {entry.get('timestamp', 'unknown')}: {similarity_score:.2%}")
            
            if similarity_score >= min_confidence:
                entry['similarity_score'] = similarity_score
                similar_patterns.append(entry)
                print(f"DEBUG: Добавили запись в similar_patterns")
        
        print(f"DEBUG: Найдено похожих паттернов: {len(similar_patterns)}")
        print(f"DEBUG: similar_patterns: {similar_patterns}")
        
        # Если не нашли похожих, возвращаем первую запись
        if not similar_patterns and all_entries:
            print("DEBUG: Похожих паттернов не найдено, возвращаем первую запись")
            similar_patterns = [all_entries[0]]
        
        # Сортируем по убыванию схожести
        similar_patterns.sort(key=lambda x: x.get('similarity_score', 0), reverse=True)
        print(f"DEBUG: Отсортированные похожие паттерны: {similar_patterns}")
        
        return similar_patterns

    
    def _calculate_similarity(self, historical_entry: Dict[str, Any], 
                            current_state: Dict[str, Any]) -> float:
        """Рассчитывает схожесть между текущим состоянием и историческим"""
        print("DEBUG: Входим в _calculate_similarity")
        print(f"DEBUG: historical_entry: {historical_entry}")
        print(f"DEBUG: current_state: {current_state}")
        
        if 'input_state' not in historical_entry:
            print("DEBUG: historical_entry без input_state")
            return 0.0
        
        historical_input = historical_entry['input_state']
        print(f"DEBUG: historical_input: {historical_input}")
        
        score = 0.0
        total_compared = 0
        
        # Используем только те метрики, которые есть в текущем состоянии
        key_metrics = ['close', 'RSI', 'ATR', 'trend5m', 'trendh1']
        print(f"DEBUG: key_metrics: {key_metrics}")
        
        for metric in key_metrics:
            if metric in historical_input and metric in current_state:
                total_compared += 1
                hist_val = historical_input[metric]
                curr_val = current_state[metric]
                print(f"DEBUG: Сравниваем {metric}: {hist_val} vs {curr_val}")
                
                if isinstance(hist_val, (int, float)) and isinstance(curr_val, (int, float)):
                    # Нормализованная разница
                    max_diff = max(abs(hist_val), abs(curr_val), 1.0)
                    diff = abs(hist_val - curr_val) / max_diff
                    similarity = max(0, 1.0 - diff)
                    score += similarity
                    print(f"DEBUG: Числовая схожесть: {similarity:.2%}")
                elif isinstance(hist_val, str) and isinstance(curr_val, str):
                    # Точное совпадение для строк
                    if hist_val == curr_val:
                        score += 1.0
                        print("DEBUG: Строки совпадают")
                    else:
                        print("DEBUG: Строки не совпадают")
        
        print(f"DEBUG: score: {score}, total_compared: {total_compared}")
        
        result = score / total_compared if total_compared > 0 else 0.0
        print(f"DEBUG: Возвращаем схожесть: {result:.2%}")
        
        return result

