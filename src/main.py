import asyncio
import pandas as pd
import numpy as np
import re
import json
import uuid
import aiohttp
from typing import Dict, Any, Literal, Optional
from datetime import datetime, timedelta, timezone, time

from src.agents.analyst import MarketAnalyst
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config.settings import settings
from src.core.pipeline import pipeline_analysis
from t_tech.invest import AsyncClient, OrderDirection, OrderType, CandleInterval, Future
from pathlib import Path
import os
import time
from src.core.multi_agent_adapter import MultiAgentShadowAdapter
from src.agents.planner import PlannerAgent
from src.agents.risk_agent import RiskAgent
from src.tools.news_aggregator import UnifiedNewsAgent
from src.core.gwdd_engine import GWDDEngine, GWDDConfig
from src.shared_state import SharedTradingState
import datetime as dt
import pytz
from src.services.weather_monitor import SynopticMonitor




def get_market_status():
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = dt.datetime.now(moscow_tz)
    current_time = now.time()
    weekday = now.weekday() # 0=Mon, 6=Sun

    # 1. Воскресенье - выходной
    if weekday == 6:
        return False, "ВОСКРЕСЕНЬЕ: ВЫХОДНОЙ"

    # 2. Рабочее окно Пн-Сб: 08:50 - 23:50
    start_trade = dt.time(8, 50)
    end_trade = dt.time(23, 50)
    if not (start_trade <= current_time <= end_trade):
        return False, f"ВНЕ РАБОЧЕГО ВРЕМЕНИ (Рынок откроется в 08:50)"

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
FIGI_NRF6 = "FUTNGM012600"
NEWS_FILE = "news.txt"

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
        atr_multiplier_uptrend: float = 1.2,
        atr_multiplier_other: float = 0.8,
    ):
        self.entry_price = entry_price
        self.atr = atr
        self.trend = trend
        self.max_price = entry_price

        # Вычисляем offset СНАЧАЛА
        if trend == "UPTREND":
            self.offset = atr * atr_multiplier_uptrend
        else:
            self.offset = atr * atr_multiplier_other

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
    def __init__(self, entryprice: float, atr: float, trend: str = "FLAT",
                 atrmultiplierdowntrend: float = 1.2, atrmultiplierother: float = 0.8):
        self.entryprice = entryprice
        self.atr = atr
        self.trend = trend
        self.minprice = entryprice

        # Для шорта логично “шире” стоп при DOWNTREND (зеркально UPTREND у лонга)
        if trend == "DOWNTREND":
            self.offset = atr * atrmultiplierdowntrend
        else:
            self.offset = atr * atrmultiplierother

        self.trailingstop = entryprice + self.offset
        self.entrytime = dt.datetime.now(dt.timezone.utc)
        self.minholdminutes = 5
        print(f"Trailing Stop SHORT entry={entryprice:.4f}, offset={self.offset:.4f} trend={trend}")

    def update(self, currentprice: float, trend: str) -> bool:
        # обновление режима/offset (по аналогии с лонгом)
        if trend != self.trend:
            self.trend = trend
            if trend == "DOWNTREND":
                newoffset = self.atr * 1.2
            else:
                newoffset = self.atr * 0.8
            if newoffset > self.offset:
                self.offset = newoffset
                self.trailingstop = self.minprice + self.offset

        # если цена ушла ниже — для шорта это прибыль → двигаем minprice вниз
        if currentprice < self.minprice:
            self.minprice = currentprice
            newstop = self.minprice + self.offset
            if newstop < self.trailingstop:
                old = self.trailingstop
                self.trailingstop = newstop
                profitpct = (self.entryprice - self.minprice) / self.entryprice * 100
                print(f"Trailing Stop SHORT moved {old:.4f}->{self.trailingstop:.4f} min={self.minprice:.4f} profit={profitpct:.2f}%")

        profitpct_now = (self.entryprice - currentprice) / self.entryprice * 100
        if profitpct_now < 0.5:
            return False

        # стоп для шорта срабатывает если цена поднялась выше stop
        if currentprice >= self.trailingstop:
            profitfinal = (self.entryprice - currentprice) / self.entryprice * 100
            print(f"TRAILING STOP SHORT! price={currentprice:.4f} stop={self.trailingstop:.4f} profit={profitfinal:.2f}%")
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
        "SELL_HALF" или "SELL_ALL" или None
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
        return "SELL_HALF"
    else:
        print(
            f"⏰ КЛИРИНГ через {minutes_to_clearing} мин | PnL +{pnl_pct:.2f}% | "
            f"Тренд слабый/боковик → Фиксирую 100%"
        )
        return "SELL_ALL"


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


class OrderExecutor:
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
                        return {
                            "lots": int(p.quantity.units),
                            "average_price": float(avg),
                        }
                # Если в цикле не нашли позицию по figi, значит ее нет
                return {"lots": 0, "average_price": 0.0}
        except Exception as e:
            print(f"⚠️ ОШИБКА СЕТИ при получении позиции: {e}")
            return None  # Возвращаем None, чтобы главный цикл знал о сбое

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
                    candles.append(
                        {
                            "time": c.time,
                            "close": c.close.units + c.close.nano / 1e9,
                        }
                    )
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
    " - Игнорируй SELL, если RSI не >85.\n"
    " - АТАКУЙ (BUY): RSI <45 (откат=возможность).\n"
    " - STRONG BUY: RSI <35.\n"
    "---------------------------------------------------\n"
    "СЦЕНАРИЙ 2: ФУНДАМЕНТАЛ = BEARISH (Падение)\n"
    " - Игнорируй BUY, если RSI не <15.\n"
    " - АТАКУЙ (SELL): RSI >55 (отскок=шорт).\n"
    " - STRONG SELL: RSI >65.\n"
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
        if not os.path.exists(NEWS_FILE):
            return ""
        with open(NEWS_FILE, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
        if not content:
            return ""
        return content[:max_len]
    except Exception as e:
        print(f"⚠️ Ошибка чтения {NEWS_FILE}: {e}")
        return ""


def parse_trading_rules_from_news() -> dict:
    try:
        if not os.path.exists(NEWS_FILE):
            return {"bias": "neutral", "max_buy_price": None, "min_sell_price": None, "force_buy": False}
        with open(NEWS_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        
        rules = {
            "bias": "neutral", 
            "max_buy_price": None, 
            "min_sell_price": None,
            "force_buy": "FORCE_BUY: TRUE" in content.upper()  # ← НОВОЕ
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

        print(f"✅ Правила: BIAS={rules['bias'].upper()}, max_buy={rules['max_buy_price']}, min_sell={rules['min_sell_price']}")
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

    emoji_map = {"BUY": "🚀", "SELL_ALL": "💥", "SELL_HALF": "⚖️", "NOOP": "😴", "BUY1": "🎯", "SELL1": "🎯", "BUY_ALL": "💪", "BUY_HALF": "⚡"}
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

    # 3. Запись в файл shadow_agents_log.jsonl (БЕЗОПАСНАЯ)
    try:
        log_file = Path("shadow_agents_log.jsonl")
        
        # Гарантируем, что файл существует
        log_file.touch(exist_ok=True)
        
        # Записываем строку в конец файла
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            f.flush()  # Явное сброс буфера на диск
        
    except PermissionError as e:
        print(f"⚠️ WARN: No write permission to shadow_agents_log.jsonl: {e}")
    except OSError as e:
        print(f"⚠️ WARN: OS error writing log: {e}")
    except Exception as e:
        print(f"⚠️ WARN: Unexpected error in logging: {type(e).__name__}: {e}")




def decide_action(
    lots: int,
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
    atr: float = 0.1,
    avg_volume: int = 1
) -> tuple[str, str, dict]:  # ← ИЗМЕНЕНО: добавлен dict с метаданными
    """
    v2.0 Hybrid Architecture Decision Engine.
    Реализует Bayesian Scenario Engine и динамическую адаптивность.
    + First Entry Aggressive: форсированный вход после 3 BUY/SELL подряд (avg Conf ≥65%)
    + Обработка ошибок OpenRouter (фильтрация NEUTRAL с Conf=0)
    
    Returns: (action, reason, metadata) где metadata = {"forced_entry": bool, "consecutive_signals": int, "avg_confidence": float}
    """
    # Адаптивный порог confidence на основе ATR
    # Высокая волатильность (ATR > 0.15) → повышаем порог до 70%
    # Низкая волатильность (ATR < 0.10) → снижаем до 60%
    if atr > 0.15:
        adaptive_threshold = 70
    elif atr < 0.10:
        adaptive_threshold = 60
    else:
        adaptive_threshold = 65
    global consecutive_buy_signals, buy_signals_history, consecutive_sell_signals, sell_signals_history
    
    # Дефолтные метаданные
    metadata = {"forced_entry": False, "consecutive_signals": 0, "avg_confidence": 0.0}
        # ЗАЩИТА: блокировка FORCED ENTRY за 30 минут до клиринга
    clearing_block = minutes_to_clearing <= 30

    
    # 0. ОБРАБОТКА ОШИБОК OPENROUTER
    if ai_signal == "NEUTRAL" and ai_confidence == 0:
        print("⚠️ OpenRouter error detected (NEUTRAL/0%) - keeping previous state")
        return "NOOP", "OpenRouter lag detected - skipping cycle", metadata
    
    # 1. НАКОПЛЕНИЕ ИСТОРИИ BUY-СИГНАЛОВ
    if ai_signal == "BUY":
        consecutive_buy_signals += 1
        buy_signals_history.append(ai_confidence)
        if len(buy_signals_history) > 5:
            buy_signals_history.pop(0)
    else:
        consecutive_buy_signals = 0
        buy_signals_history.clear()
    
    # 2. FORCED ENTRY: 3 BUY подряд + avg Conf ≥ adaptive_threshold
    if clearing_block:
        return "NOOP", f"⏱️ Clearing protection ({minutes_to_clearing}min) - no forced entry", metadata

    
    if (consecutive_buy_signals >= 3 and 
        len(buy_signals_history) >= 3 and
        lots == 0):
        
        avg_conf = sum(buy_signals_history[-3:]) / 3
        
        volume_confirmed = (current_volume >= avg_volume * 1.2) if avg_volume > 0 else True
        
        if (avg_conf >= adaptive_threshold and
            rsi < 75 and
            bullish_prob > 0.55 and
            volume_confirmed):
            
            metadata = {"forced_entry": True, "consecutive_signals": consecutive_buy_signals, "avg_confidence": avg_conf}
            return "BUY1", f"🚨 FORCED ENTRY: 3 consecutive BUY (threshold {adaptive_threshold}%, avg Conf {avg_conf:.1f}%) | RSI {rsi:.1f}", metadata
            
    # 2b. НАКОПЛЕНИЕ ИСТОРИИ SELL-СИГНАЛОВ
    if ai_signal == "SELL":
        consecutive_sell_signals += 1
        sell_signals_history.append(ai_confidence)
        if len(sell_signals_history) > 5:
            sell_signals_history.pop(0)
    elif ai_signal != "BUY":  # Сброс только если не BUY (чтобы не сбрасывать при переключении BUY->SELL)
        consecutive_sell_signals = 0
        sell_signals_history.clear()

    # 2c. FORCED ENTRY: 3 SELL подряд + avg Conf ≥65%
    if (consecutive_sell_signals >= 3 and
        len(sell_signals_history) >= 3 and
        lots == 0):

        avg_conf_sell = sum(sell_signals_history[-3:]) / 3

        volume_confirmed_sell = (current_volume >= avg_volume * 1.2) if avg_volume > 0 else True
        
        if (avg_conf_sell >= adaptive_threshold and
            rsi > 25 and
            bearish_prob > 0.55 and
            volume_confirmed_sell):

            metadata = {"forced_entry": True, "consecutive_signals": consecutive_sell_signals, "avg_confidence": avg_conf_sell}
            return "SELL1", f"🚨 FORCED ENTRY SHORT: 3 consecutive SELL (threshold {adaptive_threshold}%, avg Conf {avg_conf_sell:.1f}%) | RSI {rsi:.1f}", metadata

    
    # 3. Dynamic Confidence Correction
    is_extreme = any(word in str(rules).lower() for word in ["vortex", "extreme", "arctic", "noaa"])
    effective_confidence = ai_confidence + (20 if is_extreme else 0)
    min_entry_conf = 40 if is_extreme else 70

    # 4. Bayesian Hedge
    is_bullish_hedge_ready = (bias == "bearish" and bullish_prob > 0.30)
    is_bearish_hedge_ready = (bias == "bullish" and bearish_prob > 0.30)

    # 5. ЛОГИКА ВХОДА
    if lots == 0:
        market_state_u = str(market_state or "RANGE").upper().strip()
        if (ai_signal == "HOLD" and ai_confidence >= 65 and bullish_prob >= 0.40 and
            rsi <= 80 and market_state_u in ("IMPULSE_UP", "UP")):
            return "BUY1", f"TEST ENTRY: HOLD+Conf {ai_confidence}% + Bull {bullish_prob:.2f} + RSI {rsi:.1f}", metadata
        
        if ai_signal == "BUY":
            if effective_confidence >= min_entry_conf:
                return "BUY1", f"Entry: Conf {effective_confidence}% | Bullish {bullish_prob:.2f}", metadata
            if is_bullish_hedge_ready and rsi < 40:
                return "BUY1", f"Hedge Long: Prob {bullish_prob:.2f} вопреки Bias {bias}", metadata

        if ai_signal == "SELL":
            if effective_confidence >= min_entry_conf:
                return "SELL1", f"Entry: Conf {effective_confidence}% | Bearish {bearish_prob:.2f}", metadata
            if is_bearish_hedge_ready and rsi > 60:
                return "SELL1", f"Hedge Short: Prob {bearish_prob:.2f} вопреки Bias {bias}", metadata

    # 6.x. EXTREME OVERSOLD: блокируем Emergency Rebalance SELL_ALL
    if rsi < 20 and lots > 0 and ai_signal == "SELL":
        return "NOOP", f"Block SELL_ALL: RSI {rsi:.1f} < 20 (Extreme oversold protection)", metadata
    else:
        if lots > 0:
            if bearish_prob > 0.65 or (ai_signal == "SELL" and effective_confidence > 80):
                return "SELL_ALL", f"Emergency Rebalance: Bearish prob {bearish_prob:.2f}", metadata
            if bullish_prob < 0.40 and rsi > 70:
                return "SELL_HALF", "Partial TP: Bullish momentum weakening", metadata

        if lots < 0:
            if bullish_prob > 0.65 or (ai_signal == "BUY" and effective_confidence > 80):
                return "BUY_ALL", f"Emergency Rebalance: Bullish prob {bullish_prob:.2f}", metadata
            if bearish_prob < 0.40 and rsi < 30:
                return "BUY_HALF", "Partial TP: Bearish momentum weakening", metadata

    # 7. Static Bias Override
    # 6.5. EXTREME OVERSOLD: Агрессивный вход при RSI < 20 (NEUTRAL bias)
    if rsi < 20 and lots == 0 and bullish_prob > 0.15 and ai_signal != "SELL":
        return "BUY1", f"🚨 EXTREME OVERSOLD: RSI {rsi:.1f} < 20 | Bull {bullish_prob:.2f} | Mean reversion", metadata

    # 6.6. EXTREME OVERBOUGHT: Агрессивный вход при RSI > 80 (NEUTRAL bias)
    if rsi > 80 and lots == 0 and bearish_prob > 0.15 and ai_signal != "BUY":
        return "SELL1", f"🚨 EXTREME OVERBOUGHT: RSI {rsi:.1f} > 80 | Bear {bearish_prob:.2f} | Mean reversion", metadata

    if rsi < 20 and bias == "bearish" and bullish_prob > 0.25 and ai_signal != "SELL":
        return "BUY1", "Extreme RSI mean reversion attempt (Hedge)", metadata

    return "NOOP", f"Waiting. B:{bullish_prob:.2f} S:{bearish_prob:.2f} RSI:{rsi:.1f}", metadata



def _env_bool(name: str, default: str = "false") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


async def post_order_guarded(
    executor: "OrderExecutor",
    figi: str,
    direction: Literal["BUY", "SELL"],
    quantity: int,
    *,
    why: str,
) -> bool:
    """
    Единая точка отправки ордеров с интеграцией Telegram уведомлений.
    """
    if quantity <= 0:
        msg = f"🛑 <b>Игнор ордера:</b> qty <= 0\nНаправление: {direction}\nПричина: {why}"
        print(f"🛑 post_order_guarded: quantity<=0 ignored | {direction} {quantity} {figi} | {why}")
        # Не шлем в ТГ каждый qty=0, чтобы не спамить, только в консоль
        return False


    try:
        # Пытаемся отправить реальный ордер
        res = await executor.post_order(figi, direction, quantity)
        if res:
            # Успех — уведомление будет отправлено из основного цикла (main_loop) 
            # для полноты контекста (цена, AI сигнал и т.д.)
            return True
        else:
            await send_telegram(f"⚠️ <b>ОТКАЗ БИРЖИ:</b> {direction} {quantity} шт.\nКонтекст: {why}")
            return False
            
    except Exception as e:
        # Любая техническая ошибка (сеть, API Тинькофф, таймаут)
        error_msg = f"❌ <b>ОШИБКА ИСПОЛНЕНИЯ:</b> {direction}\n<code>{str(e)[:200]}</code>\nКонтекст: {why}"
        print(error_msg)
        await send_telegram(error_msg)
        return False



async def main_loop():
    # Теперь это сработает для всех ключей
    token = settings.TINKOFF_TOKEN.get_secret_value()

    analyst = MarketAnalyst()
    planner = PlannerAgent()

    # === GWDD ENGINE INITIALIZATION ===
    gwdd_config = GWDDConfig(
        sigma_confidence=15.0,
        sigma_rsi=12.0,
        sigma_prob=0.20,
        global_min_weight=0.50,          # GWDD < 0.5 = почти всегда SKIP
        min_weight_conservative=0.65,    # CONSERVATIVE
        min_weight_moderate=0.55,        # MODERATE
        min_weight_aggressive=0.45,      # AGGRESSIVE
    )
    gwdd_engine = GWDDEngine(gwdd_config)
    risk_agent = RiskAgent()
    executor = OrderExecutor(token)
    news_agent = UnifiedNewsAgent()
    weather_monitor = SynopticMonitor()
    sharedstate = SharedTradingState()

    print("👻 Hybrid Architecture v2.0: Режим активного мониторинга запущен.")
    await send_telegram("🚀 Predator v2.0: Bayesian Engine + Synoptic Monitor активны.")

    cycle = 0
    position_timer = PositionTimer()
    MAX_LOTS_ALLOWED = 8

    while True:
        try:
            now_msk = dt.datetime.now(pytz.timezone("Europe/Moscow"))
            is_open, status_msg = get_market_status()

            if not is_open:
                await asyncio.sleep(60)
                continue

            cycle += 1
            print(f"\n⏳ --- CYCLE {cycle:06d} | {now_msk.strftime('%H:%M:%S')} ---")

            # 1. Получение данных о позиции
            pos = await get_position_data_safe(executor, FIGI_NRF6, retries=3)
            current_lots = int(pos["lots"])
            avg_price = float(pos["average_price"])

            # 2. Получение и анализ свечей
            candles = await executor.get_candles(FIGI_NRF6)
            if candles.empty:
                print("⚠️ Свечи не получены, ожидание 10с...")
                await asyncio.sleep(10)
                continue

            data = pipeline_analysis(candles, "NRF6")
            current_price = float(data["close"])

            # Расчет PnL
            if current_lots > 0 and avg_price > 0:
                pnl_pct = ((current_price - avg_price) / avg_price) * 100
            else:
                pnl_pct = 0.0
            rsi_val = float(data.get("RSI", 50.0))
            trend_5m = data.get("trend", "FLAT")
            current_volume = int(candles["volume"].iloc[-1]) if not candles.empty and "volume" in candles.columns else 0
            avg_volume_20 = int(candles["volume"].tail(20).mean()) if len(candles) >= 20 and "volume" in candles.columns else 1


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
                    "ATR": data.get("ATR", 0.1),
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

            # 7. PLANNER синтезирует стратегию
            print("🧠 PLANNER: Синтез торговой стратегии...")
            market_context = {
                "ticker": "NG",
                "trend_d1": trend_5m,
                "trend_h1": trend_5m,
                "news_summary": manual_news[:500],
            }
            plan_result = planner.create_plan(market_context)
            final_bias = plan_result.get("bias", current_bias)

            # 8. Погода как фильтр
            weather_allowed = True
            block_reason = ""
            # === SMART WEATHER OVERRIDE ===
            weather_impact = weather_data.get("demand_impact_pct", 0)
            arctic_score = weather_data.get("arctic_blast_score", 0.0)
            
            if weather_data.get("is_extreme"):
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

            # === GWDD INTEGRATION: Gaussian Weight Distribution Dynamics ===
            print("📊 GWDD: Расчет веса входа...")
            
            entry_weight, gwdd_breakdown = gwdd_engine.calculate_entry_weight(
                ai_signal=news_result.signal,
                confidence=news_result.confidence,
                bullish_prob=news_result.bullish_prob,
                bearish_prob=news_result.bearish_prob,
                rsi=rsi_val,
                market_state=data.get("marketstate", "RANGE"),
                risk_mode=sharedstate.risk_mode

            )
            
            should_enter, weight_final, gwdd_reason = gwdd_engine.decide_entry(
                entry_weight=entry_weight,
                risk_mode=sharedstate.risk_mode,
                ai_signal=news_result.signal
            )
            
            position_size = gwdd_engine.get_position_sizing(
                entry_weight=entry_weight,
                max_lots=MAX_LOTS_ALLOWED,
                risk_mode=sharedstate.risk_mode,
                rsi=rsi_val,
            )            
            print(f"⚖️ GWDD Weight: {entry_weight:.3f} | Lots: {position_size}")
            print(f"   {gwdd_reason}")
            
            # Обновляем shared_state
            sharedstate.gwdd_weight = entry_weight
            sharedstate.suggested_lots = position_size
            

            # GWDD может перекрыть решение risk_agent
            # НО: EXTREME OVERSOLD override имеет приоритет
            if not should_enter and news_result.signal in ["BUY", "SELL"]:
                # Проверяем EXTREME OVERSOLD исключение
                is_extreme_oversold = (rsi_val < 20 and news_result.bullish_prob > 0.20 and current_lots == 0)
                is_extreme_overbought = (rsi_val > 80 and news_result.bearish_prob > 0.20 and current_lots == 0)
                
                if not (is_extreme_oversold or is_extreme_overbought):
                    trade_allowed = False
                    block_reason = f"GWDD_BLOCK: {gwdd_reason}"
                else:
                    print(f"⚡ GWDD OVERRIDE: EXTREME condition (RSI {rsi_val:.1f}) bypasses GWDD block")

            # 9. DECISION_BLOCK
            action, action_reason, decision_metadata = decide_action(
                lots=current_lots,
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
                atr=data.get("ATR", 0.1)
            )














            if trade_allowed and action != "NOOP":
                direction = "BUY" if action.startswith("BUY") else "SELL"
                qty = 1
                print(
                    f"➡️ EXECUTION: {action} | "
                    f"Risk Score: {risk_verdict.get('risk_score', 0)}"
                )
                await post_order_guarded(
                    executor, FIGI_NRF6, direction, qty, why=action_reason
                )
            elif action != "NOOP":
                why_block = (
                    block_reason or risk_verdict.get("reason", "Blocked by filter")
                )
                print(f"🚫 BLOCKED: {why_block}")

            log_decision_block(
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

            await asyncio.sleep(60)

        except Exception as e:
            print(f"💥 Critical Error in main_loop: {e}")
            import traceback

            traceback.print_exc()
            await asyncio.sleep(10)




if __name__ == "__main__":
    asyncio.run(main_loop())
