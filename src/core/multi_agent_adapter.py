# src/core/multi_agent_adapter.py

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.agents.analyst import MarketAnalyst
from src.agents.planner import PlannerAgent
from src.agents.risk_agent import RiskAgent


@dataclass
class PlannerCache:
    last_ts_utc: Optional[datetime] = None
    plan: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.plan is None:
            self.plan = {"bias": "NEUTRAL", "riskmode": "CONSERVATIVE", "reason": "init-cache"}


class MultiAgentShadowAdapter:
    """
    Shadow Mode: параллельно гоняет Planner/Analyst/Risk и пишет результаты в JSONL.
    НЕ торгует.
    """

    def __init__(
        self,
        logfile: str = "shadow_agents_log.jsonl",
        planner_ttl_seconds: int = 15 * 60,  # 15 минут
        debug_logfile: str = "shadow_debug.log",
    ) -> None:
        self.logpath = Path(logfile)

        self.planner = PlannerAgent()
        self.analyst = MarketAnalyst()
        self.risk = RiskAgent()

        self.planner_ttl_seconds = int(planner_ttl_seconds)
        self.planner_cache = PlannerCache()

        self.logger = logging.getLogger("ShadowAgents")
        self.logger.setLevel(logging.INFO)

        # чтобы не плодить хендлеры при повторных импорт/запусках
        if debug_logfile and not any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "").endswith(debug_logfile)
            for h in self.logger.handlers
        ):
            fh = logging.FileHandler(debug_logfile, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
            self.logger.addHandler(fh)

    async def run_shadow_analysis(
        self,
        market_data: Dict[str, Any],
        position_data: Dict[str, Any],
        news_context: str,
        ai_signal: str = "N/A",
        ai_confidence: float = 0,
        ai_reason: str = "",
    ) -> None:
        # Алиас для совместимости с разными стилями именования в main.py
        await self.runshadowanalysis(
            marketdata=market_data,
            positiondata=position_data,
            newscontext=news_context,
            ai_signal=ai_signal,
            ai_confidence=ai_confidence,
            ai_reason=ai_reason,
        )

    async def runshadowanalysis(
        self,
        marketdata: Dict[str, Any],
        positiondata: Dict[str, Any],
        newscontext: str,
        ai_signal: str = "N/A",
        ai_confidence: float = 0,
        ai_reason: str = "",
    ) -> None:
        """
        marketdata: close/RSI/ATR/trend5m/...
        positiondata: lots/avgprice/pnlpct/...
        newscontext: строка bias/контекст (как у тебя currentbias)
        """
        try:
            now = datetime.now(timezone.utc)
            timestamp = now.isoformat()

            agent_state: Dict[str, Any] = {
                "timestamp": timestamp,
                "close": marketdata.get("close"),
                "RSI": marketdata.get("RSI"),
                "ATR": marketdata.get("ATR"),
                "trend5m": marketdata.get("trend5m", "FLAT"),
                "trendh1": marketdata.get("trendh1", "FLAT"),
                "momentum24h": marketdata.get("momentum24h", 0.0),
                "lots": positiondata.get("lots", 0),
                "avgprice": positiondata.get("avgprice", 0.0),
                "pnlpct": positiondata.get("pnlpct", 0.0),
                "newssummary": newscontext,
                "ai_signal": ai_signal,
                "ai_confidence": ai_confidence,
                "ai_reason": ai_reason,
            }

            # --- Planner: кэшируем, чтобы не долбить LLM каждый цикл ---
            planner_due = (
                self.planner_cache.last_ts_utc is None
                or (now - self.planner_cache.last_ts_utc).total_seconds() >= self.planner_ttl_seconds
            )

            if planner_due:
                planner_task = asyncio.create_task(self.safecall(self.planner.createplan, agent_state))
            else:
                # мгновенный "task" с результатом из кэша
                planner_task = asyncio.create_task(asyncio.sleep(0, result=self.planner_cache.plan))

            # --- Analyst: ВАРИАНТ B — не вызываем LLM в Shadow, берём из main ---
            analyst_payload = {
                "signal": ai_signal,
                "confidence": ai_confidence,
                "reason": ai_reason,
                "source": "main_loop_result",
            }
            analyst_task = asyncio.create_task(asyncio.sleep(0, result=analyst_payload))

            # --- Risk: локальная оценка/правила (как реализовано в RiskAgent) ---
            risk_task = asyncio.create_task(self.safecall(self.risk.assessrisk, agent_state))

            planner_res, analyst_res, risk_res = await asyncio.gather(planner_task, analyst_task, risk_task)

            # обновляем кэш планера только если ответ валидный
            if planner_due and isinstance(planner_res, dict) and "error" not in planner_res:
                self.planner_cache.plan = planner_res
                self.planner_cache.last_ts_utc = now

            log_entry = {
                "timestamp": timestamp,
                "inputstate": {k: v for k, v in agent_state.items() if k != "newssummary"},
                "news": agent_state.get("newssummary", ""),
                "AGENTS": {
                    "PLANNER": planner_res,
                    "ANALYST": analyst_res,
                    "RISK": risk_res,
                },
            }

            with self.logpath.open("a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")

            print(
                "👻 ShadowAgents: записан лог "
                f"(Planner={self._planner_label(planner_res)}, Risk={self._risk_score(risk_res)})"
            )

        except Exception as e:
            print(f"❌ ShadowAdapter Error: {e}")
            self.logger.error("Critical error in shadow run: %s", e, exc_info=True)

    async def safecall(self, func, *args):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args)
            return func(*args)
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _planner_label(res: Any) -> str:
        """
        PlannerAgent обычно возвращает bias/riskmode.
        Но если вернулся другой формат — покажем хоть что-то.
        """
        if isinstance(res, dict):
            if "bias" in res:
                bias = res.get("bias", "N/A")
                riskmode = res.get("riskmode", "N/A")
                return f"{bias}:{riskmode}"
            return str(res.get("signal") or res.get("action") or "N/A")
        return "N/A"

    @staticmethod
    def _risk_score(res: Any) -> int:
        if isinstance(res, dict):
            return int(res.get("riskscore", res.get("risk_score", 0)) or 0)
        return 0
