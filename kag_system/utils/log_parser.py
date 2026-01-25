import json
import os
from typing import List, Dict, Any

class LogParser:
    def __init__(self, log_path: str = "shadow_agents_log.jsonl"):
        self.log_path = log_path
    
    def parse_logs(self) -> List[Dict[str, Any]]:
        """Парсит логи и возвращает структурированные данные"""
        parsed_logs = []
        
        try:
            if os.path.exists(self.log_path):
                with open(self.log_path, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if line:
                            try:
                                log_entry = json.loads(line)
                                # Нормализуем структуру
                                normalized = self._normalize_log_entry(log_entry)
                                parsed_logs.append(normalized)
                            except json.JSONDecodeError as e:
                                print(f"⚠️ KAG-Parser: Ошибка парсинга строки {line_num}: {e}")
                                continue
        except Exception as e:
            print(f"❌ KAG-Parser: Ошибка чтения логов: {e}")
        
        return parsed_logs
    
    def _normalize_log_entry(self, log_entry: Dict[str, Any]) -> Dict[str, Any]:
        """Нормализует структуру лог-записи"""
        normalized = {
            'timestamp': log_entry.get('timestamp', ''),
            'input_state': log_entry.get('input_state', {}),
            'news': log_entry.get('news', ''),
            'AGENTS': {
                'PLANNER': log_entry.get('AGENTS', {}).get('PLANNER', {}),
                'ANALYST': log_entry.get('AGENTS', {}).get('ANALYST', {}),
                'RISK': log_entry.get('AGENTS', {}).get('RISK', {})
            },
            'raw_entry': log_entry  # Сохраняем оригинальную запись
        }
        return normalized
    
    def save_to_knowledge_db(self, parsed_logs: List[Dict[str, Any]], 
                           db_path: str = "kag_system/knowledge_db.jsonl"):
        """Сохраняет спарсенные логи в базу знаний"""
        try:
            with open(db_path, 'a', encoding='utf-8') as f:
                for log in parsed_logs:
                    f.write(json.dumps(log, ensure_ascii=False) + '\n')
            print(f"✅ KAG-Parser: Сохранено {len(parsed_logs)} записей в базу знаний")
        except Exception as e:
            print(f"❌ KAG-Parser: Ошибка сохранения в базу: {e}")
