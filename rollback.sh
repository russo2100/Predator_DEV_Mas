#!/bin/bash
echo "🔄 ПРОТОКОЛ НАСЛЕДИЕ - ОТКАТ К РАБОЧЕЙ ВЕРСИИ"

docker compose down
rm -f patch_gwdd_logging.py fix_logging.py fix_market_state.py debug_decide_action.py integrate_zone_filter.py fix_pnl_calculation.py fix_pnl_simple.py
git checkout 307c47d -- src/main.py
python3 -m py_compile src/main.py
docker compose up -d --build

sleep 15
docker compose logs predator-bot 2>&1 | tail -30
echo "✅ ОТКАТ ЗАВЕРШЁН - БОТ ДОЛЖЕН РАБОТАТЬ"
