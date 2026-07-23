import asyncio
import os
os.environ["USE_DEFAULT_ENUM_IF_ERROR"] = "true"
import pandas as pd
import numpy as np
import json
import uuid
from src.utils.jsonl_enrichment import enrich_cycle_event
import re
import json
import uuid
from dotenv import load_dotenv
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
from zoneinfo import ZoneInfo  # Для точного времени МСК
from src.core.multi_agent_adapter import MultiAgentShadowAdapter
from src.agents.planner import PlannerAgent
from src.agents.risk_agent import RiskAgent
from src.tools.news_aggregator import UnifiedNewsAgent
from src.core.gwdd_engine import GWDDEngine, GWDDConfig
from src.shared_state import SharedTradingState
import datetime as dt
import pytz
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional
import hashlib
from acontext import AcontextClient
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path
import datetime as dt
import pytz
import yaml

from src.services.sleeping_market import SleepingMarketDetector, SleepingMarketInput
from src.config.settings import settings
from src.daily_limits import DailyLimitsManager, DailyLimitsConfig
from hermes_upgrades.calendar_injector import CalendarInjector
import datetime as dt_module

load_dotenv()

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
    if weekday == 6:
        return False, "Воскресенье: рынок закрыт"
    
    # Суббота: торги 09:50 - 23:50 (на час позже)
    if weekday == 5:
        start_trade = dt.time(9, 50)
        end_trade = dt.time(23, 50)
        if not (start_trade <= current_time <= end_trade):
            return False, f"Суббота: торги 09:50 - 23:50 MSK"
    else:
        # Пн-Пт: торги 10:00 - 23:50
        start_trade = dt.time(10, 0)
        end_trade = dt.time(23, 50)
        if not (start_trade <= current_time <= end_trade):
            return False, f"Вне времени торгов (10:00 - 23:50)"

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
TRADE_HISTORY_PATH = BASE_DIR / "data" / "trade_history.csv"

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
MAX_LOTS = 10

# ========== КОНСТАНТЫ КОМИССИЙ И ПРОФИТА ==========
COMMISSION_PER_TRADE = 0.39  # руб за операцию (вход или выход)
MIN_PROFIT_PCT = 0.40  # минимальный профит с учётом комиссий
COOLDOWN_AFTER_LOSS_MINUTES = 15  # охлаждение после убыточной сделки
COOLDOWN_AFTER_PROFIT_MINUTES = 0  # нет охлаждения после прибыли
# GWDD state for decide_action scaling
GWDD_TARGET_LOTS = 1
GWDD_WEIGHT = 0.0

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
last_news_mtime = 0.0        # Время последнего изменения файла news_fire.txt
last_sleep_log_time: Optional[dt.datetime] = None

# ========== SESSION ID & JSONL ==========
session_id = str(uuid.uuid4())[:8]
jsonl_path = f"data/decisions_{dt.datetime.now().strftime('%Y%m%d')}.jsonl"


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
    for attempt in range(retries):
        try:
            pos = await executor.get_position_data(figi)
            if pos is not None and pos.get("lots") is not None:
                return pos
        except Exception as e:
            print(f"⚠️ get_position_data attempt {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
    
    print(f"❌ Failed after {retries} retries. Using fallback.")
    return {
        "lots": 0,
        "average_price": 0.0,
        "current_price": 0.0,
        "expected_yield": 0.0  # ✅ Убедись что это поле есть
    }


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
        atr_multiplier_other: float = 2.5,     # ← УВЕЛИЧЕНО (было 0.8)
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
        atrmultiplierother: float = 2.5,       # ← УВЕЛИЧЕНО
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
        
        КРИТИЧНО: expected_yield — это вариационная маржа от расчётной цены (settlement),
        используется для правильного расчёта PnL в фьючерсах.
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
                        
                        # Извлекаем expected_yield (вариационная маржа в рублях)
                        expected_yield = (
                            p.expected_yield.units
                            + p.expected_yield.nano / 1e9
                        )
                        
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
                            "expected_yield": float(expected_yield)  # ✅ Вариационная маржа
                        }
                
                # Если в цикле не нашли позицию по figi, значит ее нет
                return {"lots": 0, "average_price": 0.0, "expected_yield": 0.0}
                
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
                        ) if hasattr(op, "commission") and op.commission else 0.0,
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
            model="gemini-1.5-flash",
            temperature=0.0,  # Строгость ответов
            api_key=os.environ.get("GEMINI_API_KEY", settings.OPENROUTER_API_KEY.get_secret_value()),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
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
    "4. ФУНДАМЕНТ/НОВОСТИ: {news}.\n\n"
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
    Читает trade_history.csv и восстанавливает время входа.
    КРИТИЧНО: история хранит UTC, но бот работает в UTC+4 локальном!
    """
    try:
        if not TRADE_HISTORY_PATH.exists():
            print("⏱️ PositionTimer: trade_history.csv не найден")
            return None

        with TRADE_HISTORY_PATH.open("r", encoding="utf-8") as f:
            lines = f.readlines()

        if not lines or len(lines) <= 1:
            print("⏱️ PositionTimer: trade_history.csv пуст")
            return None

        # Ищем последнюю запись с открытием позиции
        for line in reversed(lines[1:]):
            line = line.strip()
            if not line:
                continue

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

            if lots_after <= 0:
                continue

            try:
                entry_time = datetime.fromisoformat(time_str)
                # ✅ trade_history уже в UTC, ничего не преобразуем
            except Exception:
                continue

            print(f"⏱️ PositionTimer: восстановлено {entry_time.isoformat()}")
            return entry_time  # ✅ ВЕРНУТЬ ЗНАЧЕНИЕ!


        print("⏱️ PositionTimer: не найдено подходящих записей")
        return None

    except Exception as e:
        print(f"⚠️ Ошибка чтения trade_history.csv: {e}")
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
    pnl_pct: float,  # Session PnL
    holding_hours: float,
    ai_signal: str,
    ai_confidence: int,
    bias: str,
    action: str,
    reason: str,
    rules: dict,
    minutes_to_clearing: int = 999,
    forced_entry: bool = False,
    consecutive_signals: int = 0,
    avg_confidence: float = 0.0,
    position_pnl_pct: float = 0.0,
    sleeping_market: bool = False,
    sleeping_reason: str = "N/A",
    adaptive_sl_multiplier: float = 3.5,
    sl_level: float = 0.0,
    cooldown_active: bool = False,
    cooldown_remaining: int = 0,
    daily_trades_count: int = 0,
    daily_pnl_total: float = 0.0,
    daily_trades_remaining: int = 15,
    daily_limit_blocked: bool = False,
    news_context: str = "",
):

    """
    Выводит блок принятия решения в консоль и записывает его в shadow_agents_log_{date}.jsonl.
    """
    import json
    from datetime import datetime
    import pytz
    from pathlib import Path
    
    # Безопасное извлечение из rules
    trend_htf = str(rules.get("trend_htf", "N/A")).upper() if rules else "N/A"
    trend_override_reason = str(rules.get("trend_override_reason", "")) if rules else ""
    
    # Типизация
    ai_signal = str(ai_signal).upper()
    bias = str(bias).upper()
    action = str(action)
    reason = str(reason)
    
    # 1. Визуальный вывод в консоль
    # 1. Визуальный вывод в консоль
    print("\n" + "="*70)
    print(f"📊 DECISION BLOCK | Cycle: {cycle} | {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M:%S')}")
    print("-" * 70)
    print(f"💰 Price: {price:.3f} | RSI: {rsi:.1f} | Trend LTF: {trend} | Trend HTF: {trend_htf}")

    # ✅ ИСПРАВЛЕННАЯ СТРОКА:
    if lots != 0:
        print(f"📦 Lots: {lots} | Position PnL: {position_pnl_pct:+.2f}% | Session: {pnl_pct:+.2f}% | Holding: {holding_hours:.1f}h")
    else:
        print(f"📦 Lots: {lots} | PnL: 0.00% | Holding: {holding_hours:.1f}h")

    print(f"🤖 AI: {ai_signal} ({ai_confidence:.0f}%) | BIAS: {bias}")

    if trend_override_reason:
        print(f"🧭 Trend override: {trend_override_reason}")

    print(f"➡️ ACTION: {action}")
    print(f"📝 Reason: {reason}")

    
    # НОВЫЙ ВЫВОД для форсированных входов
    if forced_entry:
        print(f"🚨 FORCED ENTRY: {consecutive_signals} consecutive signals (avg Conf {avg_confidence:.1f}%)")
    
    print("="*70)
    
    # 2. JSONL запись
    log_entry = {
        "cycle": cycle,
        "timestamp": datetime.now(pytz.timezone('Europe/Moscow')).isoformat(),
        "price": price,
        "rsi": rsi,
        "trend_ltf": trend,
        "trend_htf": trend_htf,
        "trend_override": trend_override_reason,
        "lots": lots,
        "pnl_pct": pnl_pct,
        "holding_hours": holding_hours,
        "ai_signal": ai_signal,
        "ai_confidence": ai_confidence,
        "bias": bias,
        "action": action,
        "reason": reason,
        "minutes_to_clearing": minutes_to_clearing,
        "forced_entry": forced_entry,
        "consecutive_signals": consecutive_signals,
        "avg_confidence": avg_confidence,
        "sleeping_market": sleeping_market,
        "sleeping_reason": sleeping_reason,
        "adaptive_sl_multiplier": adaptive_sl_multiplier,
        "sl_level": sl_level,
        "cooldown_active": cooldown_active,
        "cooldown_remaining": cooldown_remaining,
        "daily_trades_count": daily_trades_count,
        "daily_pnl_total": round(daily_pnl_total, 2),
        "daily_trades_remaining": daily_trades_remaining,
        "daily_limit_blocked": daily_limit_blocked,
        "position_pnl_pct": position_pnl_pct,
        "news_context": news_context,
    }
    
    # 3. Запись в JSONL (дефолтный путь без LOGS_DIR)
    try:
        # Используем текущую директорию или 'logs'
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / f"shadow_agents_log_{datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y%m%d')}.jsonl"
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            f.flush()
        
    except Exception as e:
        print(f"⚠️ WARN: Error writing to log: {type(e).__name__}: {e}")
    
    # 4. Возвращаем log_entry для дальнейшего обогащения
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
    sharedstate = None,
) -> Tuple[str, str, Dict[str, Any]]:
        
    """
    Hybrid Decision Engine v2.1 (patched ordering):
    - Risk exits (TP/SL/Emergency/Clearing) evaluated BEFORE any HOLD/NOOP locks.
    - metadata includes is_risk_exit flag for executor override.
    """
    

    global consecutive_buy_signals, buy_signals_history, consecutive_sell_signals, sell_signals_history
    
    

    metadata: Dict[str, Any] = {
        "forced_entry": False,
        "consecutive_signals": 0,
        "avg_confidence": 0.0,
        "is_risk_exit": False,
    }

    # Normalize inputs
    ai_signal_u = str(ai_signal).upper().strip()
    trend_u = str(trend_5m).upper().strip()
    market_state_u = str(market_state).upper().strip()
    
    # ---------- HERMES UPGRADES (EIA & Liquidity) ----------
    current_time_msk = dt_module.datetime.now(ZoneInfo("Europe/Moscow"))
    
    # 1. EIA Blackout (Thursdays 17:25 - 17:40 MSK)
    if current_time_msk.weekday() == 3 and current_time_msk.hour == 17 and 25 <= current_time_msk.minute <= 40:
        if lots == 0:
            return "NOOP", "🚫 EIA BLACKOUT: Trading suspended during inventory report.", metadata
            
    # 2. Liquidity Window (Core Hours: 16:00 - 21:30 MSK)
    if not (16 <= current_time_msk.hour <= 21):
        if lots == 0 and market_state_u == "RANGE":
            return "NOOP", "🚫 LIQUIDITY TRAP: Trading suspended outside core hours (16:00-21:30) in RANGE.", metadata
            
            # ---------- RSI GATE (context-aware) ----------
    # Dynamic RSI thresholds based on trend context.
    trend_htf = str(rules.get("trend_htf", "NA")).upper() if rules else "NA"
    
    # Determine SHORT threshold
    if trend_htf == "BEARISH":
        rsi_threshold_short = 25  # aggressive in confirmed downtrend
    elif trend_u in ("FLAT", "RANGE") and trend_htf in ("NEUTRAL", "NA"):
        rsi_threshold_short = 30  # conservative in sideways
    else:
        rsi_threshold_short = 30  # default
    
    # Determine LONG threshold (symmetric)
    if trend_htf == "BULLISH":
        rsi_threshold_long = 75  # aggressive in confirmed uptrend
    elif trend_u in ("FLAT", "RANGE") and trend_htf in ("NEUTRAL", "NA"):
        rsi_threshold_long = 70  # conservative in sideways
    else:
        rsi_threshold_long = 70  # default
    
        # Apply gates (entry only, any trend)
    if lots == 0:
        if ai_signal_u == "BUY" and rsi >= rsi_threshold_long:
            return "NOOP", f"🚫 RSI GATE: BLOCK BUY, RSI {rsi:.1f}>={rsi_threshold_long} (trend_LTF={trend_u}, HTF={trend_htf}, state={market_state_u})", metadata
        if ai_signal_u == "SELL" and rsi <= rsi_threshold_short:
            return "NOOP", f"🚫 RSI GATE: BLOCK SELL, RSI {rsi:.1f}<={rsi_threshold_short} (trend_LTF={trend_u}, HTF={trend_htf}, state={market_state_u})", metadata

        
    # ========== SLEEPING MARKET DETECTOR ==========
    # КРИТИЧНО: Блокировка входов в боковике с низкой волатильностью
    # Размещение: ПОСЛЕ RSI GATE, ПЕРЕД POST-SL COOLDOWN
    # Приоритет: #1 (устраняет 70% убыточных сделок)

    if lots == 0:  # Проверяем только для новых входов
        # Входные данные для детектора
        sleeping_market_enabled = True  # TODO: перенести в config
        sleeping_atr_threshold = 0.020  # Порог ATR для NG (подстроить под рынок)
        
        # Условие 1: Низкий ATR (волатильность)
        is_low_atr = atr < sleeping_atr_threshold
        
        # Условие 2: Trend LTF должен быть FLAT
        is_flat_trend = trend_u == "FLAT"
        
        # Условие 3: RSI в нейтральной зоне (35-70)
        # Даже в FLAT, если RSI экстремальный → может быть пробой
        is_rsi_neutral = 35 <= rsi <= 70
        
        # Условие 4: Узкий дневной диапазон (опционально, требует price_history)
        # TODO: добавить проверку daily_range_pct < 2.0% если есть данные
        
        # SLEEPING MARKET = все условия выполнены
        is_sleeping_market = (
            sleeping_market_enabled
            and is_low_atr
            and is_flat_trend
            and is_rsi_neutral
        )
        
        if is_sleeping_market:
            sleep_reason = (
                f"🛌 SLEEPING MARKET BLOCK: "
                f"ATR={atr:.4f}<{sleeping_atr_threshold}, "
                f"trend={trend_u}, "
                f"RSI={rsi:.1f} [35-70]"
            )
            
            # Обновляем metadata для логирования
            metadata["sleeping_market"] = True
            metadata["sleeping_reason"] = sleep_reason
            
            print(f"🚫 {sleep_reason}")  # Логируем в консоль
            return "NOOP", sleep_reason, metadata
        else:
            # Рынок активен — разрешаем торговлю
            metadata["sleeping_market"] = False
            if not is_low_atr:
                metadata["sleeping_reason"] = f"ATR {atr:.4f} >= threshold {sleeping_atr_threshold}"
            elif not is_flat_trend:
                metadata["sleeping_reason"] = f"Trend {trend_u} != FLAT"
            elif not is_rsi_neutral:
                metadata["sleeping_reason"] = f"RSI {rsi:.1f} outside [35-70]"

    # ========== КОНЕЦ SLEEPING MARKET DETECTOR ==========

    
    
    # ---------- POST-SL COOLDOWN (П.4: ПРОГРЕССИВНЫЙ) ----------
    # Прогрессивный cooldown: чем больше убытков подряд, тем дольше пауза
    if lots == 0 and last_sl_exit_cycle >= 0:
        cycles_since_sl = current_cycle - last_sl_exit_cycle
        
        # Считаем количество последовательных убытков (из metadata или глобальной переменной)
        # TODO: Добавить счётчик consecutive_losses в shared state
        # Пока используем адаптивную логику на основе уверенности
        
        # Базовая прогрессия: 5, 10, 15, 20, 30 минут
        if ai_confidence < 60:
            # Низкая уверенность → долгий cooldown (3+ убытка подряд)
            cooldown_cycles = 20  # 20 minutes
        elif ai_confidence < 70:
            # Средняя уверенность → средний cooldown (2 убытка)
            cooldown_cycles = 10  # 10 minutes
        elif 70 <= ai_confidence < 85:  # ✅ ИСПРАВЛЕН БАГ
            # Высокая уверенность → короткий cooldown (1 убыток)
            cooldown_cycles = 5   # 5 minutes
        else:
            # Очень высокая уверенность → минимальный cooldown
            cooldown_cycles = 3   # 3 minutes
        
        # Дополнительная защита: в FLAT увеличиваем cooldown в 1.5x
        if trend_u == "FLAT":
            cooldown_cycles = int(cooldown_cycles * 1.5)
            cooldown_reason = f"🚫 COOLDOWN (FLAT×1.5x): SL exit {cycles_since_sl}/{cooldown_cycles} cycles ago"
        else:
            cooldown_reason = f"🚫 COOLDOWN: SL exit {cycles_since_sl}/{cooldown_cycles} cycles ago, conf={ai_confidence}"
        
        if cycles_since_sl < cooldown_cycles and ai_signal_u in ("BUY", "SELL"):
            metadata["cooldown_active"] = True
            metadata["cooldown_remaining"] = cooldown_cycles - cycles_since_sl
            return "NOOP", cooldown_reason, metadata
        else:
            metadata["cooldown_active"] = False



    # ---------- 0) RISK EXITS FIRST (always win) ----------
    # 0.1 Take Profit 3-level system (1.5%, 3%, 5%)
    # TP1: partial close at 1.5% (50% position ONCE)
    # TP3: full exit at 5.0% (trailing stop safety net)
    if lots != 0 and pnl_pct >= 5.0:
        metadata["is_risk_exit"] = True
        side = "SELLALL" if lots > 0 else "BUYALL"
        return side, f"✅ TP3 PnL{pnl_pct:.2f}% ≥5.0%, full exit (strong trend)", metadata

    # TP2: close remaining 50% at 3.0%
    if lots != 0 and pnl_pct >= 3.0:
        metadata["is_risk_exit"] = True
        
        # Close remaining position (1-3 lots left after TP1)
        if abs(lots) >= 2:
            close_qty = max(1, abs(lots) // 2)
            side = f"SELL{close_qty}" if lots > 0 else f"BUY{close_qty}"
            return side, f"✅ TP2 PnL{pnl_pct:.2f}% ≥3.0%, close {close_qty} lots", metadata
        elif abs(lots) == 1:
            side = "SELLALL" if lots > 0 else "BUYALL"
            return side, f"✅ TP2 PnL{pnl_pct:.2f}% ≥3.0%, full exit (1 lot)", metadata

    # TP1: close 50% at 1.5%
    if lots != 0 and pnl_pct >= 1.5:
        metadata["is_risk_exit"] = True
        
        # LONG: 5 lots → sell 50% = 2-3 lots at TP1
        # SHORT: -5 lots → buy 50% = 2-3 lots at TP1
        if abs(lots) >= 4:  # Close ~50% if position >= 4 lots
            close_qty = max(1, abs(lots) // 2)  # 50% rounded
            side = f"SELL{close_qty}" if lots > 0 else f"BUY{close_qty}"
            return side, f"✅ TP1 PnL{pnl_pct:.2f}% ≥1.5%, partial close {close_qty} lots (~50%)", metadata
        
        # If 1-3 lots: hold for TP2/TP3 (don't micro-exit)




    # 0.2 ATR Stop
    if lots != 0 and sl_level > 0.01 and current_price is not None:
        sl_hit = (lots > 0 and current_price <= sl_level) or (lots < 0 and current_price >= sl_level)
        if sl_hit:
            metadata["is_risk_exit"] = True
            side = "SELLALL" if lots > 0 else "BUYALL"
            return side, f"ATR SL hit price{current_price:.3f} SL{sl_level:.3f}", metadata

    # 0.3 Emergency exits (keep your logic, but only when position exists)
    # NOTE: these are risk exits too
        # 0.3 Emergency exits (only when position exists)
    # NOTE: these are risk exits too
    is_extreme = any(word in str(rules).lower() for word in ["vortex", "extreme", "arctic", "noaa"])
    effective_confidence = ai_confidence + (20 if is_extreme else 0)

    if lots > 0:
        # Emergency exit on strong bearish reversal (ANY PnL)
        if bearish_prob > 0.65 or (ai_signal_u == "SELL" and effective_confidence > 80):
            metadata["is_risk_exit"] = True
            return "SELLALL", f"Emergency Bear {bearish_prob:.2f}", metadata
        
        # Partial TP when bullish momentum weakens (ONLY if profitable)
        if bullish_prob < 0.40 and rsi > 70 and pnl_pct > 0.5:
            metadata["is_risk_exit"] = True
            return "SELLALL", f"Partial TP bull weakening (PnL {pnl_pct:.2f}%)", metadata

    if lots < 0:
        # Emergency exit on strong bullish reversal (ANY PnL)
        if bullish_prob > 0.65 or (ai_signal_u == "BUY" and effective_confidence > 80):
            metadata["is_risk_exit"] = True
            return "BUYALL", f"Emergency Bull {bullish_prob:.2f}", metadata
        
        # Partial TP when bearish momentum weakens (ONLY if profitable)
        if bearish_prob < 0.40 and rsi < 30 and pnl_pct > 0.5:
            metadata["is_risk_exit"] = True
            return "BUYALL", f"Partial TP bear weakening (PnL {pnl_pct:.2f}%)", metadata


    # 0.4 Clearing protection (ENTRIES blocked, but risk exits above are allowed)
    if minutes_to_clearing <= 3:
        # If flat (no position) — do nothing
        if lots == 0:
            return "NOOP", f"⏱️ Clearing protection ({minutes_to_clearing}min)", metadata
        # If in position — we already handled exits above; keep position
        return "NOOP", f"⏱️ Clearing protection ({minutes_to_clearing}min): hold position", metadata
    
    
        # ---------- 0.5) MINIMUM HOLD TIME (15 minutes) ----------
    # Блокируем закрытие позиции раньше 15 минут (кроме SL/TP/Emergency)
    MIN_HOLD_MINUTES = 15
    
    if lots != 0:  # Если есть позиция
        # Получаем время входа из SharedState
        entry_time = sharedstate.entry_time if sharedstate else None

        
        if entry_time is not None:
            hold_duration = (datetime.now() - datetime.fromtimestamp(entry_time)).total_seconds() / 60  # минуты
            
            if hold_duration < MIN_HOLD_MINUTES:
                # Проверяем, это риск-выход или обычное закрытие
                is_risk_exit = metadata.get("is_risk_exit", False)
                
                if not is_risk_exit:
                    # Блокируем обычное закрытие
                    print(f"⏳ MIN_HOLD: position held {hold_duration:.1f}m / {MIN_HOLD_MINUTES}m (blocking exit)")
                    metadata["min_hold_active"] = True
                    metadata["hold_duration"] = hold_duration
                    return "NOOP", f"MIN_HOLD: {hold_duration:.1f}m < {MIN_HOLD_MINUTES}m (keep position)", metadata
                else:
                    # Риск-выход разрешён (SL/TP/Emergency)
                    print(f"⚠️ MIN_HOLD bypassed: risk exit at {hold_duration:.1f}m")


    # ---------- 1) Target delta bookkeeping ----------
    delta = target_lots - lots
    metadata.update({"delta": delta, "target_lots": target_lots, "current_lots": lots})

    # ---------- 2) FORCE BUY from news_fire (only if flat; with SL cooldown guards) ----------
    trading_rules = parse_trading_rules_from_news()
    if trading_rules.get("force_buy") and lots == 0:
        cycles_since_sl = current_cycle - last_sl_exit_cycle if last_sl_exit_cycle >= 0 else 999

        if cycles_since_sl < 5:
            return "NOOP", f"🚫 FORCED BUY blocked: SL exit {cycles_since_sl} cycles ago", metadata

        if trend_u == "DOWN" and rsi < 50:
            return "NOOP", f"🚫 FORCED BUY blocked: trend DOWN + RSI {rsi:.1f} < 50", metadata

        metadata["forced_entry"] = True
        return "BUY2", "⚡ FORCED BUY from news_fire.txt", metadata

    # ---------- 3) Signal accumulation (for forced entry) ----------
    if ai_signal_u == "BUY":
        consecutive_buy_signals += 1
        buy_signals_history.append(ai_confidence)
        if len(buy_signals_history) > 5:
            buy_signals_history.pop(0)
    else:
        consecutive_buy_signals = 0
        buy_signals_history.clear()

    if ai_signal_u == "SELL":
        consecutive_sell_signals += 1
        sell_signals_history.append(ai_confidence)
        if len(sell_signals_history) > 5:
            sell_signals_history.pop(0)
    elif ai_signal_u != "BUY":
        consecutive_sell_signals = 0
        sell_signals_history.clear()

        # Adaptive threshold
    adaptive_threshold = 70 if atr > 0.15 else 60 if atr < 0.10 else 65

    
    # ---------- 3.5) FORCED ENTRY SAFETY CHECKS ----------
    # Higher confidence threshold for forced entry
    forced_entry_min_conf = 75  # Was 60-65 in adaptive_threshold
    
    # Block forced entry in extreme conditions
    forced_entry_blocked = False
    forced_entry_block_reason = ""
    
    if lots == 0:
        # Check 1: Extreme RSI zones
        if ai_signal_u == "BUY" and rsi <= 30:
            forced_entry_blocked = True
            forced_entry_block_reason = f"Forced BUY blocked: RSI {rsi:.1f} ≤ 30 (oversold)"
        elif ai_signal_u == "SELL" and rsi >= 70:
            forced_entry_blocked = True
            forced_entry_block_reason = f"Forced SELL blocked: RSI {rsi:.1f} ≥ 70 (overbought)"
        
        # Check 2: Bias conflict
        elif ai_signal_u == "BUY" and str(bias).upper() in ("SHORT_ONLY", "BEARISH"):
            forced_entry_blocked = True
            forced_entry_block_reason = f"Forced BUY blocked: conflicts with bias={bias}"
        elif ai_signal_u == "SELL" and str(bias).upper() in ("LONG_ONLY", "BULLISH"):
            forced_entry_blocked = True
            forced_entry_block_reason = f"Forced SELL blocked: conflicts with bias={bias}"
        
        # Check 3: Falling knife / rising wedge
        elif ai_signal_u == "BUY" and trend_u in ("DOWN", "IMPULSE_DOWN") and rsi <= 35:
            forced_entry_blocked = True
            forced_entry_block_reason = f"Forced BUY blocked: falling knife (trend={trend_u}, RSI {rsi:.1f})"
        elif ai_signal_u == "SELL" and trend_u in ("UP", "IMPULSE_UP") and rsi >= 65:
            forced_entry_blocked = True
            forced_entry_block_reason = f"Forced SELL blocked: rising wedge (trend={trend_u}, RSI {rsi:.1f})"




    # ---------- 4) Forced entry BUY / SELL (flat only) ----------
    if lots == 0:
                # Forced BUY after 3 consistent BUY signals
        if consecutive_buy_signals >= 3 and len(buy_signals_history) >= 3:
            avg_conf = sum(buy_signals_history[-3:]) / 3
            volume_confirmed = current_volume >= avg_volume * 1.2 if avg_volume > 0 else True
            
            # 🚨 PATCH: Block forced BUY in extreme RSI or downtrend
            # Requirement 1: Higher confidence threshold (75% instead of 60-65)
            if avg_conf < 75:
                pass  # Skip forced entry, continue to normal logic
            # Requirement 2: Block in extreme RSI zones
            elif rsi <= 30 or rsi >= 75:
                pass  # Skip forced entry (oversold/overbought)
            # Requirement 3: Block BUY when bias conflicts (SHORT_ONLY/BEARISH)
            elif str(bias).upper() in ("SHORT_ONLY", "BEARISH"):
                pass  # Skip forced BUY against bearish bias
            # Requirement 4: Block BUY in downtrend with low RSI (falling knife)
            elif trend_u in ("DOWN", "IMPULSE_DOWN") and rsi <= 35:
                pass  # Skip BUY during strong downtrend
            # All checks passed → allow forced entry
            elif bullish_prob > 0.55 and volume_confirmed:
                # ========== SLEEPING MARKET GATE (П.3) ==========
                # КРИТИЧНО: Блокировать forced entry в спящем рынке
                if metadata.get("sleeping_market", False):
                    # Рынок спит → отменяем forced entry
                    print(f"🚫 FORCED BUY blocked by SLEEPING_MARKET (signals={consecutive_buy_signals})")
                    pass  # Продолжаем к обычной логике (не делаем return)
                elif trend_u == "FLAT":
                    # Дополнительная защита: даже если не sleeping, в FLAT не форсируем
                    print(f"🚫 FORCED BUY blocked by FLAT trend (signals={consecutive_buy_signals})")
                    pass  # Продолжаем к обычной логике
                else:
                    # ОК, можно делать forced entry
                    metadata.update(
                        {"forced_entry": True, "consecutive_signals": consecutive_buy_signals, "avg_confidence": avg_conf}
                    )
                    return "BUY1", f"🚨 FORCED ENTRY: 3 BUY (thr 75%, avg {avg_conf:.1f}%) RSI {rsi:.1f}", metadata


        # Forced SELL after 3 consistent SELL signals
            
        if consecutive_sell_signals >= 3 and len(sell_signals_history) >= 3:
            avg_conf_sell = sum(sell_signals_history[-3:]) / 3
            volume_confirmed_sell = current_volume >= avg_volume * 1.2 if avg_volume > 0 else True

            if not forced_entry_blocked and avg_conf_sell >= forced_entry_min_conf and rsi > 25 and bearish_prob > 0.55 and volume_confirmed_sell:
                # ========== SLEEPING MARKET GATE (П.3) ==========
                if metadata.get("sleeping_market", False):
                    print(f"🚫 FORCED SELL blocked by SLEEPING_MARKET (signals={consecutive_sell_signals})")
                    pass  # Продолжаем к обычной логике
                elif trend_u == "FLAT":
                    print(f"🚫 FORCED SELL blocked by FLAT trend (signals={consecutive_sell_signals})")
                    pass  # Продолжаем к обычной логике
                else:
                    # ОК, можно делать forced entry
                    metadata.update(
                        {"forced_entry": True, "consecutive_signals": consecutive_sell_signals, "avg_confidence": avg_conf_sell}
                    )
                    return "SELL1", f"🚨 FORCED SHORT: 3 SELL (thr {adaptive_threshold}%, avg {avg_conf_sell:.1f}%) RSI {rsi:.1f}", metadata


    # ---------- 5) Entry logic (flat only) ----------
        # ---------- 5) Entry logic (flat only) ----------
    min_entry_conf = 40 if is_extreme else 70
    is_bullish_hedge = (str(bias).lower() == "bearish" and bullish_prob > 0.30)
    is_bearish_hedge = (str(bias).lower() == "bullish" and bearish_prob > 0.30)

    if lots == 0:
        # GWDD target из глобальной переменной
        target_lots = GWDD_TARGET_LOTS
        
        
        # ========== 🚨 FLAT TREND FILTER (НОВОЕ) ==========
        # Блокировать новые входы во FLAT тренде (кроме экстремальных условий)
        if trend_u == "FLAT":
            # Исключения: разрешаем вход только при экстремальных RSI или IMPULSE market_state
            allow_flat_entry = False
            
            # Исключение 1: Экстремальный RSI (mean reversion)
            if rsi < 20 or rsi > 80:
                allow_flat_entry = True
                print(f"⚡ FLAT override: extreme RSI {rsi:.1f}")
            
            # Исключение 2: Market state показывает IMPULSE (несмотря на FLAT trend)
            elif market_state_u in ("IMPULSE_UP", "IMPULSE_DOWN", "IMPULSEUP", "IMPULSEDOWN"):
                allow_flat_entry = True
                print(f"⚡ FLAT override: market_state={market_state_u}")
            
            # Исключение 3: Очень высокая уверенность AI (>85%) + сильная вероятность
            elif effective_confidence >= 85 and (bullish_prob >= 0.80 or bearish_prob >= 0.80):
                allow_flat_entry = True
                print(f"⚡ FLAT override: AI conf {effective_confidence}%, prob {max(bullish_prob, bearish_prob):.0%}")
            
            if not allow_flat_entry:
                return "NOOP", f"🚫 FLAT FILTER: no entry in FLAT trend (RSI={rsi:.1f}, conf={effective_confidence})", metadata
        # ========== КОНЕЦ FLAT FILTER ==========
        
        # ========== 🚨 BIAS FILTER (ДОБАВЛЕНО) ==========
        bias_u = str(bias).upper().strip()
        
        
        # SHORT_ONLY → блокировать BUY, но разрешить SHORT по технике
        if bias_u == "SHORT_ONLY" and ai_signal_u == "BUY":
            # ✅ При импульсе вниз разрешаем скальпинг шортом
            market_state_val = str(market_state).upper()
            if market_state_val in ("IMPULSE_DOWN", "IMPULSEDOWN") and rsi > 15:
                ai_signal_u = "SELL"
                metadata["bias_override"] = "SHORT_ONLY: BUY→SELL (IMPULSE_SCALP mode)"
                print(f"⚡ BIAS OVERRIDE: SHORT_ONLY + IMPULSE_DOWN → разрешён скальп-шорт (RSI={rsi:.1f})")
            else:
                return "NOOP", f"⛔ BIAS CONFLICT: SHORT_ONLY blocks BUY signal (conf={effective_confidence})", metadata

        
        
        # LONG_ONLY → блокировать SELL, но разрешить LONG по технике (даже при AI SELL)
        if bias_u == "LONG_ONLY" and ai_signal_u == "SELL":
            # ✅ При импульсе разрешаем скальпинг лонгом (игнорируем фундаментальный SELL)
            market_state_val = str(market_state).upper()
            if market_state_val in ("IMPULSE_UP", "IMPULSEUP") and rsi < 85:
                # Переключаем сигнал на BUY, но снижаем вес на 30%
                ai_signal_u = "BUY"
                metadata["bias_override"] = "LONG_ONLY: SELL→BUY (IMPULSE_SCALP mode)"
                print(f"⚡ BIAS OVERRIDE: LONG_ONLY + IMPULSE_UP → разрешён скальп-лонг (RSI={rsi:.1f})")
            else:
                return "NOOP", f"⛔ BIAS CONFLICT: LONG_ONLY blocks SELL signal (conf={effective_confidence})", metadata

        
        # BEARISH → блокировать агрессивный BUY (кроме hedge)
        if bias_u == "BEARISH" and ai_signal_u == "BUY" and not is_bullish_hedge:
            return "NOOP", f"⛔ BIAS CONFLICT: BEARISH blocks aggressive BUY (conf={effective_confidence}, no hedge)", metadata
        
        # BULLISH → блокировать агрессивный SELL (кроме hedge)
        if bias_u == "BULLISH" and ai_signal_u == "SELL" and not is_bearish_hedge:
            return "NOOP", f"⛔ BIAS CONFLICT: BULLISH blocks aggressive SELL (conf={effective_confidence}, no hedge)", metadata
        # ========== КОНЕЦ BIAS FILTER ==========
        
        
        # Small test-entry on HOLD only in impulse up (optional, kept from your version)
        if (
            ai_signal_u == "HOLD"
            and ai_confidence >= 65
            and bullish_prob >= 0.40
            and rsi <= 80
            and market_state_u in ("IMPULSE_UP", "UP", "IMPULSEUP")
        ):
            return "BUY1", f"TEST ENTRY HOLD+Conf{ai_confidence} Bull{bullish_prob:.2f} RSI{rsi:.1f}", metadata

        # === MAIN ENTRY: используем GWDD target_lots ===
        if ai_signal_u == "BUY" and effective_confidence >= min_entry_conf:
            action = f"BUY{target_lots}"
            return action, f"Entry Conf{effective_confidence} Bullish{bullish_prob:.2f} GWDD={target_lots}", metadata

        if is_bullish_hedge and rsi < 40:
            action = f"BUY{target_lots}"
            return action, f"Hedge Long Prob{bullish_prob:.2f} vs Bias{bias} GWDD={target_lots}", metadata

        if ai_signal_u == "SELL" and effective_confidence >= min_entry_conf:
            action = f"SELL{target_lots}"
            return action, f"Entry Conf{effective_confidence} Bearish{bearish_prob:.2f} GWDD={target_lots}", metadata

        if is_bearish_hedge and rsi > 60:
            action = f"SELL{target_lots}"
            return action, f"Hedge Short Prob{bearish_prob:.2f} vs Bias{bias} GWDD={target_lots}", metadata

        # === MEAN REVERSION: оставляем 1 лот (экстренный вход) ===
        if (rsi < 20 and bullish_prob > 0.15 and ai_signal_u != "SELL") or (
            rsi < 20 and str(bias).lower() == "bearish" and bullish_prob > 0.25 and ai_signal_u != "SELL"
        ):
            return "BUY1", f"🚨 OVERSOLD RSI{rsi:.1f} Bull{bullish_prob:.2f}", metadata

        if rsi > 80 and bearish_prob > 0.15 and ai_signal_u != "BUY":
            return "SELL1", f"🚨 OVERBOUGHT RSI{rsi:.1f} Bear{bearish_prob:.2f}", metadata

        # === GAP RULE: специальный случай — 2 лота ===
        if (
            trend_u == "IMPULSE_UP"
            and current_price
            and 5.28 <= current_price <= 5.36
            and 72 <= rsi <= 76
            and ai_signal_u == "HOLD"
        ):
            return "BUY2", "⚡ IMMEDIATE Gap consolidation BUY2", metadata


        
    
    # ---------- 6) HOLD enforcement LAST (so it never blocks SL/TP) ----------
    # Секция 0 УЖЕ обработала все risk-exits (TP/SL/Emergency/Clearing)
    # Если код дошёл сюда — это НЕ risk-exit

    if ai_signal_u == "HOLD":
        # Если в позиции — держим (delta уже = 0 из секции 7)
        if lots != 0:
            return "NOOP", f"HOLD keep position (lots={lots})", metadata
        # Если флэт — блокируем вход
        else:
            return "NOOP", "HOLD wait conviction", metadata

    # ---------- 7) Final delta-driven rebalance ----------
    # Recompute delta (in case something changed upstream)
    delta = target_lots - lots
    metadata.update({"delta": delta, "target_lots": target_lots, "current_lots": lots})

    # ========== GWDD DELTA REBALANCE (ПАТЧ: отключаем закрытие в RANGE) ==========
    if delta > 0:
        return f"BUY{abs(delta)}", f"GWDD Target{target_lots} cur{lots} BUY{abs(delta)}", metadata

    if delta < 0:
        # ✅ НОВОЕ: В RANGE не закрываем позицию по GWDD target_lots=0
        if market_state_u == "RANGE" and lots != 0 and target_lots == 0:
            print(f"⚠️ GWDD_BLOCK disabled in RANGE: keep position {lots} lots (target was 0)")
            return "NOOP", f"GWDD_BLOCK disabled in RANGE (keep {lots} lots)", metadata
        
        # В остальных случаях — разрешаем закрытие
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
    COOLDOWNDURATION: float = 60.0  # Per-signal
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


# ========== ACONTEXT HELPER FUNCTIONS ==========
def init_acontext_client():
    """Инициализация Acontext client с fallback."""
    acontext_enabled = os.getenv("ACONTEXT_ENABLED", "false").lower() == "true"
    
    if not acontext_enabled:
        return None, None, False
    
    try:
        client = AcontextClient(api_key=os.getenv("ACONTEXT_API_KEY"))
        session = client.sessions.create()
        print(f"✅ Acontext session created: {session.id}")
        return client, session.id, True
    except Exception as e:
        print(f"⚠️ Acontext initialization failed: {e}")
        return None, None, False


def save_decision_to_acontext(
    client,
    session_id: str,
    cycle: int,
    action: str,
    reason: str,
    current_price: float,
    rsi_val: float,
    current_lots: int,
    pnl_pct: float,
    news_signal: str,
    news_confidence: float,
    planner_bias: str,
    gwdd_weight: Optional[float],
    metadata: dict
):
    """Сохранение решения цикла в Acontext."""
    try:
        decision_content = {
            "cycle": cycle,
            "timestamp": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
            "action": action,
            "reason": reason,
            "price": round(current_price, 3),
            "rsi": round(rsi_val, 1),
            "lots": current_lots,
            "pnl_pct": round(pnl_pct, 2),
            "ai_signal": news_signal,
            "ai_confidence": news_confidence,
            "planner_bias": planner_bias,
            "gwdd_weight": round(gwdd_weight, 3) if gwdd_weight else None,
            "metadata": metadata
        }
        
        client.sessions.store_message(
            session_id=session_id,
            blob={
                "role": "assistant",
                "content": json.dumps(decision_content, ensure_ascii=False)
            },
            format="openai"
        )
    except Exception as e:
        print(f"⚠️ Acontext save failed (cycle {cycle}): {e}")


def get_acontext_summary(client, session_id: str, max_recent: int = 5) -> Optional[str]:
    """Получить краткую историю последних циклов для контекста."""
    try:
        prev_messages = client.sessions.get_messages(
            session_id=session_id,
            format="openai",
            edit_strategies=[
                {
                    "type": "token_limit",
                    "params": {"limit_tokens": 2000}
                }
            ]
        )
        
        if prev_messages.items:  # type: ignore
            recent = prev_messages.items[-max_recent:]  # type: ignore
            summaries = []
            for msg in recent:
                try:
                    data = json.loads(msg['content'])  # type: ignore
                    summaries.append(
                        f"C{data['cycle']}: {data['action']} @ {data['price']} "
                        f"(RSI={data['rsi']}, PnL={data['pnl_pct']}%)"
                    )
                except:
                    summaries.append(msg['content'][:80])  # type: ignore
            return "\n".join(summaries)
        return None
    except Exception as e:
        print(f"⚠️ Acontext summary failed: {e}")
        return None
# ===============================================

class TradingExecutor:
    """Заглушка для TradingExecutor"""
    def __init__(self, *args, **kwargs):
        pass
    
    async def execute_trade(self, *args, **kwargs):
        print("⚠️ TradingExecutor: заглушка, реальное исполнение не реализовано")
        return None

async def main_loop():
    global ACTIVE_NG_CONTRACT, ACTIVE_FIGI, ACTIVE_TICKER, ACTIVE_UID
    from src.services.atr_stop import ATRStopEngine
    
    
    sharedstate = SharedTradingState()
    executor = TradingExecutor(...)
    daily_limits = DailyLimitsManager(...)
    
    acontext_client, trading_session_id, acontext_enabled = init_acontext_client()
    
    atr_stop = ATRStopEngine(ksl_uptrend=2.0, ksl_other=1.5, ksl_flat=3.5, m_be=1.0)   # Использует defaults: k_sl_uptrend=2.0, k_sl_other=1.5, m_be=1.0

        # ========== DAILY LIMITS MANAGER ==========
    daily_limits_config = DailyLimitsConfig(
        ENABLED=True,
        MAX_TRADES_PER_DAY=15,
        MAX_DAILY_DRAWDOWN_RUB=100.0
    )
    daily_limits = DailyLimitsManager(daily_limits_config)
    print(f"✅ Daily Limits Manager initialized: max {daily_limits_config.MAX_TRADES_PER_DAY} trades/day, stop at -{daily_limits_config.MAX_DAILY_DRAWDOWN_RUB} RUB")
    # ==========================================


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
    sigma_rsi=20.0,  # ✅ Increased from 10.0
    sigma_prob=0.15,
    global_min_weight=0.40,  # ✅ Lowered from 0.50
    min_weight_conservative=0.60,  # ✅ Correct value
    min_weight_moderate=0.55,  # ✅ Raised from 0.50
    min_weight_aggressive=0.45,
    risk_mode_adjustments={
        "CONSERVATIVE": 0.5,
        "MODERATE": 1.0,
        "AGGRESSIVE": 1.2
    }
    )

    gwdd_engine = GWDDEngine(gwdd_config)
    risk_agent = RiskAgent()
    executor = MainOrderExecutor(token)
    news_agent = UnifiedNewsAgent()
    
    atr_stop = ATRStopEngine()  # Использует defaults: k_sl_uptrend=2.0, k_sl_other=1.5, m_be=1.0

    print("👻 Hybrid Architecture v2.0: Режим активного мониторинга запущен.")
    await send_telegram("🚀 Predator v2.0: Bayesian Engine + Synoptic Monitor активны.")

    cycle = 0
    position_timer = PositionTimer()
    MAX_LOTS_ALLOWED = 10
    prev_lots = 0
    # === SL COOLDOWN TRACKING ===
    last_sl_exit_cycle = -999  # Цикл когда был последний выход по SL
    last_sl_exit_price = 0.0   # Цена выхода по SL
    SL_COOLDOWN_CYCLES = 3     # Минимум циклов до повторного входа после SL
    
    global last_planner_result, last_planner_time
    last_planner_result = None
    last_planner_time = 0.0
    
    
   
    # Инициализация entry_time при старте, если позиция открыта
    try:
        pos = await get_position_data_safe(executor, ACTIVE_FIGI, retries=3)
        start_lots = int(pos.get("lots", 0))
        if start_lots > 0 and sharedstate.entry_time is None:
            # Пытаемся восстановить из истории
            entry_dt = load_last_entry_time_from_history(ACTIVE_FIGI)
            
            if entry_dt:
                sharedstate.entry_time = entry_dt.timestamp()
                holding_hours = (time_module.time() - sharedstate.entry_time) / 3600
                print(f"⏰ Восстановлено из истории: позиция {start_lots} лот удерживается {holding_hours:.1f}ч")
            else:
                sharedstate.entry_time = time_module.time()
                print(f"⏰ История не найдена, entry_time установлен на текущее время")
    except Exception as e:
        print(f"⚠️ Ошибка init entry_time: {e}")


    try:
        from scripts.automation.ng_news_parser import main as update_news
        print("📰 Выполняется сбор свежих новостей (при запуске)...")
        await update_news()
    except Exception as e:
        print(f"⚠️ Ошибка сбора новостей при запуске: {e}")

    while True:
        try:
            # ========== AUTO-ROLLOVER CHECK ==========
            new_contract = select_active_ng_contract(NG_CONTRACTS)
            if new_contract and ACTIVE_FIGI and new_contract.figi != ACTIVE_FIGI:
                print(f"🔄 ROLLOVER: Переключение контракта с {ACTIVE_TICKER} на {new_contract.code}!")
                # TODO: Если есть открытая позиция по-старому контракту, надо закрыть!
                
            if new_contract:
                ACTIVE_NG_CONTRACT = new_contract
                ACTIVE_FIGI = new_contract.figi
                ACTIVE_TICKER = new_contract.code
                ACTIVE_UID = new_contract.uid
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

            # Блокируем торговлю до 10:00 МСК (аукцион открытия, гэпы)
            if now_msk.hour < 10:
                if cycle == 0 or cycle % 10 == 0:
                    print(f"⏸ PRE-MARKET WINDOW: {now_msk.strftime('%H:%M:%S')} < 10:00 MSK, waiting...")
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
            
            
            
            # (ATR stop blocks removed from here, properly handled after data fetch)
            
        
            # 2. Получение и анализ свечей
                    
            candles = await executor.get_candles_5m(ACTIVE_FIGI)
            if candles.empty:
                print("⚠️ Свечи не получены, ожидание 10с...")
                await asyncio.sleep(10)
                continue

            data = pipeline_analysis(candles, "NRF6")
            current_price = float(data["close"])
            atr_t = float(data.get("ATR", 0.015))
            current_volume = float(data.get("volume", 0.0))
            avg_volume_20 = float(data.get("avg_volume_20", 0.0))
            
            pnl_pct = 0.0
            if current_lots != 0 and avg_price > 0:
                pnl_pct = (current_price - avg_price) / avg_price * 100 * (1 if current_lots > 0 else -1)
            position_pnl_pct = pnl_pct
            
            sleeping_detector = SleepingMarketDetector()


            # === SLEEPING MARKET DETECTION ===
           

            # Подготовка данных для детектора
            trend_5m = str(data.get("trend_5m", data.get("trend", "FLAT"))).upper()
            p_high = float(data.get("day_high", candles["high"].max()))
            p_low = float(data.get("day_low", candles["low"].min()))
            
            rsi_val = float(data.get("rsi", 50.0))  # Дефолт 50.0 если нет RSI

            sleeping_input = SleepingMarketInput(
                atr=atr_t,
                atr_threshold=settings.SLEEPING_ATR_THRESHOLD,
                trend_ltf=trend_5m,
                trend_htf="NEUTRAL",  # Если есть trend_htf в data, используй его
                price_high=p_high,
                price_low=p_low,
                price_current=current_price,
                rsi=rsi_val
            )

            # Проверка спящего рынка
            if settings.SLEEPING_MARKET_ENABLED:
                sleeping_result = sleeping_detector.detect(sleeping_input)
                sharedstate.sleeping_market = sleeping_result.is_sleeping
                
                if sleeping_result.is_sleeping:
                    print(f"💤 {sleeping_result.reason}")
                else:
                    # Выводим только если меняется статус или раз в 10 циклов
                    if cycle == 1 or cycle % 10 == 0:
                        print(f"✅ Market active: {sleeping_result.reason}")
            else:
                sharedstate.sleeping_market = False



            # === ATR STOP: OPEN/CLOSE & UPDATE ===
            if prev_lots == 0 and current_lots != 0:
                direction = "LONG" if current_lots > 0 else "SHORT"
                atr_stop.on_open(direction=direction, entry_price=avg_price, atr_0=atr_t, trend=trend_5m)
                st = atr_stop.get_state()
                if st is not None:
                    sharedstate.atr_at_entry = st.atr_at_entry
                    sharedstate.position_direction = st.direction
                    sharedstate.sl_level = st.sl_level
                    sharedstate.p_high_since_entry = st.p_high_since_entry
                    sharedstate.p_low_since_entry = st.p_low_since_entry
                    sharedstate.entry_price = avg_price
                    print(f"✅ Position opened: {current_lots} lots @ {avg_price:.3f}")

            elif prev_lots != 0 and current_lots == 0:
                atr_stop.on_close()
                sharedstate.close_position()
                sharedstate.atr_at_entry = 0.0
                sharedstate.position_direction = ""
                sharedstate.p_high_since_entry = 0.0
                sharedstate.p_low_since_entry = 0.0
                print(f"✅ Position closed, entry_time reset")
                daily_limits.register_trade(pnl=0.0)

            elif prev_lots != 0 and current_lots != 0 and sharedstate.sl_level == 0.0:
                direction = "LONG" if current_lots > 0 else "SHORT"
                atr_stop.on_open(direction=direction, entry_price=avg_price, atr_0=atr_t, trend=trend_5m)
                sharedstate.sl_level = atr_stop.get_sl() or 0.0
                print(f"🔄 ATR Stop восстановлен: SL={sharedstate.sl_level:.3f}")

            prev_lots = current_lots

            # Обновление стопа, если позиция открыта
            if current_lots != 0:
                atr_stop.on_update(price_t=current_price, atr_t=atr_t, trend=trend_5m)
                st = atr_stop.get_state()
                if st is not None:
                    sharedstate.sl_level = st.sl_level
                    sharedstate.p_high_since_entry = st.p_high_since_entry
                    sharedstate.p_low_since_entry = st.p_low_since_entry
                    
            print(
                f"💰 Price: {current_price:.3f} | RSI: {rsi_val:.1f} | "
                f"Lots: {current_lots} | PnL: {pnl_pct:.2f}% | SL: {sharedstate.sl_level:.3f}"
            )



            # Weather module removed - using external news only
            weather_data = {"demand_impact_pct": 0, "arctic_blast_score": 0.0, "is_extreme": False}
            weather_str = ""

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
            
            # 5. NEWS_AGENT анализирует новости + техничку
            global last_news_result, news_cache_cycle, last_news_mtime
            
            # Чтение времени изменения файла news_fire.txt
            news_file_path = "news_fire.txt"
            current_news_mtime = 0.0
            if os.path.exists(news_file_path):
                current_news_mtime = os.path.getmtime(news_file_path)
            
            # Точные часы запуска (10:00 или 18:00 МСК)
            is_scheduled_time = now_msk.hour in (10, 18) and now_msk.minute == 0
            
            full_context = f"НОВОСТИ:\n{manual_news}"
            if current_news_mtime != last_news_mtime or is_scheduled_time or last_news_result is None:
                reason = "full refresh"
                if current_news_mtime != last_news_mtime and last_news_mtime != 0.0:
                    reason = "news file updated"
                elif is_scheduled_time:
                    reason = f"scheduled time {now_msk.strftime('%H:%M')}"
                
                print(f"📰 NEWS_AGENT: Анализ новостей и фундамента ({reason})...")
                news_result = await analyst.analyze(
                    marketdata=data,
                    newscontext=full_context,
                    bias=current_bias,
                )
                last_news_result = news_result
                last_news_mtime = current_news_mtime
                news_cache_cycle = cycle
            else:
                print("📰 NEWS_AGENT: Используется кэш (ожидание изменения файла новостей или расписания 10:00/18:00)...")
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
            
            # --- PLANNER GATING ---
            run_planner = False
            current_timestamp = now_msk.timestamp()
            
            if last_planner_result is None:
                run_planner = True
            elif news_cache_cycle == cycle:  # Если Analyst только что отработал (сменились новости или 10:00/18:00)
                run_planner = True
            elif (current_timestamp - last_planner_time) >= 3600: # Прошел 1 час
                run_planner = True

            if run_planner:
                print("🧠 PLANNER: Синтез торговой стратегии (LLM Запрос)...")
                try:
                    plan_result = await asyncio.wait_for(
                        asyncio.to_thread(planner.create_plan, market_context),
                        timeout=30,
                    )
                    last_planner_result = plan_result
                    last_planner_time = current_timestamp
                except asyncio.TimeoutError:
                    print("⚠️ PLANNER TIMEOUT (30s) -> fallback CONSERVATIVE")
                    plan_result = {
                        "bias": "NEUTRAL",
                        "risk_mode": "CONSERVATIVE",
                        "mode": "CONSERVATIVE",
                        "strategy": "NEUTRAL",
                        "reason": "planner timeout",
                        "force_weight": 0.55,
                    }
                    last_planner_result = plan_result
                except Exception as e:
                    print(f"⚠️ PLANNER ERROR -> fallback CONSERVATIVE: {e}")
                    plan_result = {
                        "bias": "NEUTRAL",
                        "risk_mode": "CONSERVATIVE",
                        "mode": "CONSERVATIVE",
                        "strategy": "NEUTRAL",
                        "reason": f"planner error: {e}",
                        "force_weight": 0.55,
                    }
                    last_planner_result = plan_result
            else:
                mins_ago = (current_timestamp - last_planner_time) / 60
                print(f"🧠 PLANNER: Используется стратегия из кэша ({mins_ago:.1f} мин. назад)...")
                plan_result = last_planner_result

            print(f"🧠 PLANNER RESULT: mode={plan_result.get('mode')} bias={plan_result.get('bias')} reason={plan_result.get('reason','')}")

            final_bias = plan_result.get("bias", "NEUTRAL")  # ← Fallback на NEUTRAL вместо current_bias


            # 8. Погода как фильтр
            trade_allowed = risk_allowed
            
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
                #planner_force_weight = plan_result.get("force_weight")
                #if planner_force_weight is not None:
                #    entry_weight = planner_force_weight
                #    print(f"⚡ FORCE_WEIGHT override (Planner): {entry_weight:.3f}")

                should_enter, weight_final, gwdd_reason = gwdd_engine.decide_entry(
                    entry_weight=entry_weight,
                    risk_mode=gwdd_mode,
                    ai_signal=news_result.signal,
                    rsi=rsi_val,
                    market_state=data.get("market_state", "RANGE")
                )

                position_size = gwdd_engine.get_position_sizing(
                entry_weight=entry_weight,
                max_lots=MAX_LOTS_ALLOWED,
                risk_mode=gwdd_mode,
                rsi=rsi_val,
                current_lots=current_lots,  # ✅ Передаём текущую позицию
                )

                # Сохраняем GWDD для decide_action
                global GWDD_TARGET_LOTS, GWDD_WEIGHT
                GWDD_TARGET_LOTS = position_size
                GWDD_WEIGHT = entry_weight
                print(f"🔧 GWDD → decide_action: target_lots={GWDD_TARGET_LOTS}, weight={GWDD_WEIGHT:.3f}")
                
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

            # --- AI-OVERRIDE LAYER: даём ИИ право на малый вход, даже если GWDD блокирует ---
            ai_hard_buy = (
                news_result.signal == "BUY"
                and news_result.confidence >= 85
                and news_result.bullish_prob >= 75
                and rsi_val >= 70
                and data.get("market_state", "RANGE") in ("IMPULSE_UP", "RANGE")
            )

            ai_hard_sell = (
                news_result.signal == "SELL"
                and news_result.confidence >= 85
                and news_result.bearish_prob >= 75
                and rsi_val <= 30
                and data.get("market_state", "RANGE") in ("IMPULSE_DOWN", "RANGE")
            )

            ai_override = False
            ai_override_direction = None
            ai_override_lots = 0

            if current_lots == 0 and not should_enter:
                if ai_hard_buy and final_bias in ("NEUTRAL", "LONG_ONLY", "BULLISH"):
                    ai_override = True
                    ai_override_direction = "BUY"
                    ai_override_lots = 1
                elif ai_hard_sell and final_bias in ("NEUTRAL", "SHORT_ONLY", "BEARISH"):
                    ai_override = True
                    ai_override_direction = "SELL"
                    ai_override_lots = 1

            if ai_override:
                print(
                    f"🤖 AI-OVERRIDE: {ai_override_direction} {ai_override_lots} lot "
                    f"(Conf={news_result.confidence}%, "
                    f"Bull={news_result.bullish_prob:.0f}% / Bear={news_result.bearish_prob:.0f}%, "
                    f"RSI={rsi_val:.1f})"
                )
                entry_weight = max(entry_weight, 0.50)
                weight_final = entry_weight
                should_enter = True
                position_size = ai_override_lots
                gwdd_reason = gwdd_reason + " | AI-OVERRIDE: test 1 lot allowed on strong signal"

            # --- Блокировка торговли (только если НЕ override) ---
            if not should_enter and news_result.signal in ["BUY", "SELL"] and not ai_override:
                trade_allowed = False
                block_reason = f"GWDD_BLOCK: {gwdd_reason}"

            # --- Если уже в позиции, GWDD Lots=0 (SKIP) не должен обнулять target ---
            if current_lots != 0 and position_size == 0:
                position_size = abs(current_lots)

            # --- Вычисляем target_lots в зависимости от сигнала ---
            if news_result.signal == "SELL":
                target_lots_signed = -position_size  # SHORT: отрицательные лоты
            else:
                target_lots_signed = position_size   # LONG: положительные лоты


            # ========== DAILY LIMITS CHECK (П.5) ==========
            # Проверяем ТОЛЬКО перед новыми входами (current_lots == 0)
            action = None
            if current_lots == 0:
                can_trade, limit_reason = daily_limits.can_trade()
                
                if not can_trade:
                    print(f"🚫 {limit_reason}")
                    action = "NOOP"
                    action_reason = limit_reason
                    decision_metadata = {
                        "daily_limit_blocked": True,
                        "daily_trades": daily_limits.trades_today,
                        "daily_pnl": daily_limits.realized_pnl_today
                    }


            # 9. DECISION_BLOCK
        
            if action != "NOOP":
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
                    current_cycle=cycle,
                    sharedstate=sharedstate,
                )
            
            # --- EIA Thursday Blackout ---
            now_et = dt.datetime.now(ZoneInfo("US/Eastern"))
            if now_et.weekday() == 3 and dt.time(10, 0) <= now_et.time() <= dt.time(10, 45):
                if current_lots != 0 and pnl_pct > 0:
                    action = "SELLALL" if current_lots > 0 else "BUYALL"
                    action_reason = "Emergency: EIA Report Liquidation"
                    print(f"⚠️ EIA Report Liquidation: Закрываю позицию {current_lots} (PnL: {pnl_pct:.2f}%)")
                    trade_allowed = True
                else:
                    is_reduction = False
                    if current_lots > 0 and (action == "SELLALL" or action.startswith("SELL")):
                        is_reduction = True
                    elif current_lots < 0 and (action == "BUYALL" or action.startswith("BUY")):
                        is_reduction = True
                        
                    if not is_reduction:
                        action = "NOOP"
                        action_reason = "EIA Thursday Blackout window (10:00 - 10:45 ET)"
                        trade_allowed = False
            
            # Сохранить решение в Acontext
            if acontext_enabled and acontext_client and trading_session_id:
                save_decision_to_acontext(
                    client=acontext_client,
                    session_id=trading_session_id,
                    cycle=cycle,
                    action=action,
                    reason=action_reason,
                    current_price=current_price,
                    rsi_val=rsi_val,
                    current_lots=current_lots,
                    pnl_pct=pnl_pct,
                    news_signal=news_result.signal,
                    news_confidence=news_result.confidence,
                    planner_bias=final_bias,
                    gwdd_weight=weight_final if 'weight_final' in locals() else None,
                    metadata=decision_metadata
                )
            # ============================================

            # === SLEEPING MARKET FILTER ===
            # Блокируем новые активные входы, если рынок "спит"
            if sharedstate.sleeping_market and action not in ("NOOP", "BUYALL", "SELLALL"):
                # Проверяем, является ли действие открытием новой позиции
                is_new_entry = (current_lots == 0 and action.startswith(("BUY", "SELL")))
                
                if is_new_entry:
                    # Разрешить только аварийные входы при экстремальном RSI
                    if rsi_val < 25:
                        print("⚡ SLEEPING MARKET OVERRIDE: RSI < 25 (OVERSOLD) → разрешён вход")
                    elif rsi_val > 75:
                        print("⚡ SLEEPING MARKET OVERRIDE: RSI > 75 (OVERBOUGHT) → разрешён вход")
                    else:
                        print("❌ SLEEPING MARKET: новый активный вход заблокирован, удерживаем текущую позицию")
                        action = "NOOP"
                        action_reason = (action_reason + " | SLEEPING MARKET: block new entries").strip(" |")


           

            
            # Разделяем ИСПОЛНЕНИЕ на управление позицией и новые входы
            if action != "NOOP":
                direction = "BUY" if action.startswith("BUY") else "SELL"

                # 1) Определяем, уменьшает ли действие текущую позицию (risk-reduction)
                #    LONG: SELL уменьшает |lots|, SHORT: BUY уменьшает |lots|
                is_position_reduction = (
                    (direction == "SELL" and current_lots > 0) or
                    (direction == "BUY" and current_lots < 0)
                )
                # Explicitly mark SELLALL/BUYALL as reduction when position exists
                if action in ("BUYALL", "SELLALL") and current_lots != 0:
                    is_position_reduction = True

  
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
                if minutes_to_clearing <= 3 and not is_risk_exit:
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

                # ========== EXTRACT is_risk_exit FROM METADATA (BEFORE execution block) ==========
                is_risk_exit = decision_metadata.get("is_risk_exit", False)

                
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
#                     f"DEBUG_EXEC cycle={cycle} action={action} dir={direction} qty={qty} "
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
                        pass

                    
                                
                # === SL COOLDOWN CHECK ===
                if last_sl_exit_cycle >= 0 and not is_position_reduction:
                    cycles_since_sl = cycle - last_sl_exit_cycle
                    price_diff_pct = abs(current_price - last_sl_exit_price) / last_sl_exit_price * 100 if last_sl_exit_price > 0 else 999

                    # Bypass cooldown only for position reduction (risk exits)
                    if current_lots != 0 and is_position_reduction and is_risk_exit:
                        print("🔄 SL_COOLDOWN BYPASS: risk exit for existing position")
                    # Main cooldown check: block new entries for SL_COOLDOWN_CYCLES
                    elif cycles_since_sl < SL_COOLDOWN_CYCLES and current_lots == 0:
                        trade_allowed = False
                        block_reason = f"SL_COOLDOWN: {cycles_since_sl}/{SL_COOLDOWN_CYCLES} cycles since SL exit @ {last_sl_exit_price:.3f}"
                        print(block_reason)

    

               

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
                            # === LOG TRADE TO HISTORY ===
                            await log_trade(
                                action="CLOSE_LONG" if prev_lots > 0 else "CLOSE_SHORT",
                                figi=ACTIVE_FIGI,
                                lots_before=prev_lots,
                                lots_after=current_lots,
                                price=current_price,
                                signal=news_result.signal,
                                confidence=news_result.confidence,
                                reason=action_reason
                            )    

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
                position_pnl_pct=position_pnl_pct,  # ✅ НОВЫЙ параметр
                forced_entry=decision_metadata.get("forced_entry", False),
                consecutive_signals=decision_metadata.get("consecutive_signals", 0),
                avg_confidence=decision_metadata.get("avg_confidence", 0.0),
                sleeping_market=decision_metadata.get("sleeping_market", False),
                sleeping_reason=decision_metadata.get("sleeping_reason", "N/A"),
                adaptive_sl_multiplier=(
                    5.0 if trend_5m == "FLAT" else
                    3.0 if trend_5m in ("IMPULSE_UP", "IMPULSE_DOWN") else
                    3.5
                ),
                sl_level=sharedstate.sl_level,
                cooldown_active=decision_metadata.get("cooldown_active", False),
                cooldown_remaining=decision_metadata.get("cooldown_remaining", 0),
                daily_trades_count=daily_limits.trades_today,
                daily_pnl_total=daily_limits.realized_pnl_today,
                daily_trades_remaining=daily_limits_config.MAX_TRADES_PER_DAY - daily_limits.trades_today,
                daily_limit_blocked=decision_metadata.get("daily_limit_blocked", False),
                news_context=full_context,
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
