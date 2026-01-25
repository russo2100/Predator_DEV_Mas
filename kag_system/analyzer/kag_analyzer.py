import json
from typing import List, Dict, Any, Optional  # Исправление: добавляем Optional
from kag_system.database.knowledge_db import KnowledgeDatabase

class KAGAnalyzer:
    def __init__(self, db_path: str = "kag_system/knowledge_db.jsonl"):
        self.db = KnowledgeDatabase(db_path)
    
    def analyze_pattern(self, current_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Анализирует текущее состояние и возвращает рекомендацию"""
        # Находим похожие паттерны
        similar_patterns = self.db.find_similar_pattern(current_state, min_confidence=0.7)
        
        if not similar_patterns:
            return None
        
        # Генерируем рекомендацию на основе прошлого опыта
        recommendation = self._generate_recommendation(similar_patterns, current_state)
        return recommendation
    
    def _generate_recommendation(self, similar_patterns: List[Dict[str, Any]], 
                               current_state: Dict[str, Any]) -> Dict[str, Any]:
        """Генерирует рекомендацию на основе похожих паттернов"""
        # Статистика по прошлым результатам
        profitable_count = 0
        total_count = len(similar_patterns)
        recommendations = []
        avg_pnl = 0.0  # Исправление: объявляем переменную
        
        for pattern in similar_patterns:
            agents = pattern.get('AGENTS', {})
            risk = agents.get('RISK', {})
            pnl = risk.get('pnl_pct', 0.0)
            
            if pnl > 0:
                profitable_count += 1
            
            avg_pnl += pnl
            
            # Извлекаем рекомендацию из прошлого плана
            if 'PLANNER' in agents and isinstance(agents['PLANNER'], dict):
                recommendations.append({
                    'bias': agents['PLANNER'].get('bias', 'NEUTRAL'),
                    'risk_mode': agents['PLANNER'].get('risk_mode', 'CONSERVATIVE'),
                    'reason': agents['PLANNER'].get('reason', '')
                })
        
        avg_pnl = avg_pnl / total_count if total_count > 0 else 0.0
        win_rate = profitable_count / total_count if total_count > 0 else 0.0
        
        # Генерируем итоговую рекомендацию
        recommendation = {
            'status': 'ACTIVE',
            'confidence': win_rate,
            'similarity_count': total_count,
            'win_rate': win_rate,
            'avg_pnl_pct': avg_pnl,
            'suggested_bias': self._determine_suggested_bias(recommendations),
            'suggested_risk_mode': self._determine_suggested_risk_mode(recommendations),
            'reason': self._generate_reason(win_rate, avg_pnl, total_count, current_state),
            'similar_patterns': similar_patterns[:5]  # Возвращаем 5 самых похожих
        }
        
        return recommendation
    
    def _determine_suggested_bias(self, recommendations: List[Dict[str, Any]]) -> str:
        """Определяет рекомендуемое направление на основе прошлого опыта"""
        if not recommendations:
            return 'NEUTRAL'
        
        bias_counts = {'LONG_ONLY': 0, 'SHORT_ONLY': 0, 'NEUTRAL': 0, 'NO_TRADE': 0}
        
        for rec in recommendations:
            bias = rec.get('bias', 'NEUTRAL')
            if bias in bias_counts:
                bias_counts[bias] += 1
        
        return max(bias_counts.keys(), key=lambda x: bias_counts[x])
    
    def _determine_suggested_risk_mode(self, recommendations: List[Dict[str, Any]]) -> str:
        """Определяет рекомендуемый уровень риска"""
        if not recommendations:
            return 'CONSERVATIVE'
        
        risk_counts = {'AGGRESSIVE': 0, 'NORMAL': 0, 'CONSERVATIVE': 0}
        
        for rec in recommendations:
            risk_mode = rec.get('risk_mode', 'CONSERVATIVE')
            if risk_mode in risk_counts:
                risk_counts[risk_mode] += 1
        
        return max(risk_counts.keys(), key=lambda x: risk_counts[x])
    
    def _generate_reason(self, win_rate: float, avg_pnl: float, 
                        pattern_count: int, current_state: Dict[str, Any]) -> str:
        """Генерирует объяснение рекомендации"""
        base_reason = f"Найдено {pattern_count} похожих паттернов в истории. "
        
        if win_rate >= 0.7:
            base_reason += f"Процент прибыльных сделок: {win_rate:.1%}. "
        elif win_rate >= 0.5:
            base_reason += f"Процент прибыльных сделок: {win_rate:.1%} (нейтрально). "
        else:
            base_reason += f"Процент прибыльных сделок: {win_rate:.1%} (низкий). "
        
        base_reason += f"Средний PnL: {avg_pnl:.2f}%. "
        
        # Добавляем контекст текущего состояния
        current_trend = current_state.get('trend5m', 'FLAT')
        current_rsi = current_state.get('RSI', 50)
        
        if current_trend == 'UP':
            base_reason += "Текущий 5-минутный тренд: восходящий. "
        elif current_trend == 'DOWN':
            base_reason += "Текущий 5-минутный тренд: нисходящий. "
        else:
            base_reason += "Текущий 5-минутный тренд: боковой. "
        
        if current_rsi > 70:
            base_reason += "RSI указывает на перекупленность. "
        elif current_rsi < 30:
            base_reason += "RSI указывает на перепроданность. "
        
        return base_reason.strip()
