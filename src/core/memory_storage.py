import json
import os
from datetime import datetime
from pathlib import Path


class MemoryStorage:
    def __init__(self, base_dir="memory"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)  # Создает папку, если нет

        # Файл текущего состояния (открытые позиции)
        self.state_file = self.base_dir / "current_state.json"

        # Файл истории (журнал)
        self.history_file = self.base_dir / "trade_history.json"

        self.state = self._load_json(self.state_file)

    def _load_json(self, filepath):
        if not filepath.exists():
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def _save_json(self, filepath, data):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def save_daily_plan(self, news_summary: str, strategy_notes: str):
        """Сохраняет утренний план (новости + установки)"""
        today = datetime.now().strftime("%Y-%m-%d")
        daily_file = self.base_dir / f"journal_{today}.txt"

        with open(daily_file, "a", encoding="utf-8") as f:
            f.write(f"\n=== PLAN {datetime.now().strftime('%H:%M')} ===\n")
            f.write(f"NEWS: {news_summary}\n")
            f.write(f"NOTES: {strategy_notes}\n")
            f.write("="*30 + "\n")

    def get_position(self):
        return self.state.get("position")

    def open_position(self, ticker, figi, price, quantity, order_id, sl, tp):
        self.state["position"] = {
            "ticker": ticker,
            "figi": figi,
            "entry_price": price,
            "quantity": quantity,
            "order_id": order_id,
            "open_time": datetime.now().isoformat(),
            "sl": sl,
            "tp": tp
        }
        self._save_json(self.state_file, self.state)

        # Логируем в дневной журнал
        self.save_daily_plan("SYSTEM", f"OPEN POSITION {ticker} @ {price}")

    def close_position(self, exit_price, reason, pnl):
        pos = self.state.get("position")
        if not pos:
            return

        record = {
            **pos,
            "exit_price": exit_price,
            "exit_time": datetime.now().isoformat(),
            "reason": reason,
            "pnl": pnl
        }

        # Добавляем в историю
        history = self._load_json(self.history_file)
        if "trades" not in history:
            history["trades"] = []
        history["trades"].append(record)
        self._save_json(self.history_file, history)

        # Очищаем текущее состояние
        self.state["position"] = None
        self._save_json(self.state_file, self.state)

        # Логируем
        self.save_daily_plan(
            "SYSTEM", f"CLOSE POSITION {pos['ticker']} @ {exit_price}. PnL: {pnl:.2f}")
