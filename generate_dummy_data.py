import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Создаем папку data если нет
import os
os.makedirs("data", exist_ok=True)


def generate_gas_data():
    # Симулируем 3 дня торгов (минутные свечи)
    dates = pd.date_range(start="2025-12-01 10:00:00",
                          periods=1000, freq="1min")

    # Генерируем цену: Случайное блуждание + Тренд
    np.random.seed(42)
    returns = np.random.normal(
        loc=0.0002, scale=0.001, size=1000)  # Легкий тренд вверх
    price_path = 3.500 * (1 + np.cumsum(returns))

    df = pd.DataFrame({
        "time": dates,
        "open": price_path,
        "high": price_path + 0.002,
        "low": price_path - 0.002,
        # Шум на закрытии
        "close": price_path + np.random.normal(0, 0.001, 1000),
        "volume": np.random.randint(10, 500, 1000)
    })

    # Сохраняем
    df.to_csv("data/test_market_data.csv", index=False)
    print("✅ Тестовые данные 'data/test_market_data.csv' созданы!")


if __name__ == "__main__":
    generate_gas_data()
