import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
import httpx

# --- НАСТРОЙКИ ФИЛЬТРОВ ---
# Газовые и инфраструктурные триггеры
NG_KEYWORDS = ["natural gas", "lng", "eia", "henry hub", "pipeline", "export", "freeport", "inventory"]
# Геополитические триггеры (особенно для Ближнего Востока и судоходства)
GEO_KEYWORDS = ["iran", "israel", "middle east", "strike", "sanctions", "war", "blockade", "red sea", "houthis"]
# Погодные триггеры
WEATHER_KEYWORDS = ["freeze", "hurricane", "winter storm", "extreme cold", "outage"]

# --- ИСТОЧНИКИ ДАННЫХ ---
SOURCES = [
    {
        "name": "Yahoo Finance (NG Futures)",
        "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NG=F",
        "type": "rss",
        "keywords": []  # Берем все, т.к. фид строго привязан к тикеру NG
    },
    {
        "name": "OilPrice (Energy)",
        "url": "https://oilprice.com/rss/main",
        "type": "rss",
        "keywords": NG_KEYWORDS
    },
    {
        "name": "Global Geopolitics",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "type": "rss",
        "keywords": GEO_KEYWORDS + ["oil", "gas"]
    }
]

# API Погоды (Национальная метеослужба США - NOAA)
NWS_API_URL = "https://api.weather.gov/alerts/active"

async def fetch_rss(client: httpx.AsyncClient, source: dict) -> list:
    """Асинхронный парсинг RSS ленты с фильтрацией по ключевым словам."""
    results = []
    try:
        # Используем современный httpx (без устаревшего requests)
        resp = await client.get(source["url"], timeout=15.0)
        resp.raise_for_status()
        
        # Парсим XML DOM
        root = ET.fromstring(resp.text)
        
        for item in root.findall(".//item"):
            title = item.findtext("title") or ""
            desc = item.findtext("description") or ""
            pub_date = item.findtext("pubDate") or ""
            
            text_to_check = (title + " " + desc).lower()
            
            # Проверка фильтров
            if not source["keywords"] or any(k in text_to_check for k in source["keywords"]):
                # Очистка HTML-тегов из описания (базовая)
                clean_desc = desc.replace("<p>", "").replace("</p>", "").replace("<br />", "")
                results.append(f"[{source['name']}] {pub_date} | {title} - {clean_desc[:200]}...")
                
    except Exception as e:
        print(f"⚠️ Ошибка при парсинге {source['name']}: {e}")
        
    return results[:5]  # Отдаем топ-5 самых свежих новостей из источника

async def fetch_weather_alerts(client: httpx.AsyncClient) -> list:
    """Асинхронный сбор штормовых предупреждений NOAA для ключевых штатов."""
    results = []
    # Официальное требование NWS API - указывать уникальный User-Agent
    headers = {"User-Agent": "PredatorBot_WeatherParser/2.0 (contact@example.com)"}
    
    # TX = Texas (Добыча/LNG экспорт), IL = Illinois (Чикаго - потребление), LA = Louisiana (Henry Hub)
    areas = ["TX", "IL", "LA"]
    
    for area in areas:
        try:
            resp = await client.get(f"{NWS_API_URL}?area={area}", headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                
                for feature in features:
                    props = feature.get("properties", {})
                    severity = props.get("severity", "")
                    event = props.get("event", "")
                    headline = props.get("headline", "")
                    
                    # Ловим только суровые алерты (заморозки, ураганы)
                    if severity in ["Extreme", "Severe"] and any(w in event.lower() for w in WEATHER_KEYWORDS):
                        results.append(f"🚨 [NWS WEATHER ALERT - {area}] {event}: {headline}")
        except Exception as e:
            print(f"⚠️ Ошибка погоды NWS ({area}): {e}")
            
    return results

async def main():
    print("🚀 Запуск NG News Parser v2.0...")
    news_items = []
    
    # Пул подключений с follow_redirects для надежности
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # 1. Параллельный сбор RSS (Фундаментал + Геополитика)
        tasks = [fetch_rss(client, s) for s in SOURCES]
        rss_results = await asyncio.gather(*tasks)
        for res in rss_results:
            news_items.extend(res)
            
        # 2. Сбор погодных алертов NOAA
        weather_alerts = await fetch_weather_alerts(client)
        news_items.extend(weather_alerts)
        
    # Формируем итоговый текстовый отчет для LLM
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S MSK")
    output_text = f"=== PREDATOR NG NEWS CONTEXT ===\nGenerated: {timestamp}\n\n"
    
    if not news_items:
        output_text += "No significant natural gas news, geopolitical triggers, or weather alerts found at this time.\n"
    else:
        for i, item in enumerate(news_items, 1):
            output_text += f"{i}. {item}\n\n"
            
    # Сохраняем в корень проекта (где работает main.py)
    # Скрипт лежит в scripts/automation/, значит корень на 2 уровня выше
    project_root = Path(__file__).resolve().parent.parent.parent
    news_file = project_root / "news_fire.txt"
    
    # Принудительная запись (создаст файл или перезапишет, обновив mtime)
    with open(news_file, "w", encoding="utf-8") as f:
        f.write(output_text)
        
    print(f"✅ Успешно собрано {len(news_items)} триггеров. Файл обновлен: {news_file}")

if __name__ == "__main__":
    # Запуск асинхронного цикла
    asyncio.run(main())
