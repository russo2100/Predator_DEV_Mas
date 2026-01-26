import asyncio
import pandas as pd
import numpy as np
import json
import uuid
from src.utils.jsonl_enrichment import enrich_cycle_event
import re
import json
import uuid
import aiohttp
from typing import Dict, Any, Literal, Optional, Tuple
from datetime import datetime, timedelta, timezone, time
import time as time_module
from src.core.gwdd_engine import GWDDEngine, GWDDConfig
from src.agents.analyst import MarketAnalyst
from src.config.settings import settings
from src.core.pipeline import pipeline_analysis
from t_tech.invest import AsyncClient, OrderDirection, OrderType, CandleInterval, Future
from pathlib import Path
import os
from src.core.multi_agent_adapter import MultiAgentShadowAdapter
from src.agents.planner import PlannerAgent
from src.agents.risk_agent import RiskAgent
from src.tools.news_aggregator import UnifiedNewsAgent
from src.core.gwdd_engine import GWDDEngine, GWDDConfig
from src.shared_state import SharedTradingState
import datetime as dt
import pytz
from src.services.weather_monitor import SynopticMonitor
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional
import hashlib

from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
import datetime as dt
import pytz
import yaml


MOEX_TZ = pytz.timezone("Europe/Moscow")


@dataclass
class NGContract:
    code: str
    figi: str
    uid: str
    month: int
    year: int
    expiry: dt.datetime


def load_ng_contracts(config_path: str = "contracts.yaml") -> List[NGContract]:
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw.get("ng_futures", {}).get("contracts", [])
    contracts: List[NGContract] = []
    for it in items:
        expiry_str = it.get("expiry_date")
        if not expiry_str:
            continue
        expiry = dt.datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        contracts.append(
            NGContract(
                code=it["code"],
                figi=it["figi"],
                uid=it.get("uid", ""),
                month=int(it["month"]),
                year=int(it["year"]),
                expiry=expiry,
            )
        )
    return contracts


def select_active_ng_contract(contracts: List[NGContract]) -> Optional[NGContract]:
    """
    Выбираем активный контракт с учётом rollover.
    Переключение на следующий контракт за 1 день до экспирации в 18:00 (вечерний клиринг).
    """
    now = dt.datetime.now(MOEX_TZ)
    
    # Сортируем контракты по дате экспирации
    sorted_contracts = sorted(contracts, key=lambda c: c.expiry)
    
    for contract in sorted_contracts:
        # Rollover deadline: за 1 день до экспирации в 18:00 МСК
        rollover_time = contract.expiry - dt.timedelta(days=1)
        rollover_time = rollover_time.replace(hour=18, minute=0, second=0, microsecond=0)
        
        # Если ещё не наступил rollover deadline — используем этот контракт
        if now < rollover_time:
            return contract
    
    # Если все контракты просрочены — возвращаем самый дальний (или None)
    return sorted_contracts[-1] if sorted_contracts else None




def get_market_status():
    moscow_tz = pytz.timezone("Europe/Moscow")
    now = dt.datetime.now(moscow_tz)
    current_time = now.time()
    weekday = now.weekday()  # 0=Mon, 6=Sun, 5=Sat
    
    # Воскресенье - закрыто
    #if weekday == 6:
    #    return False, "Воскресенье: рынок закрыт"
    
    # Суббота: торги 09:50 - 23:50 (на час позже)
    if weekday == 5:
        start_trade = dt.time(9, 50)
        end_trade = dt.time(23, 50)
        if not (start_trade <= current_time <= end_trade):
            return False, f"Суббота: торги 09:50 - 23:50 MSK"
    else:
        # Пн-Пт: торги 08:50 - 23:50
        start_trade = dt.time(8, 50)
        end_trade = dt.time(23, 50)
        if not (start_trade <= current_time <= end_trade):
            return False, f"Вне времени торгов (08:50 - 23:50)"

    # 3. Клиринги (МСК)
    # Дневной: 14:00-14:05 | Вечерний: 18:50-19:05
    if (dt.time(14, 0) <= current_time <= dt.time(14, 5)) or \
       (dt.time(18, 50) <= current_time <= dt.time(19, 5)):
        return False, "ПАУЗА: КЛИРИНГ"

    return True, "РЫНОК ОТКРЫТ"





Action = Literal[
"NOOP",
"OPEN_LONG",
"ADD_LONG",
"CLOSE_LONG",
"OPEN_SHORT",
"ADD_SHORT",
"CLOSE_SHORT",
]

# agent_v1/src/main.py -> agent_v1
BASE_DIR = Path(__file__).resolve().parent.parent
TRADE_HISTORY_PATH = BASE_DIR / "trade_history.csv"


CHECK_INTERVAL_SEC = 60

# ========== ACTIVE CONTRACT FROM YAML ==========
NG_CONTRACTS = load_ng_contracts("src/contracts.yaml")
ACTIVE_NG_CONTRACT = select_active_ng_contract(NG_CONTRACTS)

if ACTIVE_NG_CONTRACT is None:
    raise RuntimeError("No active NG futures contract found in contracts.yaml")

ACTIVE_FIGI = ACTIVE_NG_CONTRACT.figi
ACTIVE_TICKER = ACTIVE_NG_CONTRACT.code
ACTIVE_UID = ACTIVE_NG_CONTRACT.uid


print(f"✅ Active contract: {ACTIVE_TICKER} | FIGI: {ACTIVE_FIGI} | Expiry: {ACTIVE_NG_CONTRACT.expiry.strftime('%Y-%m-%d')}")

# ========== END ACTIVE CONTRACT ==========

NEWSFILE = "news_fire.txt"

# Максимальный размер позиции в лотах (только LONG)
MAX_LOTS = 3

# ========== КОНСТАНТЫ КОМИССИЙ И ПРОФИТА ==========
COMMISSION_PER_TRADE = 0.39  # руб за операцию (вход или выход)
MIN_PROFIT_PCT = 0.35  # минимальный профит с учётом комиссий
COOLDOWN_AFTER_LOSS_MINUTES = 15  # охлаждение после убыточной сделки
COOLDOWN_AFTER_PROFIT_MINUTES = 0  # нет охлаждения после прибыли

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
last_trade_time = None
last_trade_was_profit = True  # предполагаем, что первая сделка будет прибыльной
consecutive_buy_signals = 0  # Счётчик последовательных BUY-сигналов
buy_signals_history = []     # История [confidence] за последние 5 BUY

# Forced Entry: SELL tracking
consecutive_sell_signals = 0  # Счётчик последовательных SELL-сигналов
sell_signals_history = []     # История Confidence для SELL (последние 5)
last_news_result = None      # Кэш результата NEWS_AGENT
news_cache_cycle = 0         # Номер цикла последнего обновления
last_sleep_log_time: Optional[dt.datetime] = None

# ========== SESSION ID & JSONL ==========
session_id = str(uuid.uuid4())[:8]
jsonl_path = f"shadow_agents_log_{dt.datetime.now().strftime('%Y%m%d')}.jsonl"


async def runshadowanalysisnonblocking(shadowadapter, marketdata, posdata, bias, signal, confidence, reason):
    """Shadow Mode запускается в фоне"""
    try:
        await asyncio.wait_for(
            shadowadapter.runshadowanalysis(
                marketdata=marketdata,
                positiondata=posdata,
                newscontext=bias,
                ai_signal=signal,
                ai_confidence=confidence,
                ai_reason=reason,
            ),
            timeout=15,
    )
        print("✅ Shadow Mode: анализ агентов завершён")
    except asyncio.TimeoutError:
        print("⏱️ Shadow timeout")
    except Exception as e:
        print(f"❌ Shadow Error: {e}")


# ✅ НОВАЯ ФУНКЦИЯ (добавь в начало main.py)
async def get_position_data_safe(executor, figi: str, retries: int = 3) -> dict:
    """Безопасное получение позиции с повторными попытками"""
    for attempt in range(retries):
        try:
            pos = await executor.get_position_data(figi)
            if pos is not None and pos.get("lots") is not None:
                return pos
        except Exception as e:
            print(f"⚠️ Попытка {attempt + 1}/{retries}: {e}")
        
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt)
    
    print(f"❌ Не удалось получить позицию после {retries} попыток. Считаем: 0 лотов")
    return {"lots": 0, "average_price": 0.0, "current_price": 0.0, "expected_yield": 0.0}

class PositionTimer:
    """
    Отслеживает время удержания позиции для правил:
    - Фиксация перед клирингом
    - Закрытие "висяков" > 24 часов
    - Интрадей-фиксация вне клиринга
    """

    def __init__(self):
        self.entry_time: Optional[datetime] = None

    def start(self):
        """Зафиксировать время входа в позицию (сейчас)."""
        self.entry_time = dt.datetime.now(dt.timezone.utc)
        print(
            f"⏱️ PositionTimer: вход в позицию в {self.entry_time.strftime('%H:%M:%S UTC')}")

    def set_entry_time(self, dt: datetime):
        """Установить время входа явно (например, восстановить из истории)."""
        self.entry_time = dt
        print(
            f"⏱️ PositionTimer: время входа установлено вручную: {self.entry_time.isoformat()}")

    def reset(self):
        """Сбросить таймер (при закрытии позиции)."""
        self.entry_time = None
        print(f"⏱️ PositionTimer: сброшен")

    def get_holding_hours(self) -> float:
        """Получить время удержания в часах."""
        if self.entry_time is None:
            return 0.0
        delta = dt.datetime.now(dt.timezone.utc) - self.entry_time
        return delta.total_seconds() / 3600.0

    def is_active(self) -> bool:
        """Проверить, активна ли позиция."""
        return self.entry_time is not None


class TrailingStopManager:
    """
    Управление плавающим (trailing) стоп-лоссом.

    Логика:
    - При открытии позиции фиксируем entry_price
    - При каждом обновлении цены отслеживаем max_price
    - Trailing stop = max_price - offset (offset задаётся в ATR)
    - Если current_price <= trailing_stop → возвращаем True (нужно закрывать)

    Особенности:
    - В UPTREND используем больший offset (даём больше свободы)
    - В FLAT/DOWNTREND — более жёсткий стоп
    """

    def __init__(
        self,
        entry_price: float,
        atr: float,
        trend: str = "FLAT",
        atr_multiplier_uptrend: float = 2.0,   # ← УВЕЛИЧЕНО (было 1.2)
        atr_multiplier_other: float = 1.5,     # ← УВЕЛИЧЕНО (было 0.8)
    ):
        self.entry_price = entry_price
        self.atr = atr
        self.trend = trend
        self.max_price = entry_price
        print(f"🔧 DEBUG ATR: atr={atr:.4f}, trend={trend}, multiplier={atr_multiplier_uptrend if trend == 'UPTREND' else atr_multiplier_other}")


        # Вычисляем offset СНАЧАЛА
        if trend == "UPTREND":
            self.offset = atr * atr_multiplier_uptrend
        else:
            self.offset = atr * atr_multiplier_other
            
        # ЗАЩИТА: минимальный offset = 1% от entry_price (для NG)
        min_offset = entry_price * 0.01  # 1%
        if self.offset < min_offset:
            print(f"⚠️ ATR offset слишком мал ({self.offset:.4f}), установлен минимум {min_offset:.4f}")
            self.offset = min_offset

        # ТЕПЕРЬ можем использовать self.offset
        self.trailing_stop = entry_price - self.offset  # ← ПЕРЕМЕСТИТЬ СЮДА!

        # Время удержания
        self.entry_time = dt.datetime.now(dt.timezone.utc)
        self.min_hold_minutes = 5

        print(
            f"🎯 Trailing Stop инициализирован: entry={entry_price:.4f}, "
            f"offset={self.offset:.4f} ({trend})"
        )

    def update(self, current_price: float, trend: str) -> bool:
        """
        Обновить trailing stop.
        Returns: True если нужно закрывать позицию
        """
        # Обновляем тренд
        if trend != self.trend:
            self.trend = trend
            if trend == "UPTREND":
                new_offset = self.atr * 1.2
            else:
                new_offset = self.atr * 0.8

            if new_offset > self.offset:
                self.offset = new_offset
                self.trailing_stop = self.max_price - self.offset

        # Обновляем max_price
        if current_price > self.max_price:
            self.max_price = current_price
            new_trailing_stop = self.max_price - self.offset

            if new_trailing_stop > self.trailing_stop:
                old_stop = self.trailing_stop
                self.trailing_stop = new_trailing_stop
                profit_pct = (self.max_price - self.entry_price) / \
                    self.entry_price * 100
                print(
                    f"🔼 Trailing Stop поднят: {old_stop:.4f} → {self.trailing_stop:.4f} "
                    f"(max={self.max_price:.4f}, profit={profit_pct:+.2f}%)"
                )

        # ЗАЩИТА: проверяем trailing только если прибыль >= 0.5%
        profit_pct = (current_price - self.entry_price) / \
            self.entry_price * 100

        if profit_pct < 0.5:
            return False  # Не выходим до +0.5%

        # Проверяем пробитие
        if current_price <= self.trailing_stop:
            profit_final = (current_price - self.entry_price) / \
                self.entry_price * 100
            print(
                f"🚨 TRAILING STOP СРАБОТАЛ! "
                f"Цена {current_price:.4f} <= стоп {self.trailing_stop:.4f} "
                f"(profit={profit_final:+.2f}%)"
            )
            return True
        return False

    def get_status(self) -> str:
        """Получить текущий статус для логов."""
        return (
            f"trailing_stop={self.trailing_stop:.4f}, "
            f"max_price={self.max_price:.4f}, "
            f"offset={self.offset:.4f}"
        )

class TrailingStopManagerShort:
    def __init__(
        self,
        entryprice: float,
        atr: float,
        trend: str = "FLAT",
        atrmultiplierdowntrend: float = 2.0,  # ← УВЕЛИЧЕНО
        atrmultiplierother: float = 1.5,       # ← УВЕЛИЧЕНО
    ):
        self.entryprice = entryprice
        self.atr = atr
        self.trend = trend
        self.minprice = entryprice
        
        # DEBUG: Вывод ATR параметров (ИСПРАВЛЕНО)
        print(
            f"🔧 DEBUG ATR SHORT: atr={atr:.4f}, trend={trend}, "
            f"multiplier={atrmultiplierdowntrend if trend == 'DOWNTREND' else atrmultiplierother}"
        )

        # Для шорта логично "шире" стоп при DOWNTREND
        if trend == "DOWNTREND":
            self.offset = atr * atrmultiplierdowntrend
        else:
            self.offset = atr * atrmultiplierother
        
        # ЗАЩИТА: минимальный offset = 1% от entry_price
        min_offset = entryprice * 0.01  # 1%
        if self.offset < min_offset:
            print(f"⚠️ ATR offset слишком мал ({self.offset:.4f}), установлен минимум {min_offset:.4f}")
            self.offset = min_offset

        # Trailing stop ВЫШЕ entry для SHORT
        self.trailingstop = entryprice + self.offset
        
        self.entrytime = dt.datetime.now(dt.timezone.utc)
        self.minholdminutes = 5
        
        print(
            f"🎯 Trailing Stop SHORT инициализирован: entry={entryprice:.4f}, "
            f"offset={self.offset:.4f} ({trend}), SL={self.trailingstop:.4f}"
        )

    def update(self, currentprice: float, trend: str) -> bool:
        # Обновление режима/offset (ИСПРАВЛЕНО: 2.0 и 1.5)
        if trend != self.trend:
            self.trend = trend
            if trend == "DOWNTREND":
                newoffset = self.atr * 2.0  # ← ИСПРАВЛЕНО (было 1.2)
            else:
                newoffset = self.atr * 1.5  # ← ИСПРАВЛЕНО (было 0.8)
            
            if newoffset > self.offset:
                self.offset = newoffset
                self.trailingstop = self.minprice + self.offset

        # Если цена ушла ниже — для шорта это прибыль → двигаем minprice вниз
        if currentprice < self.minprice:
            self.minprice = currentprice
            newstop = self.minprice + self.offset
            
            if newstop < self.trailingstop:
                old = self.trailingstop
                self.trailingstop = newstop
                profitpct = (self.entryprice - self.minprice) / self.entryprice * 100
                print(
                    f"🔽 Trailing Stop SHORT снижен: {old:.4f} → {self.trailingstop:.4f} "
                    f"(min={self.minprice:.4f}, profit={profitpct:+.2f}%)"
                )

        # ЗАЩИТА: проверяем trailing только если прибыль >= 0.5%
        profitpct_now = (self.entryprice - currentprice) / self.entryprice * 100
        
        if profitpct_now < 0.5:
            return False  # Не выходим до +0.5%

        # Стоп для шорта срабатывает если цена поднялась выше stop
        if currentprice >= self.trailingstop:
            profitfinal = (self.entryprice - currentprice) / self.entryprice * 100
            print(
                f"🚨 TRAILING STOP SHORT СРАБОТАЛ! "
                f"Цена {currentprice:.4f} >= стоп {self.trailingstop:.4f} "
                f"(profit={profitfinal:+.2f}%)"
            )
            return True
        
        return False



def get_minutes_to_clearing() -> int:
    """
    Вычислить минуты до ближайшего клиринга MOEX (MSK).

    Клиринги:
    - Утренний: 10:00-10:05 МСК (менее критичен)
    - Дневной:  14:00-14:05 МСК
    - Вечерний: 18:50-19:05 МСК (основной)

    Returns:
        Минуты до ближайшего клиринга (положительное число).
        Если торги закрыты — возвращает 999 (игнорировать).
    """
    # Текущее время в МСК (UTC+3)
    now_utc = dt.datetime.now(dt.timezone.utc)
    now_msk = now_utc + timedelta(hours=3)

    current_time = now_msk.time()

    # Клиринги в МСК (начало)
    clearings = [
        (10, 0),   # Утренний
        (14, 0),   # Дневной
        (18, 50),  # Вечерний (ОСНОВНОЙ)
    ]

    # Часы торгов срочного рынка: 10:00-23:50 МСК
    if current_time < dt.time(10, 0) or current_time > dt.time(23, 50):
        return 999  # Торги закрыты

    # Найти ближайший клиринг
    min_delta = None
    for hour, minute in clearings:
        clearing_time = now_msk.replace(
            hour=hour, minute=minute, second=0, microsecond=0)

        if clearing_time > now_msk:
            delta = (clearing_time - now_msk).total_seconds() / 60.0
            if min_delta is None or delta < min_delta:
                min_delta = delta

    # Если все клиринги прошли сегодня, смотрим на утренний завтра
    if min_delta is None:
        tomorrow = now_msk + timedelta(days=1)
        clearing_time = tomorrow.replace(
            hour=10, minute=0, second=0, microsecond=0)
        min_delta = (clearing_time - now_msk).total_seconds() / 60.0

    return int(min_delta)


def get_max_lots_allowed() -> tuple:
    """
    Определить максимальное количество лотов с учётом событий.
    Returns: (max_lots, reason)
    """
    now_utc = dt.datetime.now(dt.timezone.utc)
    now_msk = now_utc + timedelta(hours=3)
    weekday = now_msk.weekday()  # 0=пн, 3=чт
    hour = now_msk.hour

    # Четверг: окно отчёта EIA (17:00-19:00 МСК)
    if weekday == 3 and 17 <= hour < 19:
        return 1, "⚠️ Четверг 17:00-19:00 (окно EIA): максимум 1 лот"

    # Обычное время
    return MAX_LOTS, ""


def check_clearing_profit_take(
    lots: int,
    pnl_pct: float,
    minutes_to_clearing: int,
    trend: str,
    ai_signal: str,
    ai_confidence: float,
) -> Optional[str]:
    """
    Проверить условия фиксации прибыли перед клирингом.

    Логика:
    - Если PnL >= +1.5% И до клиринга < 60 минут:
      - Сильный тренд (UPTREND + AI BUY >=80%) → закрыть 50%
      - Слабый/боковик → закрыть 100%

    Returns:
        "SELLALL" или "SELLALL" или None
    """
    if lots <= 0:
        return None

    if pnl_pct < 1.5:
        return None

    if minutes_to_clearing > 60:
        return None

    # Сильный восходящий тренд + AI уверен
    strong_trend = (
        trend == "UPTREND"
        and ai_signal == "BUY"
        and ai_confidence >= 80
    )

    if strong_trend:
        print(
            f"⏰ КЛИРИНГ через {minutes_to_clearing} мин | PnL +{pnl_pct:.2f}% | "
            f"Сильный тренд → Фиксирую 50%"
        )
        return "SELLALL"
    else:
        print(
            f"⏰ КЛИРИНГ через {minutes_to_clearing} мин | PnL +{pnl_pct:.2f}% | "
            f"Тренд слабый/боковик → Фиксирую 100%"
        )
        return "SELLALL"


def check_stale_position(
    lots: int,
    pnl_pct: float,
    holding_hours: float,
) -> bool:
    """
    Проверить, не "застряла" ли позиция (висяк > 24 часа).

    Логика:
    - Если позиция открыта > 24 часа И PnL между -0.5% и +1.5%
      → закрыть 100%

    Returns:
        True если нужно закрыть "висяк"
    """
    if lots <= 0:
        return False

    if holding_hours <= 24.0:
        return False

    if -0.5 <= pnl_pct <= 1.5:
        print(
            f"🕐 ВИСЯК: позиция {holding_hours:.1f}ч | PnL {pnl_pct:+.2f}% | "
            f"Застряла → Закрываю 100%"
        )
        return True

    return False


def check_intraday_profit_take(
    lots: int,
    pnl_pct: float,
    holding_hours: float,
    minutes_to_clearing: int,
) -> bool:
    """
    Проверить условия интрадей-фиксации прибыли вне клиринга.

    Логика:
    - Если PnL >= +1.5% И holding > 2ч И до клиринга > 90 мин
      → частично зафиксировать 33-50%

    Returns:
        True если нужно зафиксировать интрадей-профит
    """
    if lots <= 0:
        return False

    if pnl_pct < 1.5:
        return False

    if holding_hours <= 2.0:
        return False

    if minutes_to_clearing <= 90:
        return False  # Близко к клирингу — сработает другое правило

    print(
        f"💰 ИНТРАДЕЙ: PnL +{pnl_pct:.2f}% | Holding {holding_hours:.1f}ч | "
        f"Фиксирую 50%"
    )
    return True


class MainOrderExecutor:
    def __init__(self, token: str):
        self.token = token
        self.account_id: Optional[str] = None

    async def _get_account_id(self, client: AsyncClient) -> str:
        if self.account_id:
            return self.account_id
        accounts = await client.users.get_accounts()
        

        self.account_id = accounts.accounts[0].id
        return self.account_id

    async def get_position_data(self, figi: str) -> Optional[Dict[str, Any]]:
        """
        Возвращает dict с позицией или None, если произошла ошибка сети.
        Это защищает от открытия дублирующих позиций, если бот "ослеп".
        
        ВАЖНО: В редких случаях брокер может вернуть average_price = 0.0,
        в этом случае используется текущая цена инструмента как fallback.
        """
        try:
            async with AsyncClient(self.token) as client:
                account_id = await self._get_account_id(client)
                portfolio = await client.operations.get_portfolio(account_id=account_id)
                
                for p in portfolio.positions:
                    if p.figi == figi:
                        avg = (
                            p.average_position_price.units
                            + p.average_position_price.nano / 1e9
                        )
                        
                        lots = int(p.quantity.units)
                        
                        # 🔧 FALLBACK: если avg = 0, используем current_price
                        if avg == 0.0 and lots > 0:
                            # Позиция есть, но цена не определена → используем текущую
                            current = (
                                p.current_price.units
                                + p.current_price.nano / 1e9
                            )
                            if current > 0:
                                avg = current
                                print(f"⚠️ Broker API fallback: average_price = 0, используем current_price: {avg:.3f}")
                            else:
                                # Если и current_price = 0, запросим last_price
                                last_price = await self.get_last_price(figi)
                                if last_price and last_price > 0:
                                    avg = last_price
                                    print(f"⚠️ Broker API fallback: используем last_price: {avg:.3f}")
                                else:
                                    print(f"⚠️ КРИТИЧНО: не удалось получить цену позиции! Lots={lots}, FIGI={figi}")
                        
                        return {
                            "lots": lots,
                            "average_price": float(avg),
                        }
                
                # Если в цикле не нашли позицию по figi, значит ее нет
                return {"lots": 0, "average_price": 0.0}
                
        except Exception as e:
            print(f"⚠️ ОШИБКА СЕТИ при получении позиции: {e}")
            return None  # Возвращаем None, чтобы главный цикл знал о сбое

    
    
    async def get_portfolio(self) -> Dict[str, Any]:
        """
        Получает snapshot портфеля (позиции, баланс, портфельная стоимость).
        Возвращает dict с positions, total_amount_currencies, total_amount_portfolio.
        """
        try:
            async with AsyncClient(self.token) as client:
                account_id = await self._get_account_id(client)
                portfolio = await client.operations.get_portfolio(account_id=account_id)
                
                return {
                    "total_amount_currencies": (
                        portfolio.total_amount_currencies.units 
                        + portfolio.total_amount_currencies.nano / 1e9
                    ),
                    "total_amount_portfolio": (
                        portfolio.total_amount_portfolio.units 
                        + portfolio.total_amount_portfolio.nano / 1e9
                    ),
                    "positions": [
                        {
                            "figi": p.figi,
                            "instrument_type": p.instrument_type,
                            "name": getattr(p, "instrument_name", ""),
                            "ticker": getattr(p, "ticker", ""),
                            "balance": p.quantity.units + p.quantity.nano / 1e9,
                            "average_position_price_value": (
                                p.average_position_price.units 
                                + p.average_position_price.nano / 1e9
                            ) if p.average_position_price else None,
                            "current_price_value": (
                                p.current_price.units 
                                + p.current_price.nano / 1e9
                            ),
                            "expected_yield_value": (
                                p.expected_yield.units 
                                + p.expected_yield.nano / 1e9
                            ),
                            "blocked": (
                                p.blocked.units 
                                + p.blocked.nano / 1e9
                            ) if p.blocked else 0.0
                        }
                        for p in portfolio.positions
                    ]
                }
        except Exception as e:
            print(f"⚠️ get_portfolio error: {e}")
            return {
                "positions": [], 
                "total_amount_currencies": 0.0, 
                "total_amount_portfolio": 0.0
            }

    async def get_operations(self, from_date: datetime, to_date: datetime, figi: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Получает список операций (сделки, вариационная маржа, комиссии) за период.
        
        Args:
            from_date: начало периода (datetime с timezone)
            to_date: конец периода (datetime с timezone)
            figi: фильтр по инструменту (опционально)
        
        Returns:
            Список dict с данными операций
        """
        try:
            async with AsyncClient(self.token) as client:
                account_id = await self._get_account_id(client)
                response = await client.operations.get_operations(
                    account_id=account_id,
                    from_=from_date,
                    to=to_date,
                    figi=figi
                )
                
                return [
                    {
                        "id": op.id,
                        "order_id": getattr(op, "order_id", ""),
                        "operation_type": op.operation_type.name if hasattr(op.operation_type, "name") else str(op.operation_type),
                        "date": op.date,
                        "payment_value": op.payment.units + op.payment.nano / 1e9,
                        "commission_value": (
                            op.commission.units + op.commission.nano / 1e9
                        ) if op.commission else 0.0,
                        "quantity": op.quantity,
                        "price_value": (
                            op.price.units + op.price.nano / 1e9
                        ) if op.price else 0.0,
                        "figi": op.figi,
                        "instrument_name": getattr(op, "instrument_name", ""),
                        "ticker": getattr(op, "ticker", "")
                    }
                    for op in response.operations
                ]
        except Exception as e:
            print(f"⚠️ get_operations error: {e}")
            return []


        async def get_candles(self, figi: str) -> pd.DataFrame:
            """Загрузка реальных H1‑свечей за 7 дней по FIGI."""
            now = dt.datetime.now(dt.timezone.utc)
            try:
                async with AsyncClient(self.token) as client:
                    candles = []
                    async for c in client.get_all_candles(
                        figi=figi,
                        from_=now - timedelta(days=7),
                        interval=CandleInterval.CANDLE_INTERVAL_HOUR,
                    ):
                        candles.append(
                            {
                                "time": c.time,
                                "open": c.open.units + c.open.nano / 1e9,
                                "high": c.high.units + c.high.nano / 1e9,
                                "low": c.low.units + c.low.nano / 1e9,
                                "close": c.close.units + c.close.nano / 1e9,
                                "volume": c.volume,
                            }
                        )
                    df = pd.DataFrame(candles)
                    if not df.empty:
                        df.set_index("time", inplace=True)
                    return df
            except Exception as e:
                print(f"⚠️ Ошибка свечей: {e}")
                return pd.DataFrame()

    async def get_candles_5m(self, figi: str, days: int = 7) -> pd.DataFrame:
        """Загрузка 5m‑свечей за days дней по FIGI для расчёта SMA50/200."""
        now = dt.datetime.now(dt.timezone.utc)
        try:
            async with AsyncClient(self.token) as client:
                candles = []
                async for c in client.get_all_candles(
                    figi=figi,
                    from_=now - timedelta(days=days),
                    interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
                ):
                    candles.append({
                        "time": c.time,
                        "open":  c.open.units  + c.open.nano  / 1e9,
                        "high":  c.high.units  + c.high.nano  / 1e9,
                        "low":   c.low.units   + c.low.nano   / 1e9,
                        "close": c.close.units + c.close.nano / 1e9,
                        "volume": int(getattr(c, "volume", 0)),
                    })

                df = pd.DataFrame(candles)
                if not df.empty:
                    df.set_index("time", inplace=True)
                return df
        except Exception as e:
            print(f"⚠️ Ошибка свечей 5m: {e}")
            return pd.DataFrame()

    async def get_last_price(self, figi: str) -> Optional[float]:
        """Запасной способ получить last_price."""
        try:
            async with AsyncClient(self.token) as client:
                resp = await client.market_data.get_last_prices(figi=[figi])
                if resp.last_prices:
                    lp = resp.last_prices[0]
                    return lp.price.units + lp.price.nano / 1e9
        except Exception as e:
            print(f"⚠️ Ошибка last_price: {e}")
        return None

    async def get_future_expiration(self, figi: str) -> Optional[datetime]:
        """Получаем дату экспирации фьючерса по FIGI."""
        try:
            async with AsyncClient(self.token) as client:
                inst = await client.instruments.future_by(
                    id_type=1,  # INSTRUMENT_ID_TYPE_FIGI
                    id=figi,
                )
                future: Future = inst.instrument
                return future.expiration_date
        except Exception as e:
            print(f"⚠️ Ошибка получения экспирации: {e}")
            return None
        
    async def get_future_position(self, figi: str) -> dict:
        try:
            async with AsyncClient(self.token) as client:
                accountid = await self.getaccountid(client)

                pos = await client.operations.get_positions(account_id=accountid)

                qty = 0
                for f in pos.futures:
                    if f.figi == figi:
                        qty = int(f.balance)   # ВАЖНО: signed
                        break

                # avgprice лучше брать из портфеля (там есть average_position_price)
                portfolio = await client.operations.get_portfolio(account_id=accountid)
                avg = 0.0
                for p in portfolio.positions:
                    if p.figi == figi:
                        ap = p.average_position_price
                        avg = float(ap.units) + float(ap.nano) / 1e9
                        break

                return {"qty": qty, "averageprice": avg}
        except Exception as e:
            print("get_future_position error:", e)
            return {"qty": 0, "averageprice": 0.0}

    async def post_order(
        self, figi: str, direction: Literal["BUY", "SELL"], quantity: int
    ) -> bool:
        """Отправляет рыночный ордер."""
        try:
            async with AsyncClient(self.token) as client:
                account_id = await self._get_account_id(client)
                tinkoff_dir = (
                    OrderDirection.ORDER_DIRECTION_BUY
                    if direction == "BUY"
                    else OrderDirection.ORDER_DIRECTION_SELL
                )
                order_id = str(uuid.uuid4())
                await client.orders.post_order(
                    figi=figi,
                    quantity=quantity,
                    direction=tinkoff_dir,
                    account_id=account_id,
                    order_type=OrderType.ORDER_TYPE_MARKET,
                    order_id=order_id,
                )
                print(f"🚀 ОРДЕР ОТПРАВЛЕН: {direction} {quantity} шт.")
                return True
        except Exception as e:
            print(f"❌ Ошибка ордера: {e}")
            return False


class LLMMarketAnalystAdapter:
    def __init__(self):
        key = settings.OPENROUTER_API_KEY.get_secret_value()
        self.llm = ChatOpenAI(
            model=settings.AI_MODEL_ANALYST,
            temperature=0,  # Строгость ответов
            api_key=key,  # type: ignore
            base_url=settings.OPENROUTER_BASE_URL,
            model_kwargs={"response_format": {"type": "json_object"}},
            timeout=30,  # ← ДОБАВИТЬ TIMEOUT 30 СЕКУНД
            max_retries=2,  # ← ДОБАВИТЬ RETRY
        )
        print(
        f"🤖 AI инициализирован: analyst={settings.AI_MODEL_ANALYST}, "
        f"risk={settings.AI_MODEL_RISK}, timeout=30s"
            )

    def analyze(self, data: Dict[str, Any], bias: str = "neutral") -> Dict[str, Any]:
        """
        Принимает торговое решение, объединяя Bias (из новостей) и Технический анализ.
        """
        template = (
    "SYSTEM: Ты — Агрессивный Трейдер фьючерсами NG (скальпер). Твоя цель — прибыль.\n\n"
    "ВХОДНЫЕ ДАННЫЕ:\n"
    "1. ФУНДАМЕНТАЛ (Bias): {bias}. (Bullish=атака BUY, Bearish=атака SELL).\n"
    "2. ТЕХНИКА: Цена={price}, RSI={rsi}, ATR={atr}, Тренд_5m={trend_5m} "
    "(UPTREND/DOWNTREND/FLAT).\n"
    "3. ВОЛАТИЛЬНОСТЬ: ATR={atr}. Если ATR>0.20 и RSI 30-70 → HOLD (шумный рынок).\n"
    "4. ПОГОДА/СПРОС: {weather_str}.\n\n"
    "ТВОЯ ТОРГОВАЯ СТРАТЕГИЯ:\n"
    "---------------------------------------------------\n"
    "СЦЕНАРИЙ 1: ФУНДАМЕНТАЛ = BULLISH (Рост)\n"
    " - Игнорируй SELL, если RSI не >95.\n"
    " - АТАКУЙ (BUY): RSI <65 (тренд=сила).\n"
    " - STRONG BUY: RSI 50-70 + UPTREND.\n"
    "---------------------------------------------------\n"
    "СЦЕНАРИЙ 2: ФУНДАМЕНТАЛ = BEARISH (Падение)\n"
    " - Игнорируй BUY, если RSI не <5.\n"
    " - АТАКУЙ (SELL): RSI >25 (тренд вниз=сила).\n"
    " - STRONG SELL: RSI 35-50.\n"
    "---------------------------------------------------\n"
    "СЦЕНАРИЙ 3: ФУНДАМЕНТАЛ = NEUTRAL (Боковик)\n"
    " - BUY: RSI <30.\n"
    " - SELL: RSI >70.\n"
    " - Иначе: HOLD.\n"
    "---------------------------------------------------\n"
    "ДОПОЛНИТЕЛЬНО:\n"
    " - Если ATR >0.25 и RSI 30-70 → HOLD (слишком шумно).\n"
    " - UPTREND + RSI 35-50 → BUY (откуп коррекции).\n"
    "ЗАДАЧА: Прими решение (BUY/SELL/HOLD). Сомнения → HOLD.\n"
    "ОТВЕТ (СТРОГО JSON):\n"
    '{"signal": "BUY или SELL или HOLD", '
    '"confidence": "0-100", '
    '"bullish_prob": "0-1", '
    '"bearish_prob": "0-1", '
    '"reason": "1-2 предложения RU"}'
)


        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | self.llm

        in_data = {
            "price": data.get("close"),
            "rsi": data.get("RSI"),
            "atr": data.get("ATR"),
            "bias": bias,
            "trend_5m": data.get("trend_5m", "FLAT"),
        }

        print(
            f"🔄 Отправка запроса в AI: bias={bias}, RSI={in_data['rsi']:.1f}, trend={in_data['trend_5m']}")

        try:
            res = chain.invoke(in_data)
            text = str(res.content).strip()
            # ← ЛОГИРУЕМ ПЕРВЫЕ 100 СИМВОЛОВ
            print(f"📥 AI ответ получен: {text[:100]}...")

            # Очистка от маркдауна
            text = re.sub(r"``````", "", text)
            match = re.search(r"\{.*\}", text, re.DOTALL)
            payload = match.group(0) if match else "{}"

            result = json.loads(payload)
            print(
                f"✅ AI парсинг успешен: signal={result.get('signal')}, confidence={result.get('confidence')}")
            return result

        except Exception as e:
            # Логгируем ошибку, но не крашим бота
            print(f"❌ AI Analyze Error: {e}")
            print(
                f"❌ Детали: bias={bias}, RSI={in_data['rsi']}, trend={in_data['trend_5m']}")
            return {
                "signal": "HOLD",
                "reason": f"AI Technical Error: {str(e)[:100]}",
                "confidence": 0,
            }


def escape_html(text: str) -> str:
    """Экранирует HTML-символы для Telegram API."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def send_telegram(msg: str) -> None:
    """Отправляет уведомление в Telegram с защитой от ошибок."""
    try:
        token = settings.TELEGRAM_BOT_TOKEN.get_secret_value().strip()
        chat_id = settings.TELEGRAM_CHAT_ID
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id": chat_id,
                "text": escape_html(msg),
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"⚠️ TG Error: {resp.status} - {text}")
    except Exception as e:
        print(f"❌ Критическая ошибка Telegram Alerting: {e}")


async def telegram_smoke_test() -> None:
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value().strip()
    url = f"https://api.telegram.org/bot{token}/getMe"

    async with aiohttp.ClientSession() as session:
        resp = await session.get(url)
        text = await resp.text()
        print(f"TG getMe status={resp.status} body={text[:200]}")


def calc_daily_change(candles: pd.DataFrame) -> float:
    """Процентное изменение цены за последние 24 часа по close."""
    if candles.empty:
        return 0.0
    last_ts = candles.index.max()
    first_ts = last_ts - timedelta(hours=24)
    window = candles[candles.index >= first_ts]
    if window.empty:
        return 0.0
    first_close = float(window["close"].iloc[0])
    last_close = float(window["close"].iloc[-1])
    if first_close == 0:
        return 0.0
    return (last_close - first_close) / first_close * 100.0


def get_fundamental_news(max_len: int = 2500) -> str:
    try:
        if not os.path.exists(NEWSFILE):
            return ""
        with open(NEWSFILE, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
        if not content:
            return ""
        return content[:max_len]
    except Exception as e:
        print(f"⚠️ Ошибка чтения {NEWSFILE}: {e}")
        return ""


def parse_trading_rules_from_news() -> dict:
    try:
        if not os.path.exists(NEWSFILE):
            return {
                "bias": "neutral",
                "max_buy_price": None,
                "min_sell_price": None,
                "force_buy": False,
                "forced_short": False,
                "target_lots": None,
                "short_conditions": None,
                "force_weight": None,
            }

        with open(NEWSFILE, 'r', encoding='utf-8') as f:
            content = f.read()
        
        rules = {
            "bias": "neutral",
            "max_buy_price": None,
            "min_sell_price": None,
            "force_buy": "FORCE_BUY: TRUE" in content.upper(),
            "forced_short": "FORCED_SHORT: YES" in content.upper(),
            "target_lots": None,
            "short_conditions": None,
            "force_weight": None,
        }

        # Парсим BIAS (ищем разные варианты)
        bias_patterns = [
            r'BIAS:\s*(BEARISH|BULLISH|NEUTRAL)',
            r'ФУНДАМЕНТАЛЬНЫЙ BIAS:\s*(BEARISH|BULLISH|NEUTRAL)',
        ]
        for pattern in bias_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                bias_value = match.group(1).upper()
                if bias_value in ('BULLISH', 'BULL'):
                    rules["bias"] = "bullish"
                elif bias_value in ('BEARISH', 'BEAR'):
                    rules["bias"] = "bearish"
                break

        # Парсим MAX_BUY_PRICE (оба варианта)
        for line in content.split('\n'):
            if 'MAX_BUY_PRICE' in line or 'PRICE_CAP_BUY' in line:
                numbers = re.findall(r'[0-9]+\.[0-9]+', line)
                if numbers:
                    rules["max_buy_price"] = float(numbers[0])
                    break

        # Парсим MIN_SELL_PRICE (оба варианта)
        for line in content.split('\n'):
            if 'MIN_SELL_PRICE' in line or 'PRICE_CAP_SELL' in line:
                numbers = re.findall(r'[0-9]+\.[0-9]+', line)
                if numbers:
                    rules["min_sell_price"] = float(numbers[0])
                    break

        # Парсим TARGET_LOTS (новое)
        match = re.search(r'TARGET_LOTS:\s*(\d+)', content)
        if match:
            rules["target_lots"] = int(match.group(1))

        # Парсим SHORT_CONDITIONS (новое)
        match = re.search(r'SHORT_CONDITIONS:\s*(.+?)(?:\n|$)', content)
        if match:
            rules["short_conditions"] = match.group(1).strip()

        # Парсим FORCE_WEIGHT
        match = re.search(r'FORCE_WEIGHT:\s*([0-9]*\.?[0-9]+)', content)
        if match:
            rules["force_weight"] = float(match.group(1))

        #         # print(f"✅ Правила: BIAS={rules['bias'].upper()}, max_buy={rules['max_buy_price']}, min_sell={rules['min_sell_price']}, forced_short={rules['forced_short']}, target_lots={rules['target_lots']}, force_weight={rules['force_weight']}")

        print(f"📰 Новости: прочитано {len(content)} символов")

        return rules

    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}")
        return {"bias": "neutral", "max_buy_price": None, "min_sell_price": None}





def load_last_entry_time_from_history(figi: str) -> Optional[datetime]:
    """
    Читает trade_history.csv и пытается восстановить время входа в текущую позицию.

    Логика:
    - Берём последнюю строку, где:
      - figi совпадает
      - action в ["OPEN_LONG", "ADD_LONG", "CLOSE_SHORT"] (открытие/добор/переворот в лонг)
    - Если такой строки нет — возвращаем None.
    """
    try:
        if not TRADE_HISTORY_PATH.exists():
            print("⏱️ PositionTimer: trade_history.csv не найден, таймер не восстановлен")
            return None

        with TRADE_HISTORY_PATH.open("r", encoding="utf-8") as f:
            lines = f.readlines()

        # Пропускаем заголовок, если он есть
        if not lines or len(lines) <= 1:
            print("⏱️ PositionTimer: trade_history.csv пуст, таймер не восстановлен")
            return None

        # Идём с конца файла вверх, ищем последнюю релевантную запись
        for line in reversed(lines[1:]):  # пропускаем первую строку-заголовок
            line = line.strip()
            if not line:
                continue

            # Формат: time,figi,action,lots_before,lots_after,price,signal,confidence,reason
            parts = []
            current = ""
            in_quotes = False
            for ch in line:
                if ch == '"' and not in_quotes:
                    in_quotes = True
                    current += ch
                elif ch == '"' and in_quotes:
                    in_quotes = False
                    current += ch
                elif ch == "," and not in_quotes:
                    parts.append(current)
                    current = ""
                else:
                    current += ch
            if current:
                parts.append(current)

            if len(parts) < 5:
                continue

            time_str = parts[0]
            figi_str = parts[1]
            action = parts[2]
            lots_after_str = parts[4]

            if figi_str != figi:
                continue

            if action not in ("OPEN_LONG", "ADD_LONG", "CLOSE_SHORT"):
                continue

            try:
                lots_after = int(lots_after_str)
            except ValueError:
                continue

            # Нас интересуют только записи, после которых позиция > 0
            if lots_after <= 0:
                continue

            try:
                entry_time = datetime.fromisoformat(time_str)
            except Exception:
                continue

            # В логах time хранится в локальном времени сервера, приводим к UTC
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            else:
                entry_time = entry_time.astimezone(timezone.utc)

            print(
                f"⏱️ PositionTimer: восстановлено время входа из истории: {entry_time.isoformat()}")
            return entry_time

        print("⏱️ PositionTimer: не найдено подходящих записей в trade_history.csv")
        return None

    except Exception as e:
        print(
            f"⚠️ Ошибка при чтении trade_history.csv для восстановления таймера: {e}")
        return None


async def log_trade(
    action: str,
    figi: str,
    lots_before: int,
    lots_after: int,
    price: float,
    signal: str,
    confidence: float,
    reason: str,
) -> None:
    """
    Лог сделки в trade_history.csv:
    time,figi,action,lots_before,lots_after,price,signal,confidence,reason
    """
    try:
        header = (
            "time,figi,action,lots_before,lots_after,"
            "price,signal,confidence,reason\n"
        )
        # безопасный reason без переносов строк и лишних кавычек
        safe_reason = (reason or "").replace(
            '"', "'").replace("\n", " ").strip()
        line = (
            f"{dt.datetime.now().isoformat(timespec='seconds')},"
            f"{figi},{action},{lots_before},{lots_after},"
            f"{price:.4f},{signal},{confidence:.1f},\"{safe_reason}\"\n"
        )

        need_header = not TRADE_HISTORY_PATH.exists()
        with TRADE_HISTORY_PATH.open("a", encoding="utf-8") as f:
            if need_header:
                f.write(header)
            f.write(line)
    except Exception as e:
        print(f"⚠️ Ошибка логирования сделки: {e}")


def log_decision_block(
    cycle: int,
    price: float,
    rsi: float,
    trend: str,
    lots: int,
    holding_hours: float,
    ai_signal: str,
    ai_confidence: float,
    bias: str,
    minutes_to_clearing: int,
    rules: dict,
    action: str,
    reason: str,
    pnl_pct: float = 0.0,
    forced_entry: bool = False,  # ← НОВЫЙ ПАРАМЕТР
    consecutive_signals: int = 0,  # ← Сколько BUY подряд
    avg_confidence: float = 0.0  # ← Средняя уверенность
):
    """
    Выводит блок принятия решения в консоль и записывает его в shadow_agents_log.jsonl.
    """
    import json
    from datetime import datetime
    import pytz
    from pathlib import Path

    # 1. Визуальный вывод в консоль
    print("\n" + "="*70)
    print(f"📊 DECISION BLOCK | Cycle: {cycle} | {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M:%S')}")
    print("-" * 70)
    print(f"💰 Price: {price:.3f} | RSI: {rsi:.1f} | Trend: {trend}")
    print(f"📦 Lots: {lots} | PnL: {pnl_pct:.2f}% | Holding: {holding_hours:.1f}h")
    print(f"🤖 AI: {ai_signal} ({ai_confidence:.0f}%) | BIAS: {bias.upper()}")



    # ← НОВЫЙ ВЫВОД для форсированных входов
    if forced_entry:
        print(f"🚨 FORCED ENTRY: {consecutive_signals} consecutive BUY (avg Conf {avg_confidence:.1f}%)")

    if rules.get('max_buy_price'):
        print(f"🟢 MAX_BUY: {rules['max_buy_price']:.3f}")
    if rules.get('min_sell_price'):
        print(f"🔴 MIN_SELL: {rules['min_sell_price']:.3f}")

    emoji_map = {"BUY": "🚀", "SELLALL": "💥", "SELLALL": "⚖️", "NOOP": "😴", "BUY1": "🎯", "SELL1": "🎯", "BUYALL": "💪", "BUYALL": "⚡"}
    emoji = emoji_map.get(action, "🔍")
    print(f"➡️ ACTION: {emoji} {action}")
    if reason:
        print(f"📝 Reason: {reason}")
    print("="*70 + "\n")

    # 2. Формируем лог-запись
    log_entry = {
        "timestamp": datetime.now(pytz.timezone("Europe/Moscow")).isoformat(),
        "cycle": cycle,
        "input_state": {
            "price": price,
            "rsi": rsi,
            "trend": trend,
            "lots": lots,
            "pnl_pct": pnl_pct,
            "holding_hours": holding_hours,
            "minutes_to_clearing": minutes_to_clearing,
            "bias": bias
        },
        "decision": {
            "ai_signal": ai_signal,
            "ai_confidence": ai_confidence,
            "action": action,
            "reason": reason,
            "rules": rules,
            "forced_entry": forced_entry,  # ← НОВОЕ ПОЛЕ
            "consecutive_signals": consecutive_signals,  # ← Счётчик BUY
            "avg_confidence": avg_confidence  # ← Средняя уверенность
        }
    }

    # 3. Запись в файл (без обогащения, т.к. это sync функция)
    # Обогащение произойдёт в main_loop асинхронно
    try:
        log_file = Path("shadow_agents_log.jsonl")
        log_file.touch(exist_ok=True)
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            f.flush()
        
    except PermissionError as e:
        print(f"⚠️ WARN: No write permission to shadow_agents_log.jsonl: {e}")
    except OSError as e:
        print(f"⚠️ WARN: OS error writing log: {e}")
    except Exception as e:
        print(f"⚠️ WARN: Unexpected error in logging: {type(e).__name__}: {e}")
    
    # Возвращаем log_entry для дальнейшего обогащения в main_loop
    return log_entry






def decide_action(
    lots: int,
    target_lots: int,
    max_lots: int,
    ai_signal: str,
    ai_confidence: int,
    bullish_prob: float,
    bearish_prob: float,
    trend_5m: str,
    rsi: float,
    bias: str,
    rules: Dict[str, Any],
    market_state: str = "RANGE",
    minutes_to_clearing: int = 999,
    current_volume: int = 0,
    avg_volume: int = 1,
    atr: float = 0.1,
    current_price: float = 0.0,
    sl_level: float = 0.0,
    pnl_pct: float = 0.0,
    last_sl_exit_cycle: int = -999,  
    current_cycle: int = 0,           
) -> Tuple[str, str, Dict[str, Any]]:

    """
    v2.1 Optimized Hybrid Decision Engine — 100% эквивалентна v2.0, но быстрее.
    Убраны дубли, оптимизированы формулы, early returns.
    """
    global consecutive_buy_signals, buy_signals_history, consecutive_sell_signals, sell_signals_history
    
    metadata = {"forced_entry": False, "consecutive_signals": 0, "avg_confidence": 0.0}
    
    # Adaptive threshold (optimized: ternary)
    adaptive_threshold = 70 if atr > 0.15 else 60 if atr < 0.10 else 65
    
    # Early guards
    clearing_block = minutes_to_clearing <= 30
    if clearing_block:
        return "NOOP", f"⏱️ Clearing protection ({minutes_to_clearing}min)", metadata
    
    delta = target_lots - lots
    metadata.update({"delta": delta, "target_lots": target_lots, "current_lots": lots})
    
    # Trading rules (cached call)
    trading_rules = parse_trading_rules_from_news()
    if trading_rules.get("force_buy") and lots == 0:
        # === ЗАЩИТА ОТ FORCED BUY ПОСЛЕ НЕДАВНЕГО SL ===
        cycles_since_sl = current_cycle - last_sl_exit_cycle if last_sl_exit_cycle >= 0 else 999
        
        # Блокировка 1: Недавний SL (< 5 циклов)
        if cycles_since_sl < 5:
            return "NOOP", f"🚫 FORCED BUY blocked: SL exit {cycles_since_sl} cycles ago", metadata
        
        # Блокировка 2: Падающий рынок (trend DOWN + RSI < 50)
        if trend_5m == "DOWN" and rsi < 50:
            return "NOOP", f"🚫 FORCED BUY blocked: trend DOWN + RSI {rsi:.1f} < 50", metadata
        
        # Все проверки пройдены
        return "BUY2", "⚡ FORCED BUY from news_fire.txt", metadata

    
    # Signal accumulation (optimized: single if per signal type)
    if ai_signal == "BUY":
        consecutive_buy_signals += 1
        buy_signals_history.append(ai_confidence)
        if len(buy_signals_history) > 5: buy_signals_history.pop(0)
    else:
        consecutive_buy_signals = 0
        buy_signals_history.clear()
    
    if ai_signal == "SELL":
        consecutive_sell_signals += 1
        sell_signals_history.append(ai_confidence)
        if len(sell_signals_history) > 5: sell_signals_history.pop(0)
    elif ai_signal != "BUY":
        consecutive_sell_signals = 0
        sell_signals_history.clear()
    
    # Forced entry BUY (optimized: sum[-3:] direct)
    if (consecutive_buy_signals >= 3 and len(buy_signals_history) >= 3 and lots == 0):
        avg_conf = sum(buy_signals_history[-3:]) / 3
        volume_confirmed = current_volume >= avg_volume * 1.2 if avg_volume > 0 else True
        if (avg_conf >= adaptive_threshold and rsi < 75 and bullish_prob > 0.55 and volume_confirmed):
            metadata.update({"forced_entry": True, "consecutive_signals": consecutive_buy_signals, "avg_confidence": avg_conf})
            return "BUY1", f"🚨 FORCED ENTRY: 3 BUY (thr {adaptive_threshold}%, avg {avg_conf:.1f}%) RSI {rsi:.1f}", metadata
    
    # Forced entry SELL
    if (consecutive_sell_signals >= 3 and len(sell_signals_history) >= 3 and lots == 0):
        avg_conf_sell = sum(sell_signals_history[-3:]) / 3
        volume_confirmed_sell = current_volume >= avg_volume * 1.2 if avg_volume > 0 else True
        if (avg_conf_sell >= adaptive_threshold and rsi > 25 and bearish_prob > 0.55 and volume_confirmed_sell):
            metadata.update({"forced_entry": True, "consecutive_signals": consecutive_sell_signals, "avg_confidence": avg_conf_sell})
            return "SELL1", f"🚨 FORCED SHORT: 3 SELL (thr {adaptive_threshold}%, avg {avg_conf_sell:.1f}%) RSI {rsi:.1f}", metadata
    
    # Confidence & hedge (inline optimized)
    is_extreme = any(word in str(rules).lower() for word in ["vortex", "extreme", "arctic", "noaa"])
    effective_confidence = ai_confidence + (20 if is_extreme else 0)
    min_entry_conf = 40 if is_extreme else 70
    is_bullish_hedge = (bias == "bearish" and bullish_prob > 0.30)
    is_bearish_hedge = (bias == "bullish" and bearish_prob > 0.30)
    
    # Entry logic (grouped by lots==0)
    if lots == 0:
        market_state_u = str(market_state).upper().strip()
        if (ai_signal == "HOLD" and ai_confidence >= 65 and bullish_prob >= 0.40 and 
            rsi <= 80 and market_state_u in ("IMPULSE_UP", "UP")):
            return "BUY1", f"TEST ENTRY HOLD+Conf{ai_confidence} Bull{bullish_prob:.2f} RSI{rsi:.1f}", metadata
        
        if ai_signal == "BUY" and effective_confidence >= min_entry_conf:
            return "BUY1", f"Entry Conf{effective_confidence} Bullish{bullish_prob:.2f}", metadata
        if is_bullish_hedge and rsi < 40:
            return "BUY1", f"Hedge Long Prob{bullish_prob:.2f} vs Bias{bias}", metadata
        
        if ai_signal == "SELL" and effective_confidence >= min_entry_conf:
            return "SELL1", f"Entry Conf{effective_confidence} Bearish{bearish_prob:.2f}", metadata
        if is_bearish_hedge and rsi > 60:
            return "SELL1", f"Hedge Short Prob{bearish_prob:.2f} vs Bias{bias}", metadata
    
    # TP/SL/Extreme (early grouped)
    if pnl_pct >= 10.0:
        return ("SELLALL" if lots > 0 else "BUYALL"), f"✅ TP PnL{pnl_pct:.2f}% ≥10%", metadata
    
    if sl_level > 0.01 and current_price is not None:
        if (lots > 0 and current_price <= sl_level) or (lots < 0 and current_price >= sl_level):
            side = "SELLALL" if lots > 0 else "BUYALL"
            return side, f"ATR SL hit price{current_price:.3f} SL{sl_level:.3f}", metadata
    
    # Extreme oversold block/rebalance
    if rsi < 20 and lots > 0 and ai_signal == "SELL":
        return "NOOP", f"Block SELLALL RSI{rsi:.1f}<20 oversold", metadata
    if lots > 0 and (bearish_prob > 0.65 or (ai_signal == "SELL" and effective_confidence > 80)):
        return "SELLALL", f"Emergency Bear {bearish_prob:.2f}", metadata
    if lots > 0 and bullish_prob < 0.40 and rsi > 70:
        return "SELLALL", "Partial TP bull weakening", metadata
    
    if lots < 0 and (bullish_prob > 0.65 or (ai_signal == "BUY" and effective_confidence > 80)):
        return "BUYALL", f"Emergency Bull {bullish_prob:.2f}", metadata
    if lots < 0 and bearish_prob < 0.40 and rsi < 30:
        return "BUYALL", "Partial TP bear weakening", metadata
    
    # Extreme RSI mean reversion (grouped)
    if lots == 0:
        if (rsi < 20 and bullish_prob > 0.15 and ai_signal != "SELL") or \
           (rsi < 20 and bias == "bearish" and bullish_prob > 0.25 and ai_signal != "SELL"):
            return "BUY1", f"🚨 OVERSOLD RSI{rsi:.1f} Bull{bullish_prob:.2f}", metadata
        if rsi > 80 and bearish_prob > 0.15 and ai_signal != "BUY":
            return "SELL1", f"🚨 OVERBOUGHT RSI{rsi:.1f} Bear{bearish_prob:.2f}", metadata
    
    # Gap rule
    if (trend_5m == "IMPULSE_UP" and lots == 0 and current_price and 
        5.28 <= current_price <= 5.36 and 72 <= rsi <= 76 and ai_signal == "HOLD"):
        return "BUY2", "⚡ IMMEDIATE Gap consolidation BUY2", metadata
    
    # HOLD enforcement
    if ai_signal == "HOLD" and lots == 0:
        return "NOOP", "🔒 HOLD: wait conviction", metadata

    # HOLD with position...
    if ai_signal == "HOLD" and lots != 0:
        return "NOOP", f"🔒 HOLD: keep position (lots={lots})", metadata

    # RECOMPUTE delta after all target_lots adjustments
    delta = target_lots - lots
    metadata.update({"delta": delta, "target_lots": target_lots, "current_lots": lots})

    # Final delta-driven
    if delta > 0:
        return f"BUY{abs(delta)}", f"GWDD Target{target_lots} cur{lots} BUY{abs(delta)}", metadata
    if delta < 0:
        return f"SELL{abs(delta)}", f"GWDD Target{target_lots} cur{lots} SELL{abs(delta)}", metadata


    
    return "NOOP", f"Aligned {lots}={target_lots} B{bullish_prob:.2f}S{bearish_prob:.2f}RSI{rsi:.1f}", metadata



def _env_bool(name: str, default: str = "false") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

import pytz

def _is_opening_noise_window() -> bool:
    tz = pytz.timezone("Europe/Moscow")
    now = datetime.now(tz).time()
    return time(9, 0) <= now <= time(9, 20)

async def post_order_guarded(
    executor: "MainOrderExecutor",
    figi: str,
    direction: Literal["BUY", "SELL"],
    quantity: int,
    *,
    why: str,
) -> bool:
    """
    Отправка ордеров + Telegram. ЧИСТАЯ — cooldown проверяется ДО вызова!
    """
    if quantity <= 0:
        print(f"🛑 post_order_guarded: qty<=0 ignored | {direction} {quantity} {figi} | {why}")
        return False

    try:
        res = await executor.post_order(figi, direction, quantity)
        if res:
            await send_telegram(f"✅ <b>ORDER OK</b>: {direction} {quantity} шт.\n{why}")
            return True

        # Отказ биржи (09:00-09:20 подавляем spam)
        if _is_opening_noise_window():
            print(f"⚠️ Exchange reject suppressed (09:00-09:20): {direction} {quantity} {figi} | {why}")
            return False

        await send_telegram(f"⚠️ <b>ОТКАЗ БИРЖИ:</b> {direction} {quantity} шт.\n{why}")
        return False

    except Exception as e:
        error_msg = f"❌ <b>ОШИБКА:</b> {direction}\n<code>{str(e)[:200]}</code>\n{why}"
        print(error_msg)
        await send_telegram(error_msg)
        return False


@dataclass
class ExecutionCooldownState:
    """Anti-duplicate orders: per-signal 120s + global 30s lock."""
    lastexecutiontime: Dict[Tuple[str, str], float] = field(default_factory=dict)
    lastconfirmedlots: Optional[int] = None
    globallockuntil: float = 0.0
    COOLDOWNDURATION: float = 120.0  # Per-signal
    GLOBALLOCKDURATION: float = 30.0  # BUYALL/SELLALL

    def hash_reason(self, reason: str) -> str:
        return hashlib.md5(reason.encode('utf-8')).hexdigest()[:8]

    def is_cooldown_active(self, action: str, reason: str, currenttime: float) -> Tuple[bool, Optional[float]]:
        # Global lock
        if currenttime < self.globallockuntil:
            remaining = self.globallockuntil - currenttime
            return True, remaining
        
        reasonhash = self.hash_reason(reason)
        key = (action, reasonhash)
        if key in self.lastexecutiontime:
            elapsed = currenttime - self.lastexecutiontime[key]
            if elapsed < self.COOLDOWNDURATION:
                remaining = self.COOLDOWNDURATION - elapsed
                return True, remaining
        return False, None

    def record_execution(self, action: str, reason: str, currenttime: float):
        reasonhash = self.hash_reason(reason)
        key = (action, reasonhash)
        self.lastexecutiontime[key] = currenttime
        if action in ("BUYALL", "SELLALL"):
            self.globallockuntil = currenttime + self.GLOBALLOCKDURATION

    def on_position_confirmed(self, newlots: int, currenttime: float):
        if self.lastconfirmedlots is None or self.lastconfirmedlots != newlots:
            self.lastexecutiontime.clear()
            self.globallockuntil = 0.0
            self.lastconfirmedlots = newlots

    def reset_all(self):
        self.lastexecutiontime.clear()
        self.globallockuntil = 0.0
        self.lastconfirmedlots = None


async def main_loop():
    
    from src.services.atr_stop import ATRStopEngine
    from src.shared_state import SharedTradingState
    
    sharedstate = SharedTradingState()
    atr_stop = ATRStopEngine()  # Использует defaults: k_sl_uptrend=2.0, k_sl_other=1.5, m_be=1.0

    prev_lots = 0

        # Initialize execution cooldown system
    execution_cooldown_state = ExecutionCooldownState()
    print(f"✅ EXECUTION COOLDOWN initialized: {execution_cooldown_state.COOLDOWNDURATION}s per signal, {execution_cooldown_state.GLOBALLOCKDURATION}s global lock")

    
    # Теперь это сработает для всех ключей
    token = settings.TINKOFF_TOKEN.get_secret_value()

    analyst = MarketAnalyst()
    planner = PlannerAgent()

    # === GWDD ENGINE INITIALIZATION ===
    gwdd_config = GWDDConfig(
        sigma_confidence=15.0,
        sigma_rsi=10.0,
        sigma_prob=0.15,
        global_min_weight=0.50,
        min_weight_conservative=0.55,
        min_weight_moderate=0.50,
        min_weight_aggressive=0.45,
        risk_mode_adjustments={
            "CONSERVATIVE": 0.5,
            "MODERATE": 1.0,
            "AGGRESSIVE": 1.5
        }
    )
    gwdd_engine = GWDDEngine(gwdd_config)
    risk_agent = RiskAgent()
    executor = MainOrderExecutor(token)
    news_agent = UnifiedNewsAgent()
    weather_monitor = SynopticMonitor()
    sharedstate = SharedTradingState()
    atr_stop = ATRStopEngine()  # Использует defaults: k_sl_uptrend=2.0, k_sl_other=1.5, m_be=1.0

    print("👻 Hybrid Architecture v2.0: Режим активного мониторинга запущен.")
    await send_telegram("🚀 Predator v2.0: Bayesian Engine + Synoptic Monitor активны.")

    cycle = 0
    position_timer = PositionTimer()
    MAX_LOTS_ALLOWED = 8
    prev_lots = 0
    # === SL COOLDOWN TRACKING ===
    last_sl_exit_cycle = -999  # Цикл когда был последний выход по SL
    last_sl_exit_price = 0.0   # Цена выхода по SL
    SL_COOLDOWN_CYCLES = 5     # Минимум циклов до повторного входа после SL


    # Инициализация entry_time при старте, если позиция открыта
    try:
        pos = await get_position_data_safe(executor, ACTIVE_FIGI, retries=3)
        start_lots = int(pos.get("lots", 0))
        if start_lots > 0 and sharedstate.entry_time is None:
            sharedstate.entry_time = time_module.time()
            print(f"⏰ Позиция {start_lots} лот при старте, entry_time установлен")
    except Exception as e:
        print(f"⚠️ Ошибка init entry_time: {e}")


    while True:
        try:
            # ===== per-cycle defaults (MUST be before any gate/continue) =====
            can_execute = False
            qty = 0
            block_reason = None
            trade_allowed = False
            is_position_reduction = False
            action = "NOOP"
            action_reason = ""
            direction = "BUY"
            orig_action = "NOOP"
            orig_qty = 0

            now_msk = dt.datetime.now(pytz.timezone("Europe/Moscow"))

            # Блокируем торговлю до 09:00 МСК (аукцион открытия, гэпы)
            if now_msk.hour < 9:
                if cycle == 0 or cycle % 10 == 0:
                    print(f"⏸ PRE-MARKET WINDOW: {now_msk.strftime('%H:%M:%S')} < 09:00 MSK, waiting...")
                await asyncio.sleep(60)
                continue

            is_open, status_msg = get_market_status()
            is_open, status_msg = get_market_status()

            if not is_open:
                await asyncio.sleep(60)
                continue

            cycle += 1
            print(f"\n⏳ --- CYCLE {cycle:06d} | {now_msk.strftime('%H:%M:%S')} ---")

            # 1. Получение данных о позиции
            pos = await get_position_data_safe(executor, ACTIVE_FIGI, retries=3)
            current_lots = int(pos["lots"])
            avg_price = float(pos["average_price"])
            
            if current_lots != prev_lots:
                execution_cooldown_state.on_position_confirmed(current_lots, time_module.time())
                print(f"POSITION CONFIRMED: {prev_lots}→{current_lots}, cooldowns RESET!")
            prev_lots = current_lots
            
            # === ATR STOP: OPEN/CLOSE DETECTION ===
            if prev_lots == 0 and current_lots != 0:
                # Открылась новая позиция
                direction = "LONG" if current_lots > 0 else "SHORT"  # ← ДИНАМИЧЕСКИЙ direction
                atr_0 = float(data.get("ATR", 0.015)) if 'data' in locals() else 0.015


                atr_stop.on_open(direction=direction, entry_price=avg_price, atr_0=atr_0)


                # Синхронизируем с shared state
                sharedstate.entry_price = avg_price
                sharedstate.atr_at_entry = atr_0
                sharedstate.position_direction = direction
                sharedstate.sl_level = atr_stop.get_sl() or 0.0
                sharedstate.p_high_since_entry = avg_price
                sharedstate.entry_time = time_module.time()
                sharedstate.p_low_since_entry = avg_price

            elif prev_lots != 0 and current_lots != 0 and sharedstate.sl_level == 0.0:
                direction = "LONG" if current_lots > 0 else "SHORT"
                atr_0 = float(data.get("ATR", 0.015)) if 'data' in locals() else 0.015

                atr_stop.on_open(direction=direction, entry_price=avg_price, atr_0=atr_0)
                sharedstate.sl_level = atr_stop.get_sl() or 0.0
                print(f"🔄 ATR Stop восстановлен: SL={sharedstate.sl_level:.3f}")
            elif prev_lots > 0 and current_lots == 0:
                # Позиция полностью закрыта
                atr_stop.on_close()
                sharedstate.entry_time = None
                sharedstate.sl_level = 0.0
                sharedstate.atr_at_entry = 0.0
                sharedstate.position_direction = ""
                sharedstate.p_high_since_entry = 0.0
                sharedstate.p_low_since_entry = 0.0

            prev_lots = current_lots


            # 2. Получение и анализ свечей
            candles = await executor.get_candles_5m(ACTIVE_FIGI)
            if candles.empty:
                print("⚠️ Свечи не получены, ожидание 10с...")
                await asyncio.sleep(10)
                continue

            data = pipeline_analysis(candles, "NRF6")
            current_price = float(data["close"])
            
            atr_t = float(data.get("ATR", 0.015))

            # ATR STOP: открытие / закрытие
            if prev_lots == 0 and current_lots != 0:
                direction = "LONG" if current_lots > 0 else "SHORT"  # ← ДИНАМИЧЕСКИЙ
                atr_stop.on_open(direction=direction, entry_price=avg_price, atr_0=atr_t, trend=trend_5m)



                st = atr_stop.get_state()
                if st is not None:
                    sharedstate.entry_price = st.entry_price
                    sharedstate.atr_at_entry = st.atr_at_entry
                    sharedstate.position_direction = st.direction
                    sharedstate.sl_level = st.sl_level
                    sharedstate.p_high_since_entry = st.p_high_since_entry
                    sharedstate.p_low_since_entry = st.p_low_since_entry

                    sharedstate.entry_time = time_module.time()
            elif prev_lots > 0 and current_lots == 0:
                atr_stop.on_close()
                sharedstate.entry_time = None
                sharedstate.sl_level = 0.0
                sharedstate.atr_at_entry = 0.0
                sharedstate.position_direction = ""
                sharedstate.p_high_since_entry = 0.0
                sharedstate.p_low_since_entry = 0.0

            prev_lots = current_lots

            # Если позиция уже открыта — просто подтягиваем стоп
            if current_lots > 0:
                atr_stop.on_update(price_t=current_price, atr_t=atr_t, trend=data.get("trend", "FLAT"))
                st = atr_stop.get_state()
                if st is not None:
                    sharedstate.sl_level = st.sl_level
                    sharedstate.p_high_since_entry = st.p_high_since_entry
                    sharedstate.p_low_since_entry = st.p_low_since_entry


            # Расчет PnL
            if current_lots > 0 and avg_price > 0:
                pnl_pct = ((current_price - avg_price) / avg_price) * 100
            else:
                pnl_pct = 0.0
            # Расчет времени удержания позиции
            if sharedstate.entry_time is not None:
                holding_hours = (time_module.time() - sharedstate.entry_time) / 3600
            else:
                holding_hours = 0.0
            rsi_val = float(data.get("RSI", 50.0))
            trend_5m = data.get("trend", "FLAT")
            current_volume = int(candles["volume"].iloc[-1]) if not candles.empty and "volume" in candles.columns else 0
            avg_volume_20 = int(candles["volume"].tail(20).mean()) if len(candles) >= 20 and "volume" in candles.columns else 1
            
            
            atr_t = float(data.get("ATR", 0.015))

            # === ATR STOP: OPEN/CLOSE & UPDATE ===
            # Открытие новой позиции
            if prev_lots == 0 and current_lots != 0:
                # TODO: когда появятся реальные шорты — добавить определение направления
                direction = "LONG" if current_lots > 0 else "SHORT"

                atr_stop.on_open(direction=direction, entry_price=avg_price, atr_0=atr_t, trend=trend_5m)

                st = atr_stop.get_state()
                if st is not None:
                    sharedstate.entry_price = st.entry_price
                    sharedstate.atr_at_entry = st.atr_at_entry
                    sharedstate.position_direction = st.direction
                    sharedstate.sl_level = st.sl_level
                    sharedstate.p_high_since_entry = st.p_high_since_entry
                    sharedstate.p_low_since_entry = st.p_low_since_entry

                    sharedstate.entry_time = time_module.time()  # Сохраняем время открытия
            # Полное закрытие позиции
            elif prev_lots > 0 and current_lots == 0:
                atr_stop.on_close()
                sharedstate.entry_time = None
                sharedstate.sl_level = 0.0
                sharedstate.atr_at_entry = 0.0
                sharedstate.position_direction = ""
                sharedstate.p_high_since_entry = 0.0
                sharedstate.p_low_since_entry = 0.0

            prev_lots = current_lots

            # Обновление стопа, если позиция открыта
            if current_lots != 0:
                atr_stop.on_update(price_t=current_price, atr_t=atr_t, trend=data.get("trend", "FLAT"))
                st = atr_stop.get_state()
                if st is not None:
                    sharedstate.sl_level = st.sl_level
                    sharedstate.p_high_since_entry = st.p_high_since_entry
                    sharedstate.p_low_since_entry = st.p_low_since_entry

            
            # === ATR STOP: UPDATE SL ===
            if current_lots != 0:
                atr_t = float(data.get("ATR", 0.015))
                atr_stop.on_update(price_t=current_price, atr_t=atr_t, trend=data.get("trend", "FLAT"))

                st = atr_stop.get_state()
                if st is not None:
                    sharedstate.sl_level = st.sl_level
                    sharedstate.atr_at_entry = st.atr_at_entry
                    sharedstate.p_high_since_entry = st.p_high_since_entry
                    sharedstate.p_low_since_entry = st.p_low_since_entry
                    sharedstate.position_direction = st.direction
                    
            print(
                f"💰 Price: {current_price:.3f} | RSI: {rsi_val:.1f} | "
                f"Lots: {current_lots} | PnL: {pnl_pct:.2f}% | SL: {sharedstate.sl_level:.3f}"
            )



            # 3. Синоптический мониторинг (погода)
            print("🌡️ Опрос метеослужб (Open-Meteo)...")
            weather_data = await weather_monitor.get_weather_impact()
            weather_str = weather_monitor.get_weather_context_str(weather_data)
            print(f"📡 {weather_str}")

            # 4. Фундаментал и новости (только news_fire.txt)
            manual_news = ""
            try:
                with open("news_fire.txt", "r", encoding="utf-8") as f:
                    manual_news = f.read().strip()
            except FileNotFoundError:
                print("⚠️ news_fire.txt не найден (создайте файл для ручных новостей)")
            except Exception as e:
                print(f"⚠️ Ошибка чтения news_fire.txt: {e}")
            print(f"📰 Новости: прочитано {len(manual_news)} символов (news_fire.txt)")

            rules = parse_trading_rules_from_news()
            current_bias = rules.get("bias", "neutral")
            
            # 5. NEWS_AGENT анализирует новости + техничку (кэш: обновление раз в 10 циклов)
            global last_news_result, news_cache_cycle
            
            if cycle % 10 == 1 or last_news_result is None:
                print("📰 NEWS_AGENT: Анализ новостей и фундамента (full refresh)...")
                full_context = f"{weather_str}\nНОВОСТИ:\n{manual_news}"
                news_result = await analyst.analyze(
                    marketdata=data,
                    newscontext=full_context,
                    bias=current_bias,
                )
                last_news_result = news_result
                news_cache_cycle = cycle
            else:
                print(f"📰 NEWS_AGENT: Используется кэш (цикл {news_cache_cycle}, след обновление через {10 - (cycle % 10)} циклов)...")
                news_result = last_news_result

            print(f"🤖 AI: {news_result.signal} | Conf: {news_result.confidence}%")
            print(
                f"📈 Prob: Bullish {news_result.bullish_prob*100:.0f}% | "
                f"Bearish {news_result.bearish_prob*100:.0f}%"
            )

            # 6. RISK_AGENT оценивает рыночный риск
            print("🛡️ RISK_AGENT: Оценка волатильности и входа...")
            risk_verdict = risk_agent.assess_risk(
                alpha_signal={
                    "signal": news_result.signal,
                    "confidence": news_result.confidence,
                    "reason": "",
                },
                market_data={
                    "ATR": data.get("ATR", 0.15),
                    "RSI": rsi_val,
                    "ATRSL": data.get("ATRSL", 0.05),
                    "ATRTP": data.get("ATRTP", 0.15),
                    "market_state": data.get(
                    "marketstate", data.get("market_state", "RANGE")
                    ),
                },
            )
            risk_allowed = bool(risk_verdict.get("allowed", False))
            # === EXTREME OVERSOLD OVERRIDE ===
            if not risk_allowed and rsi_val < 20 and news_result.bullish_prob > 0.20:
                risk_allowed = True
                print(f"✅ RISK OVERRIDE: EXTREME OVERSOLD RSI {rsi_val:.1f} < 20 | Bull {news_result.bullish_prob:.2f}")
                risk_verdict["allowed"] = True
                risk_verdict["reason"] = "EXTREME OVERSOLD override"
            print(
                f"✓ Risk Verdict: {risk_verdict['reason']} | "
                f"Risk Score: {risk_verdict['risk_score']}"
            )

            # --- PLANNER CONTEXT SAFE DEFAULTS ---
            trend_h1 = trend_5m
            trend_d1 = trend_5m
            

            ai_confidence = getattr(news_result, 'confidence', 50)

            market_context = {
                "ticker": "NG",
                "trend_5m": trend_5m,
                "trend_h1": locals().get("trend_h1", trend_5m),
                "trend_d1": locals().get("trend_d1", trend_5m),
                "market_state": data.get("market_state", data.get("marketstate","RANGE")),
                "news_summary": manual_news[:500],
                "ai_confidence": ai_confidence,
            }
          
            print("🧠 PLANNER: Синтез торговой стратегии...")
            # --- PLANNER CONTEXT SAFE DEFAULTS ---
            trend_h1 = trend_5m
            trend_d1 = trend_5m

            market_context = {
                "ticker": "NG",

               
                "trend_5m": trend_5m,
                "trend_h1": locals().get("trend_h1", trend_5m),
                "trend_d1": locals().get("trend_d1", trend_5m),

                # критично для override на импульсе/режиме рынка
                "market_state": data.get("market_state", data.get("marketstate","RANGE")),   # или data.get("marketstate")

                "news_summary": manual_news[:500],
            }
            

            try:
                plan_result = await asyncio.wait_for(
                    asyncio.to_thread(planner.create_plan, market_context),
                    timeout=30,  # ← УВЕЛИЧЕНО с 20 до 30
                )
            except asyncio.TimeoutError:
                print("⚠️ PLANNER TIMEOUT (30s) -> fallback CONSERVATIVE")
                plan_result = {
                    "bias": "NEUTRAL",  # ← Более безопасный fallback (вместо current_bias)
                    "risk_mode": "CONSERVATIVE",  # ← Добавлено (для совместимости)
                    "mode": "CONSERVATIVE",
                    "strategy": "NEUTRAL",
                    "reason": "planner timeout",
                    "force_weight": 0.55,  # ← КРИТИЧНО: чтобы проходил порог CONSERVATIVE
                }
            except Exception as e:
                print(f"⚠️ PLANNER ERROR -> fallback CONSERVATIVE: {e}")
                plan_result = {
                    "bias": "NEUTRAL",  # ← Более безопасный fallback
                    "risk_mode": "CONSERVATIVE",
                    "mode": "CONSERVATIVE",
                    "strategy": "NEUTRAL",
                    "reason": f"planner error: {e}",
                    "force_weight": 0.55,  # ← КРИТИЧНО
                }

            print(f"🧠 PLANNER RESULT: mode={plan_result.get('mode')} bias={plan_result.get('bias')} reason={plan_result.get('reason','')}")

            final_bias = plan_result.get("bias", "NEUTRAL")  # ← Fallback на NEUTRAL вместо current_bias


            # 8. Погода как фильтр
            weather_allowed = True
            block_reason = None
            # === SMART WEATHER OVERRIDE ===
            weather_impact = weather_data.get("demand_impact_pct", 0)
            arctic_score = weather_data.get("arctic_blast_score", 0.0)
            # FIX: убираем ложный extreme, если метрики не рассчитаны
            is_extreme = bool(weather_data.get("is_extreme")) and (weather_impact > 0 or arctic_score > 0)
            
            if is_extreme:
                # Проверяем EXTREME OVERSOLD override
                if rsi_val < 20 and news_result.bullish_prob > 0.20:
                    if weather_impact < 90:
                        # Обычный холод → full override
                        weather_allowed = True
                        print(f"✅ WEATHER OVERRIDE: RSI {rsi_val:.1f} < 20 > Weather (impact {weather_impact}%)")
                    else:
                        # Критический шторм → partial override (половина лота)
                        weather_allowed = True
                        MAX_LOTS_ALLOWED = max(1, MAX_LOTS_ALLOWED // 2)
                        print(f"⚠️ PARTIAL OVERRIDE: RSI {rsi_val:.1f} < 20 + CRITICAL Weather ({weather_impact}%) → 0.5x lots")
                else:
                    # Блокируем вход
                    weather_allowed = False
                    block_reason = f"WEATHER_ALERT: extreme conditions (impact {weather_impact}%, arctic {arctic_score:.2f}) -> block entries"
                    print(f"🌡️ Weather Impact: {weather_impact}% | Arctic Score: {arctic_score:.2f}")
            else:
                weather_allowed = True
            trade_allowed = risk_allowed and weather_allowed

            position_size = 0

            try:
                # Синхронизация режима Planner -> GWDD
                planner_mode = plan_result.get("risk_mode", "MODERATE")
                gwdd_mode = "MODERATE" if planner_mode == "NORMAL" else planner_mode
                print(f"⚖️ GWDD mode: {gwdd_mode} (Planner: {planner_mode})")
                
                print("📊 GWDD: Расчет веса входа...")
                entry_weight, gwdd_breakdown = await gwdd_engine.calculate_entry_weight(
                    ai_signal=news_result.signal,
                    confidence=news_result.confidence,
                    bullish_prob=news_result.bullish_prob,
                    bearish_prob=news_result.bearish_prob,
                    rsi=rsi_val,
                    market_state=data.get("market_state", "RANGE"),
                    risk_mode=gwdd_mode,
                    news_text=full_context,
                )
                
                
               
                # Override weight если задан FORCE_WEIGHT из Planner
                planner_force_weight = plan_result.get("force_weight")
                if planner_force_weight is not None:
                    entry_weight = planner_force_weight
                    print(f"⚡ FORCE_WEIGHT override (Planner): {entry_weight}")


                should_enter, weight_final, gwdd_reason = gwdd_engine.decide_entry(
                    entry_weight=entry_weight,
                    risk_mode=gwdd_mode,
                    ai_signal=news_result.signal,
                    rsi=rsi_val,
                )

                position_size = gwdd_engine.get_position_sizing(
                    entry_weight=entry_weight,
                    max_lots=MAX_LOTS_ALLOWED,
                    risk_mode=gwdd_mode,
                    rsi=rsi_val,
                )

                

            except Exception as e:
                print(f"⚠️ GWDD ERROR: {e}")
                entry_weight = 0.5
                position_size = 0
                should_enter = False
                gwdd_reason = f"Error: {e}"

            
            print(f"⚖️ GWDD Weight: {entry_weight:.3f} | Lots: {position_size}")
            print(f"   {gwdd_reason}")
            
            # Обновляем shared_state
            sharedstate.gwdd_weight = entry_weight
            sharedstate.suggested_lots = position_size
            

            # GWDD может перекрыть решение risk_agent
            if not should_enter and news_result.signal in ["BUY", "SELL"]:
                trade_allowed = False
                block_reason = f"GWDD_BLOCK: {gwdd_reason}"


            # Если уже в позиции, GWDD Lots=0 (SKIP) не должен обнулять target -> держим текущие лоты
            if current_lots != 0 and position_size == 0:
                position_size = abs(current_lots)

            
            
            # ⚡ GWDD: target_lots зависит от ai_signal (SELL = SHORT, отрицательный)
            if news_result.signal == "SELL":
                target_lots_signed = -position_size  # SHORT: отрицательные лоты
            else:
                target_lots_signed = position_size   # LONG: положительные лоты
            
            # 9. DECISION_BLOCK
            action, action_reason, decision_metadata = decide_action(
                lots=current_lots,
                target_lots=target_lots_signed,
                max_lots=MAX_LOTS_ALLOWED,
                ai_signal=news_result.signal,
                ai_confidence=news_result.confidence,
                bullish_prob=news_result.bullish_prob,
                bearish_prob=news_result.bearish_prob,
                trend_5m=trend_5m,
                rsi=rsi_val,
                bias=final_bias,
                rules=plan_result,
                market_state=data.get("marketstate", "RANGE"),
                minutes_to_clearing=get_minutes_to_clearing(),
                current_volume=current_volume,
                avg_volume=avg_volume_20,
                atr=data.get("ATR", 0.15),
                current_price=current_price,
                sl_level=sharedstate.sl_level,
                pnl_pct=pnl_pct,
                last_sl_exit_cycle=last_sl_exit_cycle,  
                current_cycle=cycle
            )
            
         
	
            # --- SLEEPING MARKET GATE (Variant B): block only GWDD Target rebalance in FLAT ---
            sleeping_market = (trend_5m == "FLAT")

            if sleeping_market and isinstance(action_reason, str) and action_reason.startswith("GWDD Target"):
                # Вариант B: блокируем только ребаланс GWDD Target в сонном рынке.
                # Аварийные выходы не должны начинаться с "GWDD Target", но оставим страховку.
                risk_exit_markers = (
                    "Clearing protection",
                    "Clearing lock",
                    "ATR SL",
                    "SL hit",
                    "STOP",
                    "Emergency",
                    "HOLD de-risk",
                    "FORCED",
                )
                is_risk_exit = any(m in action_reason for m in risk_exit_markers)

                if not is_risk_exit:
                    print(f"😴 SLEEPING MARKET: blocked {action} | {action_reason}")
                    action = "NOOP"
                    action_reason = f"SLEEPING MARKET: blocked {action_reason}"
            # --- END SLEEPING MARKET GATE ---

            
            # Разделяем ИСПОЛНЕНИЕ на управление позицией и новые входы
            if action != "NOOP":
                direction = "BUY" if action.startswith("BUY") else "SELL"

                # 1) Определяем, уменьшает ли действие текущую позицию (risk-reduction)
                #    LONG: SELL уменьшает |lots|, SHORT: BUY уменьшает |lots|
                is_position_reduction = (
                    (direction == "SELL" and current_lots > 0) or
                    (direction == "BUY" and current_lots < 0)
                )

  
                # 2) Исполнение разрешено если:
                # Определяем is_risk_exit ПЕРЕД can_execute
                is_risk_exit = isinstance(action_reason, str) and any(m in action_reason for m in ("ATR SL", "SL hit", "Emergency", "Clearing protection", "Clearing lock", "TP"))

                # КРИТИЧНО: risk exits ВСЕГДА исполняются (даже если trade_allowed=False)
                can_execute = (is_position_reduction and is_risk_exit) or trade_allowed

                # DEBUG: показать почему can_execute=True/False
                if is_position_reduction and is_risk_exit:
                    print(f"✅ RISK EXIT OVERRIDE: can_execute=True (reduction={is_position_reduction}, risk_exit={is_risk_exit})")


                
                # 3) Clearing lock: за 30 минут до клиринга запрещаем увеличивать риск (|lots|)
                minutes_to_clearing = get_minutes_to_clearing()
                if minutes_to_clearing <= 30:
                    # Разрешаем только снижение позиции/закрытие, даже если trade_allowed=True
                    if not is_position_reduction and action not in ("BUYALL", "SELLALL"):
                        can_execute = False
                        why_block = f"Clearing lock ({minutes_to_clearing}m): no add lots before clearing"
                
                       
                        
                # --- 4) Парсим qty (FIX: разные префиксы BUY/SELL) ---
                qty = 1
                if action in ("BUYALL", "SELLALL"):
                    qty = abs(current_lots)
                else:
                    if action.startswith("BUY"):
                        suffix = action[3:]
                    elif action.startswith("SELL"):
                        suffix = action[4:]
                    else:
                        suffix = ""

                    qty = int(suffix) if suffix.isdigit() else 1

                # ВАЖНО: orig_* должны задаваться всегда (и для BUYALL/SELLALL тоже)
                orig_action = action
                orig_qty = qty


                # --- 5) ANTI-FLIP CLAMP: запрет пересекать 0 (только закрыть до нуля) ---
                # Если сейчас SHORT, BUY не может быть больше abs(short)
                if direction == "BUY" and current_lots < 0:
                    qty = min(qty, abs(current_lots))
                # Если сейчас LONG, SELL не может быть больше abs(long)
                elif direction == "SELL" and current_lots > 0:
                    qty = min(qty, abs(current_lots))

                # ВАЖНО: CLAMP-метку делаем ДО исполнения, чтобы why= ушёл правильный
                if qty != orig_qty:
                    action = f"{direction}{qty}"
                    action_reason = f"{action_reason} | CLAMP {orig_action}->{action} (lots={current_lots})"
                    
                # === COOLDOWN CHECK BLOCK 4 ===
                now = time_module.time()
                shouldskip, remaining = execution_cooldown_state.is_cooldown_active(action, action_reason, now)
                if shouldskip:
                    skip_reason = f"COOLDOWN {action} {remaining:.1f}s (duplicate protection)"
                    print(skip_reason)
                    continue  # Пропуск цикла!

                # Record success ДОЛЖЕН БЫТЬ после post_order_guarded success

                can_execute = bool(is_position_reduction or trade_allowed)
                if action == "NOOP":
                    can_execute = False
                    qty = 0
                
                # --- DEBUG EXEC (compact) ---
                why_block = (
                    block_reason
                    or risk_verdict.get("reason")
                    or ("NOOP" if action == "NOOP" else None)
                    or "unknown"
                )

                print(
                    f"DEBUG_EXEC cycle={cycle} action={action} dir={direction} qty={qty} "
                    f"lots={current_lots} can={can_execute} allowed={trade_allowed} "
                    f"reduct={is_position_reduction} why={why_block}"
                )

                print(
                    f"DEBUG_GATES trade_allowed={trade_allowed} "
                    f"block_reason={block_reason} action_reason={action_reason}"
                )
                
                # GWDD BLOCK: Если trade_allowed=False, блокируем исполнение (кроме risk exits!)
                if not trade_allowed and not (is_position_reduction and is_risk_exit):
                    print(f"🚫 BLOCKED: {block_reason}")
                    action = "NOOP"
                    action_reason = f"Blocked: {block_reason}"
                else:
                    if is_position_reduction and is_risk_exit:
                        print(f"✅ RISK EXIT ALLOWED despite trade_allowed=False")

                    
                                
                # === SL COOLDOWN CHECK ===
                if last_sl_exit_cycle >= 0 and not is_position_reduction:
                    cycles_since_sl = cycle - last_sl_exit_cycle
                    
                    if cycles_since_sl < SL_COOLDOWN_CYCLES:
                        # Недавний выход по SL
                        price_diff_pct = abs(current_price - last_sl_exit_price) / last_sl_exit_price * 100
                        
                        if price_diff_pct < 1.0:  # Цена в той же зоне (±1%)
                            trade_allowed = False
                            block_reason = f"SL_COOLDOWN: {cycles_since_sl}/{SL_COOLDOWN_CYCLES} cycles since SL exit @ {last_sl_exit_price:.3f}"
                            print(f"🚫 {block_reason}")
    

               

                if can_execute and qty > 0:
                    print(f"➡️ EXECUTION: {action} | Risk Score: {risk_verdict.get('risk_score', 0)}")
                    print(f"   -> CLAMPED: direction={direction} qty={qty} current_lots={current_lots}")
                    
                    # КРИТИЧНО: Финальная проверка перед отправкой ордера
                    if action == "NOOP":
                        print(f"⛔ ORDER BLOCKED: action=NOOP after trade_allowed check")
                    else:
                        ok = await post_order_guarded(executor, ACTIVE_FIGI, direction, qty, why=action_reason)
                        
                        if ok:
                            await asyncio.sleep(0.7)
                            pos = await executor.get_position_data(ACTIVE_FIGI)
                            current_lots = int(pos.get("lots", current_lots))
                            print(f"✅ POSITION AFTER ORDER: {pos}")
                                                        
                            # === ЗАПОМИНАЕМ SL EXIT ===
                            if "SL hit" in action_reason and current_lots == 0:
                                last_sl_exit_cycle = cycle
                                last_sl_exit_price = current_price
                                print(f"🚨 SL EXIT записан: cycle={cycle}, price={current_price:.3f}")

                            print(f"DEBUG_SYNC lots_before_log: current_lots={current_lots}, pos_lots={pos.get('lots')}")
                            
                            # Инициализация Trailing Stop ПОСЛЕ успешного ордера
                            if current_lots > 0 and direction == "BUY":
                                print(f"🔧 DEBUG MAIN: atr_t={atr_t:.4f}, trend={trend_5m}, entry={pos.get('average_price', current_price):.4f}")
                                
                                trailing_stop = TrailingStopManager(
                                    entry_price=pos.get("average_price", current_price),
                                    atr=atr_t,  # ← ИСПРАВЛЕНО
                                    trend=trend_5m  # ← ИСПРАВЛЕНО (trend может быть не определён)
                                )
                            elif current_lots < 0 and direction == "SELL":
                                print(f"🔧 DEBUG MAIN SHORT: atr_t={atr_t:.4f}, trend={trend_5m}, entry={pos.get('average_price', current_price):.4f}")
                                
                                trailing_stop = TrailingStopManagerShort(
                                    entryprice=pos.get("average_price", current_price),
                                    atr=atr_t,  # ← ИСПРАВЛЕНО
                                    trend=trend_5m  # ← ИСПРАВЛЕНО
                                )




                else:
                    why_block = block_reason or risk_verdict.get("reason", "Blocked by filter")
                    print(f"🚫 BLOCKED: {why_block}")




            # === LOG DECISION & ENRICH WITH BROKER DATA ===
            cycle_event = log_decision_block(
                cycle=cycle,
                price=current_price,
                rsi=rsi_val,
                trend=trend_5m,
                lots=current_lots,
                holding_hours=position_timer.get_holding_hours(),
                ai_signal=news_result.signal,
                ai_confidence=news_result.confidence,
                bias=final_bias,
                minutes_to_clearing=get_minutes_to_clearing(),
                rules=plan_result,
                action=action,
                reason=action_reason,
                pnl_pct=pnl_pct,
                forced_entry=decision_metadata.get("forced_entry", False),
                consecutive_signals=decision_metadata.get("consecutive_signals", 0),
                avg_confidence=decision_metadata.get("avg_confidence", 0.0),
            )
            
            # Обогащаем данными из broker API
            enriched_event = await enrich_cycle_event(
                cycle_data=cycle_event,
                executor=executor,
                active_figi=ACTIVE_FIGI,
                session_id=session_id
            )
            
            # Записываем обогащённое событие в новый JSONL
            try:
                with open(jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(enriched_event, ensure_ascii=False) + "\n")
                    f.flush()
            except Exception as e:
                print(f"⚠️ WARN: Failed to write enriched JSONL: {e}")


            if getattr(settings, "TELEGRAM_DECISION_NOTIFICATIONS", False):
                if can_execute and qty > 0:
                    await send_telegram(
                        f"🧠 <b>DECISION</b>\n"
                        f"Cycle: {cycle}\n"
                        f"Lots: {current_lots}\n"
                        f"AI: {news_result.signal} {news_result.confidence:.0f}\n"
                        f"Action: {action}\n"
                        f"Reason: {action_reason}"
                    )





            await asyncio.sleep(60)

        except Exception as e:
            print(f"💥 Critical Error in main_loop: {e}")
            import traceback

            traceback.print_exc()
            await asyncio.sleep(10)




if __name__ == "__main__":
    asyncio.run(main_loop())
