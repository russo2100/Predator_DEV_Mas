"""
Storage Agent - EIA Weekly Natural Gas Storage Report Monitor

FIX (2025-12-26):
- Switch from api.eia.gov/v2 (may return 403) to ir.eia.gov/ngs/wngsr.json (public).
- Provide correct weekly change sign:
    weekly_change_bcf > 0  -> Injection
    weekly_change_bcf < 0  -> Withdrawal
- Format string per spec:
    📊 EIA Storage (week ending YYYY-MM-DD): 3,579 Bcf (Withdrawal -167 Bcf)
"""
from __future__ import annotations

import httpx
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple


class StorageAgent:
    """
    Monitors EIA weekly natural gas storage report.

    Output fields (minimal correct set):
    - report_date (week ending, ISO date)
    - working_gas_bcf
    - weekly_change_bcf (net change; negative=withdrawal, positive=injection)

    Optional (best-effort if present in feed):
    - vs_last_year_bcf
    - vs_5yr_avg_bcf
    """

    def __init__(self, api_key: str = ""):
        # api_key kept for backward compatibility, not required for ir.eia.gov feed
        self.api_key = api_key
        self.logger = logging.getLogger(__name__)

        # Public JSON used by EIA storage page
        self.public_json_url = "https://ir.eia.gov/ngs/wngsr.json"

    async def get_storage_data(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "PredatorBot/1.0"}) as client:
                resp = await client.get(self.public_json_url)
                resp.raise_for_status()
                payload = resp.json()

            latest = self._extract_latest_total(payload)
            if latest is None:
                raise ValueError("No usable TOTAL record in wngsr.json")

            report_date = latest.get("week_ending")
            working_gas_bcf = latest.get("working_gas_bcf")
            weekly_change_bcf = latest.get("weekly_change_bcf")
            vs_last_year_bcf = None
            vs_5yr_avg_bcf = None

            bias = self._calculate_bias(weekly_change_bcf)

            return {
                "success": True,
                "report_date": report_date,  # ISO YYYY-MM-DD
                "working_gas_bcf": float(working_gas_bcf),
                "weekly_change_bcf": float(weekly_change_bcf),
                "vs_last_year_bcf": None if vs_last_year_bcf is None else float(vs_last_year_bcf),
                "vs_5yr_avg_bcf": None if vs_5yr_avg_bcf is None else float(vs_5yr_avg_bcf),
                "storage_bias": bias,
                "source": "EIA ir.eia.gov/ngs/wngsr.json",
                "reason": f"EIA storage report {report_date}: working_gas={working_gas_bcf:.0f} Bcf, weekly_change={weekly_change_bcf:+.0f} Bcf, bias={bias}",
            }

        except Exception as e:
            self.logger.error(f"StorageAgent error: {e}")
            return {
                "success": False,
                "storage_bias": "NEUTRAL",
                "report_date": datetime.now().strftime("%Y-%m-%d"),
                "source": "EIA (fallback)",
                "reason": f"API error: {str(e)}",
                "error": str(e),
            }

    def _extract_latest_total(self, payload: dict) -> dict:
        """
        Извлекает последние данные по Total Lower 48 из wngsr.json.
        Возвращает: {
            'week_ending': 'YYYY-MM-DD',
            'working_gas_bcf': int,
            'weekly_change_bcf': int (со знаком),
            'type': 'Injection' или 'Withdrawal'
        }
        """
        try:
            series_list = payload.get("series", [])
            if not series_list:
                self.logger.warning("No 'series' array in wngsr.json")
                return {}

            # Первая серия — это "total lower 48 states"
            total_series = series_list[0]
            name = total_series.get("name", "").lower()
            
            if "total lower 48" not in name:
                self.logger.warning(f"First series is not 'total lower 48', got: {name}")
                return {}

            # Последняя неделя: data[0] = [date, value]
            data_points = total_series.get("data", [])
            if not data_points or len(data_points[0]) < 2:
                self.logger.warning("No valid data points in total series")
                return {}

            latest = data_points[0]
            week_ending = latest[0]
            working_gas = int(latest[1])

            # Weekly change из calculated.net_change
            calculated = total_series.get("calculated", {})
            net_change = calculated.get("net_change")
            
            if net_change is None:
                self.logger.warning("No net_change in calculated fields")
                return {}
            
            net_change = int(net_change)
            
            # Определяем тип: положительный = Injection, отрицательный = Withdrawal
            if net_change > 0:
                change_type = "Injection"
            elif net_change < 0:
                change_type = "Withdrawal"
            else:
                change_type = "No Change"

            return {
                "week_ending": week_ending,
                "working_gas_bcf": working_gas,
                "weekly_change_bcf": net_change,
                "type": change_type
            }

        except Exception as e:
            self.logger.error(f"Error parsing wngsr.json structure: {e}")
            return {}

    def _calculate_bias(self, weekly_change_bcf: float) -> str:
        """
        Определяет storage_bias на основе недельного изменения.
        Injection (положительный) = BEARISH (больше запасов - меньше спроса)
        Withdrawal (отрицательный) = BULLISH (меньше запасов - больше спроса)
        """
        if weekly_change_bcf > 100:
            return "BEARISH"
        elif weekly_change_bcf < -100:
            return "BULLISH"
        else:
            return "NEUTRAL"


    def get_storage_context_str(self, data: Dict[str, Any]) -> str:
        """
        Spec-aligned output line.
        """
        if not data or not data.get("success"):
            return f"⚠️ EIA Storage unavailable: {data.get('reason', 'Unknown error')}"

        report_date = data.get("report_date", "")
        wg = float(data.get("working_gas_bcf", 0.0))
        chg = float(data.get("weekly_change_bcf", 0.0))

        if chg > 0:
            cls = "Injection"
        elif chg < 0:
            cls = "Withdrawal"
        else:
            cls = "Flat"

        # Format with thousands separator like 3,579
        wg_str = f"{wg:,.0f}"
        chg_str = f"{chg:+.0f}"

        extra = []
        if data.get("vs_last_year_bcf") is not None:
            extra.append(f"vs LY {float(data['vs_last_year_bcf']):+.0f} Bcf")
        if data.get("vs_5yr_avg_bcf") is not None:
            extra.append(f"vs 5yr {float(data['vs_5yr_avg_bcf']):+.0f} Bcf")
        extra_str = f" | {'; '.join(extra)}" if extra else ""

        return f"📊 EIA Storage (week ending {report_date}): {wg_str} Bcf ({cls} {chg_str} Bcf){extra_str}"

    @staticmethod
    def _to_float(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_iso_date(v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()

        # Already ISO
        try:
            if len(s) >= 10 and s[4] == "-" and s[7] == "-":
                return s[:10]
        except Exception:
            pass

        # Common formats: "20251212" or "12/12/2025"
        for fmt in ("%Y%m%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except Exception:
                continue

        return None


if __name__ == "__main__":
    import asyncio

    async def test():
        agent = StorageAgent(api_key="")
        result = await agent.get_storage_data()
        print(agent.get_storage_context_str(result))
        print(result)

    asyncio.run(test())
