import httpx
from src.config.settings import settings


class TelegramNotifier:
    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send_message(self, message: str):
        """Отправляет сообщение в Telegram"""
        try:
            async with httpx.AsyncClient() as client:
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "Markdown"
                }
                await client.post(self.base_url, json=payload)
        except Exception as e:
            print(f"⚠️ Ошибка отправки в Telegram: {e}")


# Тест
if __name__ == "__main__":
    import asyncio

    async def test():
        notifier = TelegramNotifier()
        await notifier.send_message("🤖 **Predator Agent**: Система связи активна.")
        print("Сообщение отправлено.")

    asyncio.run(test())
