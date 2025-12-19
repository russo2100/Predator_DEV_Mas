import pandas as pd
from src.tools.technical_analysis import TechnicalAnalysis

# Грузим наши тестовые данные
df = pd.read_csv("data/test_market_data.csv")
print("Исходные данные:", df.shape)

# Прогоняем через новый индикатор
df_analyzed = TechnicalAnalysis.add_indicators(df)

# Смотрим результат
print(df_analyzed[['time', 'close', 'Kalman_Price', 'SMA_50']].tail(10))
