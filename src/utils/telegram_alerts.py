import aiohttp
import asyncio
from datetime import datetime
import os
from typing import Optional

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ALERT_ENABLED = os.getenv("TELEGRAM_ALERT_ENABLED", "true").lower() == "true"

class TelegramAlerts:
    """Alert system for Predator Bot via Telegram"""
    
    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.timeout = aiohttp.ClientTimeout(total=5)
    
    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send message to Telegram chat"""
        if not self.enabled or not self.bot_token or not self.chat_id:
            print(f"⚠️ TG Alert disabled or not configured")
            return False
        
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                }
                async with session.post(
                    f"{self.base_url}/sendMessage",
                    json=payload
                ) as resp:
                    return resp.status == 200
        except asyncio.TimeoutError:
            print("⏱️ Telegram timeout")
            return False
        except Exception as e:
            print(f"❌ Telegram error: {e}")
            return False
    
    async def send_alert(
        self,
        error_type: str,
        error_msg: str,
        position_status: str = "",
        cycle: int = 0,
    ) -> None:
        """Send critical alert to Telegram"""
        
        emoji_map = {
            "CONNECTION_ERROR": "🌐",
            "API_ERROR": "⚠️",
            "LOGIC_ERROR": "💥",
            "CRASH": "🔴",
            "RECOVERY": "✅",
        }
        
        emoji = emoji_map.get(error_type, "❌")
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        message = f"""
{emoji} <b>PREDATOR ALERT</b>

<b>Type:</b> {error_type}
<b>Time:</b> {timestamp}
<b>Cycle:</b> {cycle}

<b>Error:</b>
<code>{error_msg[:500]}</code>

<b>Position:</b>
{position_status or "No open position"}

⚙️ <i>Auto-restart in progress...</i>
"""
        
        success = await self.send_message(message)
        if success:
            print(f"✅ Alert sent to Telegram")
        else:
            print(f"⚠️ Failed to send Telegram alert")
    
    async def send_healthcheck(
        self,
        price: float,
        rsi: float,
        trend: str,
        bias: str,
        position_info: str = "",
        cycle: int = 0,
    ) -> None:
        """Send periodic healthcheck"""
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        message = f"""
✅ <b>PREDATOR HEALTHCHECK</b>

<b>Time:</b> {timestamp}
<b>Cycle:</b> {cycle}

<b>Market Data:</b>
• Price: {price:.3f}
• RSI: {rsi:.1f}
• Trend: {trend}
• BIAS: {bias}

<b>Position:</b>
{position_info or "FLAT"}

Status: 🟢 RUNNING
"""
        
        await self.send_message(message)


# Global instance
tg_alerts = TelegramAlerts(
    bot_token=TELEGRAM_BOT_TOKEN,
    chat_id=TELEGRAM_CHAT_ID,
    enabled=TELEGRAM_ALERT_ENABLED,
)
