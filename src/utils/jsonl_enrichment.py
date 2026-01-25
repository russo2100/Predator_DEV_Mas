"""
JSONL enrichment: добавляет account_snapshot, trades_executed, pnl_today из TBank API.
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

async def enrich_cycle_event(
    cycle_data: dict,
    executor,  # твой MainOrderExecutor с методами get_portfolio/get_operations
    active_figi: str,
    session_id: str
) -> dict:
    """
    Обогащает событие цикла данными из broker API (TBank Invest).
    
    Args:
        cycle_data: базовый dict с input_state/decision/timestamp/cycle
        executor: MainOrderExecutor (или wrapper над tinkoff API)
        active_figi: FIGI инструмента (например NGM)
        session_id: уникальный ID сессии (генерируется при старте бота)
    
    Returns:
        enriched dict с полями account_snapshot, trades_executed (если есть), event_id
    """
    now = datetime.now(tz=timezone.utc)
    
    # === 1. Account Snapshot (портфель, позиции, баланс) ===
    try:
        portfolio = await executor.get_portfolio()  # метод должен быть в executor
        positions_data = []
        unrealized_total_rub = 0.0
        
        for pos in portfolio.get("positions", []):
            if pos.get("instrument_type") == "futures" and pos.get("figi") == active_figi:
                unrealized = pos.get("expected_yield_value", 0.0)  # в рублях
                positions_data.append({
                    "symbol": pos.get("name", ""),
                    "contract_code": pos.get("ticker", ""),
                    "qty": int(pos.get("balance", 0)),
                    "avg_entry_price_usd": pos.get("average_position_price_value"),
                    "current_price_usd": pos.get("current_price_value"),
                    "unrealized_pnl_rub": unrealized,
                    "guarantee_rub": pos.get("blocked", 0.0)
                })
                unrealized_total_rub += unrealized
        
        balance_rub = portfolio.get("total_amount_currencies", 0.0)
        portfolio_value_rub = portfolio.get("total_amount_portfolio", 0.0)
    except Exception as e:
        print(f"⚠️ get_portfolio failed: {e}")
        positions_data = []
        unrealized_total_rub = 0.0
        balance_rub = 0.0
        portfolio_value_rub = 0.0
    
    # === 2. PnL Today (realized var margin + fees из operations) ===
    today_start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        operations = await executor.get_operations(
            from_date=today_start,
            to_date=now,
            figi=active_figi
        )
        
        var_margin_today = sum(
            op.get("payment_value", 0.0) for op in operations
            if "MARGIN" in op.get("operation_type", "").upper() or "VARIATION" in op.get("operation_type", "").upper()
        )
        
        fees_today = sum(
            abs(op.get("commission_value", 0.0)) for op in operations
            if "COMMISSION" in op.get("operation_type", "").upper() or "FEE" in op.get("operation_type", "").upper()
        )
    except Exception as e:
        print(f"⚠️ get_operations failed: {e}")
        var_margin_today = 0.0
        fees_today = 0.0
    
    pnl_today = {
        "realized_pnl_rub": var_margin_today,
        "unrealized_pnl_rub": unrealized_total_rub,
        "fees_total_rub": fees_today,
        "net_pnl_rub": var_margin_today + unrealized_total_rub - fees_today
    }
    
    # === 3. Trades Executed (если на этом цикле была сделка) ===
    trades_executed = []
    action = cycle_data.get("decision", {}).get("action", "NOOP")
    
    if action != "NOOP":
        # Ищем недавние сделки (последние 5 минут)
        try:
            recent_ops = await executor.get_operations(
                from_date=now - timedelta(minutes=5),
                to_date=now,
                figi=active_figi
            )
            
            for op in recent_ops:
                op_type = op.get("operation_type", "")
                if "BUY" in op_type.upper() or "SELL" in op_type.upper():
                    side = "BUY" if "BUY" in op_type.upper() else "SELL"
                    
                    # Разделение комиссий (упрощённо: делим на 3 части)
                    total_commission = abs(op.get("commission_value", 0.0))
                    broker_fee = total_commission / 3.0
                    exchange_fee = total_commission / 3.0
                    clearing_fee = total_commission / 3.0
                    
                    trades_executed.append({
                        "trade_id": op.get("id", ""),
                        "order_id": op.get("order_id", ""),
                        "timestamp": op.get("date", now).isoformat(),
                        "side": side,
                        "symbol": op.get("instrument_name", ""),
                        "contract_code": op.get("ticker", ""),
                        "qty": abs(op.get("quantity", 0)),
                        "price_usd": op.get("price_value", 0.0),
                        "amount_rub": abs(op.get("payment_value", 0.0)),
                        "broker_fee_rub": broker_fee,
                        "exchange_fee_rub": exchange_fee,
                        "clearing_fee_rub": clearing_fee,
                        "settlement_date": op.get("date", now).isoformat()
                    })
        except Exception as e:
            print(f"⚠️ get recent trades failed: {e}")
    
    # === 4. Фикс unrealized_pnl_pct → переименование + добавление флага ===
    old_pnl = cycle_data.get("input_state", {}).pop("pnl_pct", None)
    cycle_data.setdefault("input_state", {})["unrealized_pnl_pct"] = old_pnl
    cycle_data["input_state"]["unrealized_pnl_pct_missing"] = (old_pnl is None)
    
    # === 5. Собираем enriched event ===
    cycle_data["event_id"] = f"{session_id}_{cycle_data.get('cycle', 0)}_{int(now.timestamp() * 1000)}"
    cycle_data["session_id"] = session_id
    
    cycle_data["account_snapshot"] = {
        "timestamp": now.isoformat(),
        "balance_rub": balance_rub,
        "portfolio_value_rub": portfolio_value_rub,
        "positions": positions_data,
        "pnl_today": pnl_today
    }
    
    if trades_executed:
        cycle_data["trades_executed"] = trades_executed
    
    return cycle_data
