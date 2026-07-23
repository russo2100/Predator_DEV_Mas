import pandas as pd
import numpy as np

class HistoricalPatternBacktester:
    """
    Ищет исторически схожие паттерны в межсезонье (например, как вел себя газ в аналогичные периоды прошлых лет).
    """
    def __init__(self, historical_data_path=None):
        self.historical_data_path = historical_data_path
        
    def mock_historical_data(self):
        # Генерация мок-данных, в проде заменить на реальный датафрейм D1 свечей
        dates = pd.date_range(start='2020-01-01', periods=1500, freq='D')
        prices = np.random.normal(loc=2.5, scale=0.5, size=1500)
        return pd.DataFrame({'date': dates, 'close': prices})

    def find_similar_shoulder_months(self, current_month, lookback_years=5):
        """
        Сравнивает текущий месяц с закрытием этого же месяца в прошлые годы.
        Полезно для оценки вероятности разворота на исторических минимумах.
        """
        df = self.mock_historical_data()
        df['month'] = df['date'].dt.month
        df['year'] = df['date'].dt.year
        
        target_df = df[df['month'] == current_month]
        
        results = {}
        for year in target_df['year'].unique()[-lookback_years:]:
            year_data = target_df[target_df['year'] == year]
            if not year_data.empty:
                start_price = year_data.iloc[0]['close']
                end_price = year_data.iloc[-1]['close']
                change_pct = ((end_price - start_price) / start_price) * 100
                results[str(year)] = {
                    "start_price": round(start_price, 3),
                    "end_price": round(end_price, 3),
                    "change_pct": round(change_pct, 2)
                }
                
        # Агрегация вероятности бычьего исхода
        bullish_years = sum(1 for v in results.values() if v["change_pct"] > 0)
        total_years = len(results)
        bullish_probability = (bullish_years / total_years * 100) if total_years > 0 else 50
        
        return {
            "historical_stats": results,
            "bullish_probability_pct": bullish_probability
        }
