import json
from pathlib import Path
from datetime import datetime

def generate_audit_report():
    today_str = datetime.now().strftime("%Y%m%d")
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_file = log_dir / f"shadow_agents_log_{today_str}.jsonl"
    report_file = log_dir / f"audit_report_{today_str}.md"
    
    if not log_file.exists():
        print(f"⚠️ Файл логов {log_file} не найден. Ждем первых сделок.")
        return
        
    trades = []
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            try:
                entry = json.loads(line)
                action = entry.get("action", "")
                # Фильтруем только активные действия (входы и выходы), игнорируем NOOP
                if action and action != "NOOP":
                    trades.append(entry)
            except Exception:
                continue

    report = f"# 🕵️‍♂️ Predator Mathematical Audit Report - {today_str}\n\n"
    report += f"**Обнаружено торговых решений:** {len(trades)}\n\n"
    
    if not trades:
        report += "Сделок за сегодня не зафиксировано.\n"
    
    for i, t in enumerate(trades, 1):
        report += f"## 📈 Решение #{i} | Время: {t.get('timestamp')}\n"
        report += f"**Действие бота:** `{t.get('action')}` | **Сигнал LLM:** {t.get('ai_signal')} ({t.get('ai_confidence')}%)\n\n"
        
        report += f"### 🧠 Аргументация Нейросети:\n> {t.get('reason')}\n\n"
        
        report += f"### 📊 Математический Срез (Алиби):\n"
        report += f"- **Цена:** {t.get('price')} | **RSI:** {t.get('rsi')}\n"
        report += f"- **Локальный тренд:** {t.get('trend_ltf')} | **Глобальный:** {t.get('trend_htf')}\n"
        report += f"- **Текущий PnL сделки:** {t.get('position_pnl_pct')}%\n"
        report += f"- **Уровень Стоп-Лосса (ATR):** {t.get('sl_level')}\n\n"
        
        report += f"### 📰 Информационный Фон (Что видела LLM):\n"
        news = t.get("news_context", "Фон отсутствовал или не записан.")
        report += f"```text\n{news}\n```\n"
        report += "---\n\n"
        
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"✅ Отчет аудита сохранен: {report_file}")

if __name__ == "__main__":
    generate_audit_report()
