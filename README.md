# 🤖 PREDATOR DEV MAS - Advanced Multi-Agent Trading System

[![Docker Image CI](https://github.com/russo2100/PredatorDEVMas/actions/workflows/docker-image.yml/badge.svg)](https://github.com/russo2100/PredatorDEVMas/actions/workflows/docker-image.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Tinkoff API](https://img.shields.io/badge/Tinkoff-Invest_API-orange.svg)](https://tinkoff.github.io/invest-api/)
[![LangChain](https://img.shields.io/badge/LangChain-Enabled-green.svg)](https://python.langchain.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Продвинутая мультиагентная торгово-аналитическая система** с гибридным AI/ML подходом для автоматизированной торговли на финансовых рынках через Tinkoff Invest API.

---

## 🌟 Ключевые особенности

### 🤖 Многоагентная архитектура
- **Байесовский движок** - вероятностная оценка рыночных состояний
- **Синоптический монитор** - макроанализ рыночных условий
- **Планировщик стратегий** - адаптивное управление торговлей
- **Агент управления рисками** - многоуровневая система защиты

### 🧠 Гибридный AI/ML подход
- **KAG (Knowledge-Augmented Generation)** - обогащённый анализ с внешними данными
- **LangChain интеграция** - интеллектуальная обработка естественного языка
- **Импульсные стратегии** (IMPULSE_UP/DOWN) - обнаружение рыночных импульсов
- **Конфиденциальные решения** (HOLD+Conf) - управление уверенностью в действиях

### 📊 Расширенный анализ данных
- **Фильтр Калмана** - продвинутое сглаживание временных рядов
- **RSI с адаптивными порогами** - динамическая настройка индикаторов
- **Сканер рынка** (`scanner.py`) - автоматический поиск возможностей
- **Анализ новостей** (NewsAPI) - интеграция фундаментального анализа

### 🔧 Производственная готовность
- **Полный CI/CD пайплайн** (GitHub Actions)
- **Модульная архитектура** с чёткими границами ответственности
- **Структурированное логирование** в формате JSON
- **Контейнеризация** с Docker Compose

---

## 🏗️ Архитектура системы

```
┌─────────────────────────────────────────────────────────────┐
│                    Многоагентная архитектура                │
│                                                            │
│  ┌─────────────────────────────────────────────────────┐  │
│  │             Когнитивный слой (AI/ML)                │  │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────┐  │  │
│  │  │ Байесовский│  │KAG система │  │ LangChain   │  │  │
│  │  │   движок   │  │(Knowledge) │  │  агент      │  │  │
│  │  └────────────┘  └────────────┘  └─────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
│                          ↓                                 │
│  ┌─────────────────────────────────────────────────────┐  │
│  │           Аналитический слой (Analysis)             │  │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────┐  │  │
│  │  │ Синоптич.  │  │Технический │  │ Фундаментал.│  │  │
│  │  │ монитор    │  │  анализ    │  │   анализ    │  │  │
│  │  └────────────┘  └────────────┘  └─────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
│                          ↓                                 │
│  ┌─────────────────────────────────────────────────────┐  │
│  │          Операционный слой (Execution)              │  │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────┐  │  │
│  │  │Планировщик │  │Управление  │  │ Исполнение  │  │  │
│  │  │ стратегий  │  │  рисками   │  │   ордеров   │  │  │
│  │  └────────────┘  └────────────┘  └─────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
│                          ↓                                 │
│  ┌─────────────────────────────────────────────────────┐  │
│  │          Интеграционный слой (Integration)          │  │
│  │  ┌────────────┐  ┌────────────┐  ┌─────────────┐  │  │
│  │  │ Tinkoff API│  │  News API  │  │Telegram/Web │  │  │
│  │  │            │  │            │  │ notifications│  │  │
│  │  └────────────┘  └────────────┘  └─────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Взаимодействие компонентов
```
Сбор данных → [Сканер] → Обработка → [Байесовский движок]
                              ↓
                 [Синоптический монитор] → Анализ → [KAG система]
                              ↓
                 [Планировщик] → Решение → [Агент рисков]
                              ↓
                 [Исполнитель] → Tinkoff API → Торговля
```

---

## 🚀 Быстрый старт

### Вариант 1: Docker (рекомендуется для production)

```bash
# 1. Клонировать репозиторий
git clone https://github.com/russo2100/PredatorDEVMas.git
cd PredatorDEVMas

# 2. Создать файл конфигурации
cp .env.example .env
nano .env  # Редактировать параметры

# 3. Запустить систему (все компоненты)
docker-compose up -d

# 4. Проверить статус
docker-compose ps
docker-compose logs -f predator-bot

# 5. Остановить систему
docker-compose down
```

### Вариант 2: Локальная установка (для разработки)

```bash
# 1. Создать виртуальное окружение
python3.12 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 2. Установить зависимости
pip install --upgrade pip
pip install -r requirements.txt

# 3. Установить дополнительные зависимости для разработки
pip install -r requirements-dev.txt

# 4. Настроить окружение
cp .env.example .env
# Добавить в .env ваши токены:
# TINKOFF_API_TOKEN, NEWSAPI_KEY, TELEGRAM_* и т.д.

# 5. Инициализировать базу знаний KAG
python -m src.tools.init_kag

# 6. Запустить основную систему
python -m src.main

# 7. Или запустить отдельные компоненты:
python -m src.scanner               # Сканер рынка
python -m src.backtest_pipeline     # Бэктестинг
python -m src.debug_market          # Отладка
```

### Вариант 3: Docker с расширенной конфигурацией

```yaml
# docker-compose.override.yml
version: "3.9"

services:
  predator-bot:
    environment:
      - STRATEGY_MODE=ADAPTIVE
      - RISK_LEVEL=MODERATE
      - ENABLE_KAG=true
    volumes:
      - ./data:/app/data
      - ./kag_knowledge:/app/docs/kag_knowledge
    ports:
      - "8080:8080"  # Веб-интерфейс (если есть)
  
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: predator_db
      POSTGRES_USER: predator_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"

volumes:
  postgres_data:
```

---

## ⚙️ Конфигурация

### Основные переменные окружения (.env)

```bash
# ===================== API КЛЮЧИ =====================
TINKOFF_API_TOKEN="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
NEWSAPI_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TELEGRAM_BOT_TOKEN="xxxxxxxxxxxxxxxxxxxxx"
TELEGRAM_CHAT_ID="xxxxxxxxxxxxxxxxxxxxx"
OPENROUTER_API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# ===================== СТРАТЕГИИ =====================
STRATEGY_MODE="ADAPTIVE"           # ADAPTIVE, AGGRESSIVE, CONSERVATIVE
IMPULSE_STRATEGY_ENABLED=true      # Включить импульсные стратегии
HOLD_CONFIDENCE_THRESHOLD=0.65     # Порог уверенности для HOLD
ENABLE_KAG=true                    # Включить KAG систему

# ===================== РИСКИ =====================
MAX_POSITION_SIZE=10
MAX_PORTFOLIO_RISK=0.02            # 2% риска на сделку
DAILY_LOSS_LIMIT=-5000.00
STOP_LOSS_PERCENT=0.03             # 3% стоп-лосс

# ===================== АНАЛИЗ =====================
RSI_PERIOD=14
RSI_OVERBOUGHT=72
RSI_OVERSOLD=28
KALMAN_Q=0.01
KALMAN_R=1.0

# ===================== ЛОГИРОВАНИЕ =====================
LOG_LEVEL="INFO"
LOG_FORMAT="json"
ENABLE_SHADOW_LOGS=true
LOG_RETENTION_DAYS=30

# ===================== СИСТЕМНЫЕ =====================
POLLING_INTERVAL=10                # Секунды между циклами
MARKET_OPEN_TIME="09:50"
MARKET_CLOSE_TIME="23:50"
TIMEZONE="Europe/Moscow"
```

### Конфигурационные файлы

```
config/
├── strategies/           # Конфигурации стратегий
│   ├── impulse.yaml     # Импульсные стратегии
│   ├── adaptive.yaml    # Адаптивные стратегии
│   └── conservative.yaml
├── risk_profiles/       # Профили риска
│   ├── aggressive.yaml
│   ├── moderate.yaml
│   └── conservative.yaml
├── agents_config.yaml   # Конфигурация агентов
└── system_config.yaml   # Системные настройки
```

---

## 📁 Структура проекта

```
PredatorDEVMas/
│
├── src/                          # Основной исходный код
│   ├── main.py                   # Точка входа системы
│   ├── scanner.py                # Сканер рыночных возможностей
│   ├── backtest_pipeline.py      # Патлайн бэктестинга
│   ├── debug_market.py           # Инструменты отладки
│   ├── data_provider.py          # Универсальный поставщик данных
│   │
│   ├── agents/                   # Многоагентная система
│   │   ├── bayesian_engine.py    # Байесовский движок
│   │   ├── synoptic_monitor.py   # Синоптический монитор
│   │   ├── strategy_planner.py   # Планировщик стратегий
│   │   ├── risk_manager.py       # Агент управления рисками
│   │   └── impulse_detector.py   # Детектор импульсов
│   │
│   ├── analysis/                 # Аналитические модули
│   │   ├── technical/            # Технический анализ
│   │   │   ├── rsi_adaptive.py   # Адаптивный RSI
│   │   │   ├── kalman_filter.py  # Фильтр Калмана
│   │   │   └── trend_analyzer.py
│   │   ├── fundamental/          # Фундаментальный анализ
│   │   │   ├── news_analyzer.py  # Анализ новостей
│   │   │   └── sentiment.py      # Анализ настроения
│   │   └── hybrid/               # Гибридный анализ
│   │       └── kag_engine.py     # KAG движок
│   │
│   ├── services/                 # Сервисы интеграции
│   │   ├── tinkoff_service.py    # Tinkoff Invest API
│   │   ├── news_service.py       # NewsAPI интеграция
│   │   ├── notification_service.py
│   │   └── data_cache.py         # Кэширование данных
│   │
│   └── tools/                    # Вспомогательные инструменты
│       ├── init_kag.py           # Инициализация KAG
│       ├── data_migrator.py      # Миграция данных
│       └── performance_monitor.py
│
├── docs/                         # Документация
│   ├── kag_knowledge/            # База знаний KAG
│   │   ├── market_patterns.md    # Паттерны рынка
│   │   ├── strategy_rules.md     # Правила стратегий
│   │   └── risk_framework.md     # Фреймворк рисков
│   ├── api/                      # API документация
│   └── architecture/             # Архитектурные схемы
│
├── data/                         # Данные и хранилища
│   ├── market_data/              # Рыночные данные
│   ├── models/                   # Сохраненные ML модели
│   └── backups/                  # Резервные копии
│
├── tests/                        # Тесты
│   ├── unit/                     # Юнит-тесты
│   ├── integration/              # Интеграционные тесты
│   ├── performance/              # Тесты производительности
│   └── fixtures/                 # Фикстуры для тестов
│
├── scripts/                      # Скрипты управления
│   ├── deploy.sh                 # Развертывание
│   ├── backup.sh                 # Резервное копирование
│   ├── health_check.sh           # Проверка здоровья
│   └── update_knowledge.sh       # Обновление базы знаний
│
├── docker/                       # Docker конфигурации
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── docker-compose.prod.yml
│   └── docker-compose.dev.yml
│
├── config/                       # Конфигурационные файлы
├── logs/                         # Логи системы
├── .github/workflows/            # CI/CD пайплайны
└── requirements/                 # Зависимости
    ├── base.txt
    ├── dev.txt
    └── prod.txt
```

---

## 📖 Использование системы

### Основные команды

```bash
# Запуск полной системы
python -m src.main --mode production --strategy adaptive

# Сканирование рыночных возможностей
python -m src.scanner --interval 5m --output opportunities.json

# Бэктестинг стратегии
python -m src.backtest_pipeline \
  --strategy impulse_up \
  --start-date 2025-01-01 \
  --end-date 2025-12-01 \
  --output report.html

# Отладка рыночных данных
python -m src.debug_market --figi BBG004730N88 --days 30

# Мониторинг производительности
python -m src.tools.performance_monitor --interval 60

# Обновление базы знаний KAG
python -m src.tools.update_knowledge --source news --limit 100
```

### Работа с агентами

```python
from src.agents.bayesian_engine import BayesianEngine
from src.agents.synoptic_monitor import SynopticMonitor

# Инициализация Байесовского движка
bayesian = BayesianEngine(
    prior_belief=0.5,
    learning_rate=0.1,
    confidence_threshold=0.65
)

# Обновление убеждений на основе новых данных
new_belief = bayesian.update(
    evidence={'rsi': 75, 'volume_spike': True},
    market_context={'trend': 'bullish'}
)

# Использование Синоптического монитора
monitor = SynopticMonitor()
market_condition = monitor.analyze({
    'vix': 18.5,
    'bond_yields': 4.2,
    'oil_price': 78.3,
    'usd_rub': 88.5
})
```

### Интеграция с KAG системой

```python
from src.analysis.hybrid.kag_engine import KAGEngine

# Инициализация KAG движка
kag = KAGEngine(knowledge_base='docs/kag_knowledge/')

# Обогащенный анализ рыночной ситуации
enhanced_analysis = kag.augment_analysis(
    technical_data={'price': 4.01, 'rsi': 68},
    context={
        'market_regime': 'volatile',
        'news_sentiment': 'negative',
        'economic_calendar': ['CPI release tomorrow']
    }
)

# Генерация торговой рекомендации с объяснением
recommendation = kag.generate_recommendation(
    analysis=enhanced_analysis,
    risk_profile='moderate',
    current_positions=[]
)
```

---

## 🧪 Тестирование

```bash
# Запуск всех тестов
pytest tests/ -v

# Тесты конкретного модуля
pytest tests/unit/test_bayesian_engine.py -v
pytest tests/integration/test_tinkoff_integration.py -v

# Тесты с покрытием кода
pytest --cov=src tests/ --cov-report=html

# Тесты производительности
pytest tests/performance/test_latency.py -v

# Запуск тестов в Docker
docker-compose -f docker-compose.test.yml up --build
```

### Структура тестов

```
tests/
├── unit/                     # Юнит-тесты отдельных компонентов
│   ├── test_bayesian_engine.py
│   ├── test_kalman_filter.py
│   ├── test_rsi_adaptive.py
│   └── test_strategy_planner.py
│
├── integration/              # Интеграционные тесты
│   ├── test_tinkoff_integration.py
│   ├── test_newsapi_integration.py
│   └── test_telegram_notifications.py
│
├── performance/              # Тесты производительности
│   ├── test_latency.py
│   ├── test_memory_usage.py
│   └── test_scalability.py
│
└── fixtures/                 # Фикстуры и тестовые данные
    ├── market_data.json
    ├── test_config.yaml
    └── mock_responses/
```

---

## 🔄 CI/CD Pipeline

### GitHub Actions Workflow

```yaml
# .github/workflows/docker-image.yml
name: Docker Image CI

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: |
          docker-compose -f docker-compose.test.yml up --build --exit-code-from tests
  
  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker image
        run: |
          docker build -t predator-mas:latest .
  
  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - name: Deploy to production
        run: |
          # Автоматический деплой на VPS
          echo "Deploying to production..."
```

### Этапы пайплайна:
1. **Тестирование** - unit и integration тесты
2. **Сборка** - создание Docker образа
3. **Сканирование безопасности** - проверка уязвимостей
4. **Деплой** - автоматическое развертывание

---

## 📊 Мониторинг и логирование

### Структура логов

```bash
logs/
├── shadowagentslog.jsonl      # Структурированные логи агентов (JSONL)
├── shadowdebug.log            # Детальные debug логи
├── trading.log               # Логи торговых операций
├── performance.log           # Логи производительности
└── errors.log               # Критические ошибки
```

### Пример JSON лога

```json
{
  "timestamp": "2025-12-26T14:30:45.123Z",
  "level": "INFO",
  "component": "bayesian_engine",
  "event": "belief_update",
  "data": {
    "prior_belief": 0.55,
    "new_evidence": {
      "rsi_overbought": true,
      "volume_spike": false,
      "news_sentiment": "negative"
    },
    "posterior_belief": 0.42,
    "confidence": 0.78,
    "recommendation": "HOLD",
    "market_context": {
      "regime": "volatile",
      "time_of_day": "afternoon",
      "weekday": "Thursday"
    }
  },
  "metadata": {
    "session_id": "sess_20251226_1430",
    "trace_id": "trace_abc123"
  }
}
```

### Мониторинг в реальном времени

```bash
# Просмотр логов агентов
tail -f logs/shadowagentslog.jsonl | jq '.'

# Поиск ошибок
grep -i "error" logs/shadowdebug.log | tail -20

# Мониторинг торговли
python scripts/trading_dashboard.py

# Проверка здоровья системы
bash scripts/health_check.sh
```

---

## 🔐 Безопасность

### Рекомендации для production

1. **Управление секретами:**
   ```bash
   # Использовать Docker Secrets или HashiCorp Vault
   docker secret create tinkoff_token ${TINKOFF_API_TOKEN}
   ```

2. **Шифрование данных:**
   ```python
   # В конфигурации
   ENABLE_DATA_ENCRYPTION=true
   ENCRYPTION_KEY=${ENCRYPTION_KEY}
   ```

3. **Контроль доступа:**
   - Использовать отдельные API ключи для каждого окружения
   - Регулярно ротировать токены
   - Ограничить IP-адреса для доступа к API

4. **Аудит:**
   - Ведение журнала всех торговых операций
   - Мониторинг подозрительной активности
   - Регулярные security review

---

## 🚀 Производительность

### Оптимизированные настройки

```yaml
# config/performance.yaml
optimizations:
  cache:
    enabled: true
    ttl_minutes: 5
    max_size_mb: 512
  
  async_processing:
    enabled: true
    max_workers: 8
    queue_size: 1000
  
  database:
    connection_pool: 20
    query_timeout: 30
  
  api_rate_limiting:
    tinkoff: 100  # запросов в минуту
    newsapi: 50   # запросов в минуту
```

### Мониторинг ресурсов

```bash
# Проверка использования памяти
docker stats predator-trading-bot

# Мониторинг сети
iftop -i eth0

# Профилирование кода
python -m cProfile -o profile.stats src/main.py
```

---

## 🤝 Вклад в проект

### Процесс разработки

1. **Форкните репозиторий**
   ```bash
   fork https://github.com/russo2100/PredatorDEVMas
   ```

2. **Создайте feature ветку**
   ```bash
   git checkout -b feature/amazing-feature
   ```

3. **Сделайте коммит изменений** (следуем Conventional Commits)
   ```bash
   git commit -m "feat: add adaptive RSI thresholds"
   # или
   git commit -m "fix: resolve memory leak in bayesian engine"
   git commit -m "docs: update KAG knowledge base"
   ```

4. **Запушьте изменения**
   ```bash
   git push origin feature/amazing-feature
   ```

5. **Откройте Pull Request**

### Конвенции коммитов

```
feat:     Новая функциональность
fix:      Исправление ошибки
docs:     Изменения в документации
style:    Форматирование, точки с запятой и т.д.
refactor: Рефакторинг кода
test:     Добавление тестов
chore:    Обновление сборки, зависимостей
perf:     Изменения, улучшающие производительность
ci:       Изменения CI/CD конфигурации
```

---

## 📚 Документация

### Полная документация

- **[Архитектура системы](docs/architecture/overview.md)** - детальное описание архитектуры
- **[API документация](docs/api/reference.md)** - руководство по API
- **[Руководство по развертыванию](docs/deployment/guide.md)** - инструкции по деплою
- **[База знаний KAG](docs/kag_knowledge/)** - обучающие материалы системы

### Внешние ресурсы

- [Tinkoff Invest API](https://tinkoff.github.io/invest-api/) - официальная документация
- [LangChain Documentation](https://python.langchain.com/) - руководство по LangChain
- [Docker Documentation](https://docs.docker.com/) - документация Docker
- [Bayesian Methods](https://bayesian.org/) - ресурсы по Байесовским методам

---

## 📄 Лицензия

Этот проект распространяется под лицензией MIT. Смотрите файл [LICENSE](LICENSE) для деталей.

---

## ⚠️ Дисклеймер

**ВАЖНО:** Торговля на финансовых рынках сопряжена с высокими рисками. Данная система предназначена для образовательных и исследовательских целей.

**ПРЕДУПРЕЖДЕНИЕ:**
- Никогда не инвестируйте деньги, которые вы не готовы потерять
- Всегда тестируйте стратегии на демо-счетах
- Постоянно мониторьте работу системы
- Имейте чёткую стратегию управления рисками
- Консультируйтесь с финансовыми советниками

Автор не несёт ответственности за любые финансовые потери, вызванные использованием этого программного обеспечения.

---

## 👨‍💻 Автор и контакты

**russo2100** - Lead Developer & Architect

### Контакты
- **GitHub**: [@russo2100](https://github.com/russo2100)
- **Проект**: [PredatorDEVMas](https://github.com/russo2100/PredatorDEVMas)
- **Документация**: [Wiki](https://github.com/russo2100/PredatorDEVMas/wiki)

### Благодарности
- **Tinkoff** за предоставление Invest API
- **LangChain team** за фреймворк AI разработки
- **Open-source сообществу** за вдохновение и инструменты
- **Тестерам и контрибьюторам** проекта

---

## 🎯 Статус проекта

**Версия:** 2.0.0-beta  
**Статус:** Активная разработка  
**Поддержка:** Python 3.12+, Docker 24+  
**Последнее обновление:** 26.12.2025  
**Следующий релиз:** Q1 2026  

### Roadmap
- [ ] Версия 2.1: Расширение KAG системы
- [ ] Версия 2.2: Поддержка криптовалют
- [ ] Версия 2.3: Веб-интерфейс управления
- [ ] Версия 3.0: Децентрализованная архитектура

---

**⭐ Если проект был полезен, поставьте звезду на GitHub и следите за обновлениями!**

[![Star History Chart](https://api.star-history.com/svg?repos=russo2100/PredatorDEVMas&type=Date)](https://star-history.com/#russo2100/PredatorDEVMas&Date)
