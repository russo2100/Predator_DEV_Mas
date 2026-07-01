import feedparser
import ssl

# SSL check removed


class NewsReader:
    def __init__(self):
        self.sources = [
            "https://www.kommersant.ru/RSS/section-finance.xml",
            "https://vedomosti.ru/rss/rubric/finance",
            "https://1prime.ru/export/rss.xml"
        ]

    def get_latest_news(self, limit=5) -> str:
        """Собирает заголовки последних новостей в одну строку"""
        headlines = []
        print("📰 Читаю новости...")

        for url in self.sources:
            try:
                feed = feedparser.parse(url)
                # Берем по 2 свежие новости с каждого источника
                for entry in feed.entries[:2]:
                    headlines.append(f"- {entry.title}")
            except Exception as e:
                print(f"Ошибка чтения {url}: {e}")

        # Ограничиваем итоговый список
        return "\n".join(headlines[:limit])


# Тест
if __name__ == "__main__":
    reader = NewsReader()
    print(reader.get_latest_news())
