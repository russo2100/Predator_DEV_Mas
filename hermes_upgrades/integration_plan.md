# План Интеграции и Синхронизации Hermes Upgrades в Predator

Этот документ описывает, как новые модули (Календарный инжектор, Симулятор проскальзывания, Исторические паттерны) и **инсайдерские правила торговли газом** будут вплетены в существующую архитектуру `Predator_DEV_Mas`.

## 1. Интеграция Calendar Injector (Календарный Инжектор)
**Цель:** Динамически изменять агрессивность бота и ширину стопов (ATR) в зависимости от текущего сезона газа (Зима, Лето, Межсезонье).
**Целевой файл в Predator:** `src/strategy/gwdd_engine.py` и `src/scanner.py`.

**Код интеграции (в `scanner.py`):**
```python
from hermes_upgrades.calendar_injector import CalendarInjector

injector = CalendarInjector()

def on_new_signal(base_long_score, base_short_score):
    # Корректируем базовые скоры перед принятием решения
    adj_long, adj_short, vol_multiplier = injector.adjust_gwdd_scores(base_long_score, base_short_score)
    
    # Расширяем/сужаем ATR стопы на основе множителя волатильности
    dynamic_atr_stop = base_atr * vol_multiplier
    
    if adj_long > THRESHOLD:
        execute_long(stop_loss=dynamic_atr_stop)
```

## 2. Интеграция Slippage Simulator (Симулятор Проскальзывания)
**Цель:** Отбраковывать сделки, где потенциальный тейк-профит слишком мал и будет съеден комиссией брокера и спредом.
**Целевой файл в Predator:** `src/execution/order_router.py`.

**Код интеграции (в модуле исполнения):**
```python
from hermes_upgrades.slippage_simulator import SlippageSimulator

simulator = SlippageSimulator(broker_fee_pct=0.0004, avg_spread_pts=0.002)

def before_trade_execution(price, volume, take_profit):
    sim_result = simulator.simulate_trade_costs(price, volume, take_profit)
    
    if not sim_result["is_viable"]:
        log.warning(f"Отмена: Профит съедается комиссией. Удержание: {sim_result['profit_retention_pct']}%")
        return False
        
    return True
```

## 3. Интеграция Historical Patterns (Исторические Паттерны)
**Цель:** При формировании дневного "настроения" (Daily Bias) бот должен заглядывать в историю за последние 5 лет на этот же месяц, чтобы избегать глупых шортов на исторических минимумах весной.
**Целевой файл в Predator:** `src/agents/planner_agent.py`.

## 4. ИНТЕГРАЦИЯ ИНСАЙДЕРСКИХ ПРАВИЛ (Отчет Разведки Scout)
Разведчик принес критические данные с форумов алготрейдеров. Их необходимо жестко закодить в ядро (Risk Agent):

### А. Эффект Четверга (EIA Blackout)
**Событие:** Отчет EIA выходит каждый четверг в 17:30 (или 18:30) по МСК. В эту секунду спреды разрывает, ликвидность пропадает, алгоритмы уничтожаются.
**Код синхронизации (Risk Agent):**
```python
import datetime

def is_eia_blackout(current_time):
    # Четверг = 3 (0-Monday, 3-Thursday)
    if current_time.weekday() == 3:
        # Проверяем окно 17:25 - 17:40 МСК (нужна корректировка на летнее время)
        eia_time = datetime.time(17, 30)
        # Если время внутри +/- 10 минут от EIA - жестко глушим торги
        if abs(current_time.minute - eia_time.minute) <= 10 and current_time.hour == eia_time.hour:
            return True
    return False
```

### Б. Окно Ликвидности (Core Trading Hours)
**Событие:** Газ ("Вдоводел") опасен на Азиатской сессии из-за ложных пробоев и сбора стопов.
**Код синхронизации (Scanner):**
```python
def is_core_liquidity_window(current_time):
    # Лучшее время: 16:00 - 21:30 МСК (совпадает с 9:00am - 2:30pm ET)
    if 16 <= current_time.hour <= 21:
        return True
    return False # Вне этого окна бот торгует только на X2 завышенных требованиях к сигналу
```

### В. Запрет на Пробойные Стратегии (Breakout Traps)
**Событие:** Технические пробои уровней поддержки/сопротивления на газе часто ложные (Stop-hunting). 
**Синхронизация:** В `GWDD` движке необходимо **отключить** или обнулить вес технических паттернов "пробоя" и повысить веса осцилляторов возврата к среднему (Mean Reversion), когда актив ушел слишком далеко от средних значений без новостного фона.
