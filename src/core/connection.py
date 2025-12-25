import asyncio
from t_tech.invest import AsyncClient, Client
from tinkoff.invest.services import Services
from src.config.settings import settings


async def check_connection():
    """
    Проверяет соединение с API Тинькофф.
    Возвращает список счетов пользователя.
    """
    token = settings.TINKOFF_TOKEN.get_secret_value()

    print(f"🔌 Подключение к API... (Sandbox: {settings.SANDBOX_MODE})")

    # Используем AsyncClient для асинхронной работы (важно для Python 3.13)
    try:
        async with AsyncClient(token) as client:
            # Получаем счета (в песочнице или боевые)
            if settings.SANDBOX_MODE:
                accounts = await client.sandbox.get_sandbox_accounts()
                print("✅ Успешное подключение к SANDBOX!")
            else:
                accounts = await client.users.get_accounts()
                print("✅ Успешное подключение к БОЕВОМУ контуру!")

            # Выводим информацию о счетах
            for acc in accounts.accounts:
                print(f"   🆔 Счет ID: {acc.id}")
                print(f"   🏷 Название: {acc.name}")
                print(f"   🔒 Статус: {acc.status}")
                print("   ---")

            return accounts.accounts

    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return None

if __name__ == "__main__":
    # Запуск проверки при прямом вызове файла
    asyncio.run(check_connection())
