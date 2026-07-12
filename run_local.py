# run_local.py
"""Локальный запуск бота для тестирования."""
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Добавить корень проекта в sys.path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Запуск main
from src.main import main_loop
import asyncio

if __name__ == "__main__":
    print("🚀 Starting Predator Trading Bot (Local Test Mode)")
    asyncio.run(main_loop())
