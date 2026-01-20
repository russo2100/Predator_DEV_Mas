#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== Repo =="
git rev-parse --show-toplevel
git status -sb || true
echo

echo "== Grep: market_context blocks =="
grep -nE 'market_context\s*=\s*\{' -n src/main.py || true
echo

echo "== Grep: market_state usage =="
grep -nE '"market_state"\s*:' -n src/main.py || true
grep -nE 'data\.getmarketstate|data\.get\("marketstate"\)' -n src/main.py || true
echo

echo "== Grep: trend variables in main.py =="
grep -nE '\btrend5m\b|\btrend_5m\b|\btrend_h1\b|\btrend_h4\b|\btrend_d1\b|data\.gettrend' -n src/main.py || true
echo

echo "== Grep: dangerous locals().get fallbacks =="
grep -nE 'locals\(\)\.get\("trend_h1"' -n src/main.py || true
echo

echo "== Python compile (main) =="
python3 -m py_compile src/main.py
echo "OK: py_compile src/main.py"
echo

echo "== Python compile (all src) =="
python3 - <<'PY'
import compileall, sys
ok = compileall.compile_dir("src", quiet=1)
print("compileall:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
PY
echo

echo "== Docker compose config =="
docker compose config >/dev/null
echo "OK: docker compose config"
echo

echo "== Build image (no run) =="
docker compose build
echo "OK: docker compose build"
echo

echo "== Suggested safe fix preview =="
echo "If you see locals().get(\"trend_h1\", trend5m) -> trend5m is undefined."
echo "Safer: locals().get(\"trend_h1\", data.gettrend) or default 'FLAT'."
