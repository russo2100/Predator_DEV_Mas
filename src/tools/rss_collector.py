import feedparser
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config.settings import settings

class NewsCollector:
    """
    Умный Агрегатор Новостей.
    1. Собирает RSS.
    2. Анализирует их через LLM (Gemini), выдавая сжатый контекст.
    """
    def __init__(self):
        self.sources = [
            "https://oilprice.com/rss/main",
            "https://ru.investing.com/rss/commodities_Natural_Gas.rss",
            "https://finance.yahoo.com/rss/industry/energy",
            
            # СПЕЦИАЛИЗИРОВАННЫЕ (Погода и запасы - Драйверы!)
            "https://www.eia.gov/naturalgas/weekly/rss/ngweekly.xml", # Запасы США (EIA) - Критично!
            "https://rss.app/feeds/v1.1/t3k5Kz8S7q0sX1y.json", # (Пример, лучше найти прямой RSS NatGasWeather, если есть, или использовать ключевые слова "Weather" жестче)
        ]
        # Используем умную модель (Risk/Planner model), так как вызовов мало
        self.llm = ChatOpenAI(
            model=settings.AI_MODEL_RISK, 
            temperature=0,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL
        )

    def get_market_sentiment_news(self) -> str:
        print("🌍 [RSS] Сбор заголовков (Расширенный поиск)...")
        raw_news = []
        
        # Ключевые слова-триггеры (Погода, Запасы, Цены)
        keywords = [
            'gas', 'energy', 'lng', 'газ',   # Основные
            'weather', 'cold', 'warm', 'forecast', 'noaa', # Погода (Драйвер №1)
            'storage', 'withdrawal', 'injection', 'eia',   # Запасы (Драйвер №2)
            'blast', 'storm', 'freeze'       # Экстремальная погода
        ]

        for url in self.sources:
            try:
                feed = feedparser.parse(url)
                # Увеличили лимит просмотра до 15 заголовков с каждой ленты
                for entry in feed.entries[:15]: 
                    title = entry.title
                    link = entry.link
                    
                    # Проверяем, есть ли хоть одно ключевое слово в заголовке
                    if any(w in title.lower() for w in keywords):
                        # Добавляем в список
                        raw_news.append(f"- {title}")
                        
            except Exception as e:
                print(f"⚠️ Ошибка RSS {url}: {e}")

        # Убираем дубликаты (set) и ограничиваем итоговый список (чтобы не перегрузить ИИ)
        unique_news = list(set(raw_news))
        
        if not unique_news:
            return "Нет важных новостей."

        # Берем топ-10 самых свежих (они обычно сверху)
        final_list = unique_news[:10]

        print(f"🧠 [AI News] Анализирую {len(final_list)} заголовков...")
        return self._summarize_with_ai("\n".join(final_list))


    def _summarize_with_ai(self, raw_text: str) -> str:
        template = """
        SYSTEM: You are a Market Sentiment Analyzer for Natural Gas (NG).
        Summarize the provided headlines into a concise market context for another AI Agent.
        
        HEADLINES:
        {text}
        
        OUTPUT FORMAT:
        SENTIMENT: [BULLISH/BEARISH/NEUTRAL]
        KEY_FACTORS: [List key drivers: Weather, Stocks, Geopolitics]
        SUMMARY: [Concise 1-2 sentences explanation in English]
        """
        
        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | self.llm
        
        try:
            response = chain.invoke({"text": raw_text})
            return str(response.content)
        except Exception as e:
            print(f"⚠️ News AI Error: {e}")
            return "NO_DATA"


if __name__ == "__main__":
    c = NewsCollector()
    print("\n📰 ИТОГ:\n" + c.get_market_sentiment_news())
