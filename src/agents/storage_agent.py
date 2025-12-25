"""
Storage Agent - EIA Weekly Natural Gas Storage Report Monitor
Uses EIA Open Data API v2 to fetch weekly storage data and calculate storage bias.
"""
import httpx
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

class StorageAgent:
    """
    Monitors EIA weekly natural gas storage reports via Open Data API.
    Returns storage bias (BULLISH/NEUTRAL/BEARISH) based on injection/withdrawal rates.
    """
    
    def __init__(self, api_key: str):
        """
        Args:
            api_key: EIA API key from https://www.eia.gov/opendata/register.php
        """
        self.api_key = api_key
        self.base_url = "https://api.eia.gov/v2"
        self.logger = logging.getLogger(__name__)
        
        # EIA Series ID for Weekly Natural Gas Storage (Working Gas in Underground Storage)
        # Series: NG.NW2_EPG0_SWO_R48_BCF.W (Lower 48 States, Weekly, Billion Cubic Feet)
        self.series_id = "NG.NW2_EPG0_SWO_R48_BCF.W"
        
    async def get_storage_data(self) -> Dict[str, Any]:
        """
        Fetches latest EIA weekly storage data and calculates storage bias.
        
        Returns:
            {
                'storage_bcf': float,           # Latest working gas storage (BCF)
                'injection_rate': float,        # Weekly change (BCF)
                'storage_bias': str,            # BULLISH/NEUTRAL/BEARISH
                'pct_change': float,            # % change week-over-week
                'report_date': str,             # ISO format date
                'source': str,                  # Data source identifier
                'reason': str                   # Human-readable explanation
            }
        """
        params = {
            "api_key": self.api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": self.series_id,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 8  # Fetch last 8 weeks for trend analysis
        }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = f"{self.base_url}/natural-gas/stor/wkly/data/"
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if not data.get("response", {}).get("data"):
                    raise ValueError("No storage data returned from EIA API")
                
                records = data["response"]["data"]
                latest = records[0]
                previous = records[1] if len(records) > 1 else None
                
                storage_bcf = float(latest["value"])
                report_date = latest["period"]
                
                # Calculate injection/withdrawal rate
                if previous:
                    prev_value = float(previous["value"])
                    injection_rate = storage_bcf - prev_value
                    pct_change = (injection_rate / prev_value) * 100 if prev_value > 0 else 0.0
                else:
                    injection_rate = 0.0
                    pct_change = 0.0
                
                # Determine storage bias
                bias = self._calculate_bias(injection_rate, storage_bcf, records)
                
                reason = (
                    f"Storage: {storage_bcf:.0f} BCF, "
                    f"{'Injection' if injection_rate >= 0 else 'Withdrawal'}: {abs(injection_rate):.1f} BCF "
                    f"({pct_change:+.1f}%), Bias: {bias}"
                )
                
                return {
                    "storage_bcf": float(storage_bcf),
                    "injection_rate": float(injection_rate),
                    "storage_bias": bias,
                    "pct_change": float(pct_change),
                    "report_date": report_date,
                    "source": "EIA Open Data API v2",
                    "reason": reason
                }
                
        except Exception as e:
            self.logger.error(f"StorageAgent error: {e}")
            # Fallback: neutral bias with zero values
            return {
                "storage_bcf": 0.0,
                "injection_rate": 0.0,
                "storage_bias": "NEUTRAL",
                "pct_change": 0.0,
                "report_date": datetime.now().strftime("%Y-%m-%d"),
                "source": "EIA (fallback)",
                "reason": f"API error: {str(e)}",
                "error": str(e)
            }
    
    def _calculate_bias(self, injection_rate: float, storage_bcf: float, records: list) -> str:
        """
        Calculate storage bias based on injection/withdrawal patterns.
        
        Logic:
        - Large withdrawals (< -80 BCF) → BULLISH (supply tightening)
        - Moderate withdrawals (-80 to -40 BCF) → NEUTRAL
        - Small changes (-40 to +40 BCF) → NEUTRAL
        - Moderate injections (+40 to +80 BCF) → NEUTRAL
        - Large injections (> +80 BCF) → BEARISH (oversupply)
        
        Args:
            injection_rate: Weekly net injection (+) or withdrawal (-)
            storage_bcf: Current storage level
            records: Historical data for trend analysis
            
        Returns:
            "BULLISH", "NEUTRAL", or "BEARISH"
        """
        # Threshold-based classification
        if injection_rate < -80:
            return "BULLISH"
        elif injection_rate > 80:
            return "BEARISH"
        else:
            # Check 4-week trend for moderate cases
            if len(records) >= 5:
                trend = self._get_trend(records[:5])
                if trend < -200:  # Sustained withdrawals
                    return "BULLISH"
                elif trend > 200:  # Sustained injections
                    return "BEARISH"
        
        return "NEUTRAL"
    
    def _get_trend(self, records: list) -> float:
        """
        Calculate cumulative injection/withdrawal over recent weeks.
        
        Args:
            records: List of recent storage records (sorted newest first)
            
        Returns:
            Cumulative net change in BCF
        """
        if len(records) < 2:
            return 0.0
        
        total_change = 0.0
        for i in range(len(records) - 1):
            current = float(records[i]["value"])
            previous = float(records[i + 1]["value"])
            total_change += (current - previous)
        
        return total_change
    
    def get_storage_context_str(self, data: Dict[str, Any]) -> str:
        """
        Format storage data as a human-readable string for logging/LLM context.
        
        Args:
            data: Output from get_storage_data()
            
        Returns:
            Formatted string: "Storage: XXXX BCF (±X%), Bias: BULLISH/BEARISH/NEUTRAL"
        """
        if "error" in data:
            return f"⚠️ EIA Storage unavailable: {data.get('reason', 'Unknown error')}"
        
        direction = "↑" if data["injection_rate"] >= 0 else "↓"
        return (
            f"📊 EIA Storage: {data['storage_bcf']:.0f} BCF "
            f"{direction} {abs(data['injection_rate']):.1f} BCF "
            f"({data['pct_change']:+.1f}%) → Bias: {data['storage_bias']}"
        )


# Quick self-test
if __name__ == "__main__":
    import asyncio
    
    async def test():
        # Replace with your EIA API key
        agent = StorageAgent(api_key="YOUR_EIA_API_KEY")
        result = await agent.get_storage_data()
        print(agent.get_storage_context_str(result))
        print(f"Full data: {result}")
    
    asyncio.run(test())
