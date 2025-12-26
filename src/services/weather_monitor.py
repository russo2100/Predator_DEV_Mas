import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple


class SynopticMonitor:
    """
    Weather monitor for Natural Gas trading.

    Sources:
    - Open-Meteo forecast (fast, no-key) for temperatures (fallback/primary temps).
    - NOAA/NWS api.weather.gov for ACTIVE alerts (official, free).
      NWS requires a User-Agent header identifying the app. [web:201][web:203]

    Output is designed to be stable for LLM prompts and for rule-based scoring.
    """

    def __init__(
        self,
        user_agent: str = "PredatorBot/2.0 (contact: your_email@example.com)",
        timeout_s: float = 10.0,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.timeout_s = timeout_s
        self.user_agent = user_agent

        # ---------- Key demand regions ----------
        # Henry Hub is the pricing hub, but demand sensitivity is often strongest in NE/Midwest.
        # You can tune these points/weights later.
        self.locations: Dict[str, Dict[str, float]] = {
            "henry_hub": {"lat": 29.95, "lon": -90.07, "weight": 0.10},
            "nyc": {"lat": 40.71, "lon": -74.01, "weight": 0.45},
            "chicago": {"lat": 41.88, "lon": -87.63, "weight": 0.35},
            "dallas": {"lat": 32.78, "lon": -96.80, "weight": 0.10},
        }

        # ---------- Endpoints ----------
        self.open_meteo_url = "https://api.open-meteo.com/v1/forecast"
        self.nws_api_base = "https://api.weather.gov"

        # Simple in-memory cache for NWS alerts to reduce request rate.
        self._alerts_cache: Dict[str, Any] = {"ts": 0.0, "data": None, "ttl_s": 600.0}

    # -------------------------
    # Public API
    # -------------------------
    async def get_weather_impact(self) -> Dict[str, Any]:
        """
        Returns:
          {
            "temps_min": {name: float},
            "impact_score": float,
            "is_extreme": bool,
            "alerts": {
                "by_location": {name: {...}},
                "summary": {"alerts_count": int, "has_severe": bool}
            },
            "source": "Open-Meteo (temps) + NWS (alerts)",
            "timestamp_utc": "...ISO..."
          }
        """
        result: Dict[str, Any] = {
            "temps_min": {},
            "impact_score": 0.0,
            "is_extreme": False,
            "alerts": {"by_location": {}, "summary": {"alerts_count": 0, "has_severe": False}},
            "source": "Open-Meteo (temps) + NWS (alerts)",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        # 1) Temperatures (Open-Meteo)
        temps_min, temps_err = await self._fetch_open_meteo_min_temps()
        if temps_err:
            # Keep previous behavior: fail soft with neutral score.
            self.logger.error(f"SynopticMonitor temps error: {temps_err}")
            result["error"] = f"temps_error: {temps_err}"
            result["temps_min"] = {"henry_hub": 15.0}
            return result

        result["temps_min"] = temps_min

        # 2) Alerts (NWS) - cached
        alerts_data = await self._fetch_nws_alerts_cached()
        result["alerts"] = alerts_data

        # 3) Impact scoring (simple, explainable)
        impact_score, is_extreme = self._score_impact(temps_min, alerts_data)
        result["impact_score"] = impact_score
        result["is_extreme"] = is_extreme

        return result

    def get_weather_context_str(self, data: Dict[str, Any]) -> str:
        """
        Prompt-friendly, deterministic string.
        """
        if not data or "error" in data and "temps_min" not in data:
            return "Метеоданные временно недоступны."

        temps = data.get("temps_min") or {}
        alerts = (data.get("alerts") or {}).get("summary") or {}
        alerts_count = int(alerts.get("alerts_count", 0) or 0)
        has_severe = bool(alerts.get("has_severe", False))

        def fmt_temp(name: str) -> str:
            v = temps.get(name)
            if v is None:
                return f"{name}: n/a"
            return f"{name}: {v:.1f}°C"

        status = "ЭКСТРЕМАЛЬНЫЙ РИСК" if data.get("is_extreme") else "Норма"
        sev_txt = "YES" if has_severe else "NO"

        # Short list of top alert headlines (max 2) across all locations
        headlines: List[str] = []
        by_loc = (data.get("alerts") or {}).get("by_location") or {}
        for loc_name, loc_alerts in by_loc.items():
            for a in (loc_alerts.get("top_alerts") or [])[:2]:
                hl = a.get("headline") or a.get("event")
                if hl:
                    headlines.append(f"{loc_name}:{hl}")
            if len(headlines) >= 2:
                break
        headlines_txt = "; ".join(headlines[:2]) if headlines else "none"

        impact_pct = float(data.get("impact_score", 0.0)) * 100.0

        return (
            "ПОГОДА (NG Regions): "
            f"{fmt_temp('nyc')}, {fmt_temp('chicago')}, {fmt_temp('henry_hub')} | "
            f"Alerts: {alerts_count} (Severe={sev_txt}) | "
            f"Status: {status} | "
            f"Impact: {impact_pct:.1f}% | "
            f"Top: {headlines_txt}"
        )

    # -------------------------
    # Open-Meteo temps
    # -------------------------
    async def _fetch_open_meteo_min_temps(self) -> Tuple[Dict[str, float], Optional[str]]:
        """
        Fetch min temperature for "tomorrow" (daily min index [1]) for each location.
        """
        temps_min: Dict[str, float] = {}
        headers = {"Accept": "application/json"}

        async with httpx.AsyncClient(timeout=self.timeout_s, headers=headers) as client:
            for name, p in self.locations.items():
                params = {
                    "latitude": p["lat"],
                    "longitude": p["lon"],
                    "daily": "temperature_2m_min",
                    "forecast_days": 3,
                    "timezone": "America/Chicago",
                }
                try:
                    r = await client.get(self.open_meteo_url, params=params)
                    r.raise_for_status()
                    j = r.json()
                    mins = j.get("daily", {}).get("temperature_2m_min", [])
                    if not mins or len(mins) < 2:
                        return {}, f"Open-Meteo: missing daily.temperature_2m_min for {name}"
                    temps_min[name] = float(mins[1])
                except Exception as e:
                    return {}, f"Open-Meteo fetch failed for {name}: {e}"

        return temps_min, None

    # -------------------------
    # NWS alerts (official)
    # -------------------------
    async def _fetch_nws_alerts_cached(self) -> Dict[str, Any]:
        """
        Cache alerts for TTL seconds to reduce request load.
        """
        now_ts = datetime.now(timezone.utc).timestamp()
        cache_ts = float(self._alerts_cache.get("ts", 0.0) or 0.0)
        ttl_s = float(self._alerts_cache.get("ttl_s", 600.0) or 600.0)
        if self._alerts_cache.get("data") is not None and (now_ts - cache_ts) < ttl_s:
            return self._alerts_cache["data"]

        data = await self._fetch_nws_alerts()
        self._alerts_cache["ts"] = now_ts
        self._alerts_cache["data"] = data
        return data

    async def _fetch_nws_alerts(self) -> Dict[str, Any]:
        """
        Pull active alerts for each location using:
        https://api.weather.gov/alerts/active?point=lat,lon [web:201][web:236]
        NWS requires User-Agent header. [web:201][web:203]
        """
        headers = {
            "Accept": "application/geo+json",
            "User-Agent": self.user_agent,  # required/recommended [web:201][web:203]
        }

        by_location: Dict[str, Any] = {}
        total_alerts = 0
        has_severe_any = False

        async with httpx.AsyncClient(timeout=self.timeout_s, headers=headers) as client:
            for name, p in self.locations.items():
                lat = p["lat"]
                lon = p["lon"]
                url = f"{self.nws_api_base}/alerts/active"
                params = {"point": f"{lat},{lon}"}
                try:
                    r = await client.get(url, params=params)
                    r.raise_for_status()
                    j = r.json()
                    loc_alerts = self._parse_nws_alerts(j)
                    by_location[name] = loc_alerts
                    total_alerts += int(loc_alerts.get("alerts_count", 0) or 0)
                    has_severe_any = has_severe_any or bool(loc_alerts.get("has_severe", False))
                except Exception as e:
                    # Fail soft per-location
                    self.logger.warning(f"NWS alerts error for {name}: {e}")
                    by_location[name] = {
                        "alerts_count": 0,
                        "has_severe": False,
                        "top_alerts": [],
                        "error": str(e),
                        "source": "NWS API",
                    }

        return {
            "by_location": by_location,
            "summary": {"alerts_count": total_alerts, "has_severe": has_severe_any},
            "source": "NWS API",
        }

    @staticmethod
    def _parse_nws_alerts(payload: Dict[str, Any]) -> Dict[str, Any]:
        features = payload.get("features") or []
        alerts_count = len(features)

        top: List[Dict[str, Any]] = []
        has_severe = False

        for f in features[:50]:
            props = f.get("properties") or {}
            event = props.get("event") or ""
            severity = (props.get("severity") or "").strip()
            headline = props.get("headline") or ""
            expires = props.get("expires") or ""
            effective = props.get("effective") or ""

            # Heuristic: treat "Severe"/"Extreme" severity as severe; keep it simple and explainable.
            if severity in {"Severe", "Extreme"}:
                has_severe = True

            # Keep short list for prompt/debug
            if len(top) < 3:
                top.append(
                    {
                        "event": event,
                        "severity": severity,
                        "headline": headline,
                        "effective": effective,
                        "expires": expires,
                    }
                )

        return {
            "alerts_count": alerts_count,
            "has_severe": has_severe,
            "top_alerts": top,
            "source": "NWS API",
        }

    # -------------------------
    # Scoring
    # -------------------------
    def _score_impact(self, temps_min: Dict[str, float], alerts_data: Dict[str, Any]) -> Tuple[float, bool]:
        """
        Simple NG-oriented scoring:
        - colder in NYC/Chicago increases score (heating demand),
        - very warm reduces score,
        - severe alerts add risk premium.

        Returns: (impact_score in [-1..+1], is_extreme)
        """
        # Base from temps: piecewise linear per location, then weighted sum.
        score = 0.0
        is_extreme = False

        for name, p in self.locations.items():
            w = float(p.get("weight", 0.0) or 0.0)
            t = temps_min.get(name)
            if t is None:
                continue

            # Temperature -> local score
            # These thresholds are intentionally simple; tune later with backtests.
            local = 0.0
            if name in {"nyc", "chicago"}:
                # Strongest demand sensitivity
                if t <= -15:
                    local = 0.9
                    is_extreme = True
                elif t <= -5:
                    local = 0.6
                elif t <= 5:
                    local = 0.35
                elif t >= 20:
                    local = -0.25
            else:
                # HenryHub/South: weaker direct heating signal, but still matters
                if t <= 0:
                    local = 0.6
                    is_extreme = True
                elif t <= 10:
                    local = 0.25
                elif t >= 25:
                    local = -0.20

            score += w * local

        # Add alert premium (risk)
        summary = (alerts_data or {}).get("summary") or {}
        has_severe = bool(summary.get("has_severe", False))
        alerts_count = int(summary.get("alerts_count", 0) or 0)

        if has_severe:
            score += 0.15
            is_extreme = True
        elif alerts_count > 0:
            score += 0.05

        # Clamp to [-1..+1]
        score = max(-1.0, min(1.0, score))
        return score, is_extreme
