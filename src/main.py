import asyncio
import pandas as pd
import numpy as np
import re
import json
import uuid
import aiohttp
from typing import Dict, Any, Literal, Optional
from datetime import datetime, timedelta, timezone, time
from src.agents.analyst import MarketAnalyst as AgentsMarketAnalyst
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.config.settings import settings
from src.core.pipeline import pipeline_analysis
from tinkoff.invest import AsyncClient, OrderDirection, OrderType, CandleInterval, Future
from pathlib import Path
import os
import time
from src.core.multi_agent_adapter import MultiAgentShadowAdapter 
from src.agents.planner import PlannerAgent
from src.tools.news_aggregator import UnifiedNewsAgent
import datetime as dt
import pytz


def get_market_status():
    moscow_tz = pytz.timezone("Europe/Moscow")
    now = dt.datetime.now(moscow_tz)
    weekday = now.weekday()  # 0=Пн ... 5=Сб ... 6=Вс
    current_time = now.time()

    # Торгуем по субботам, НЕ торгуем по воскресеньям
    if weekday == 6:
        return False, "🌙 Воскресенье (выходной)"

    # Клиринг (как вы задали сегодня)
    if dt.time(14, 0) <= current_time <= dt.time(14, 5):
        return False, "⏳ Дневной клиринг (14:00-14:05 МСК)"
    if dt.time(18, 45) <= current_time <= dt.time(19, 0):
        return False, "⏳ Вечерний клиринг (18:45-19:00 МСК)"

    # Торговые часы
    if dt.time(8, 50) <= current_time <= dt.time(23, 50):
        return True, "🚀 Торги активны"

    return False, f"🌙 Рынок закрыт ({current_time.strftime('%H:%M')} МСК)"




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
FIGI_NRZ5 = "FUTNGM122500"
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
            "SYSTEM: Ты — Агрессивный Трейдер фьючерсами (скальпер). Твоя цель — прибыль.\n\n"
            "ВХОДНЫЕ ДАННЫЕ:\n"
            "1. ФУНДАМЕНТАЛ (Bias): {bias}. (Bullish=Ищем покупки, Bearish=Ищем продажи).\n"
            "2. ТЕХНИКА: Цена={price}, RSI={rsi}, ATR={atr}, Тренд_5m={trend_5m} "
            "(UPTREND/DOWNTREND/FLAT).\n\n"
            "ТВОЯ ТОРГОВАЯ СТРАТЕГИЯ:\n"
            "---------------------------------------------------\n"
            "СЦЕНАРИЙ 1: ФУНДАМЕНТАЛ = BULLISH (Рост)\n"
            " - Игнорируй сигналы на продажу, если RSI не экстремален (>85).\n"
            " - АТАКУЙ (BUY): Если RSI < 45 (любой откат — это возможность).\n"
            " - STRONG BUY: Если RSI < 35.\n"
            "---------------------------------------------------\n"
            "СЦЕНАРИЙ 2: ФУНДАМЕНТАЛ = BEARISH (Падение)\n"
            " - Игнорируй сигналы на покупку, если RSI не на дне (<15).\n"
            " - АТАКУЙ (SELL): Если RSI > 55 (любой отскок — это возможность шорта).\n"
            " - STRONG SELL: Если RSI > 65.\n"
            "---------------------------------------------------\n"
            "СЦЕНАРИЙ 3: ФУНДАМЕНТАЛ = NEUTRAL (Боковик)\n"
            " - Торгуй от границ канала.\n"
            " - BUY: RSI < 30.\n"
            " - SELL: RSI > 70.\n"
            " - Иначе: HOLD.\n"
            "---------------------------------------------------\n"
            "ЗАДАЧА: Прими решение (BUY/SELL/HOLD). Если сомневаешься — HOLD.\n"
            "Если тренд UPTREND и RSI между 35 и 50 — склоняйся к BUY (откуп коррекции в тренде).\n"
            "ОТВЕТ (СТРОГО JSON):\n"
            '{{"signal": "BUY или SELL или HOLD", "confidence": "число от 0 до 100", "reason": "Твое короткое обоснование на русском"}}'
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


async def send_telegram(msg: str) -> None:
    """Отправляет уведомление в Telegram с защитой от ошибок."""
    try:
        token = settings.TELEGRAM_BOT_TOKEN.get_secret_value().strip()
        chat_id = settings.TELEGRAM_CHAT_ID
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id": chat_id,
                "text": msg,
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
    pnl_pct: float,
    holding_hours: float,
    ai_signal: str,
    ai_confidence: float,
    bias: str,
    minutes_to_clearing: int,
    rules: dict,
    action: str,
    reason: str = ""
):
    """Детальное логирование перед принятием решения."""
    print("\n" + "="*70)
    print(f"🔍 DECISION BLOCK | Цикл {cycle}")
    print(f"   📊 Цена: {price:.3f} | RSI: {rsi:.1f} | Тренд: {trend}")
    print(
        f"   💼 Позиция: {lots} лот | PnL: {pnl_pct:+.2f}% | Holding: {holding_hours:.1f}ч")
    print(
        f"   🤖 AI: {ai_signal} ({ai_confidence:.0f}%) | BIAS: {bias.upper()}")
    print(f"   ⏰ До клиринга: {minutes_to_clearing} мин")

    if rules.get("max_buy_price"):
        print(f"   📋 MAX_BUY: {rules['max_buy_price']:.3f}")
    if rules.get("min_sell_price"):
        print(f"   📋 MIN_SELL: {rules['min_sell_price']:.3f}")

    emoji_map = {
        "BUY_1": "🟢",
        "SELL_ALL": "🔴",
        "SELL_HALF": "🟠",
        "NOOP": "⚪"
    }
    emoji = emoji_map.get(action, "❓")

    print(f"   {emoji} РЕШЕНИЕ: {action}")
    if reason:
        print(f"   💬 Причина: {reason}")
    print("="*70 + "\n")


def decide_action(
    lots: int,
    max_lots_allowed: int,
    ai_signal: str,
    ai_confidence: float,
    trend_5m: str,
    rsi: float,
    bias: str,
    rules: dict = None
) -> Action:
    # 1. Сначала парсим базовые сигналы
    sig = (ai_signal or "HOLD").upper().strip()
    b = (bias or "neutral").lower().strip()

    # 2. ПРИНУДИТЕЛЬНЫЙ ВХОД (FORCE BUY) - ПЕРЕХВАТЫВАЕМ ТУТ
    if rules and rules.get("force_buy") is True:
        if lots == 0:
            print("🚨 FORCE_BUY ОБНАРУЖЕН! Игнорируем AI и входим.")
            return "OPEN_LONG"
        elif 0 < lots < max_lots_allowed:
            return "ADD_LONG"

    # 3. Если принудительного входа нет, работаем по обычной логике
    if sig == "HOLD":
        return "NOOP"

    # 3.1) Вход в LONG
    if lots >= 0 and sig == "BUY":
        # --- НОВАЯ ЛОГИКА RSI + TREND ---
        if rsi > 70:
            if trend_5m != "UPTREND":
                print(f"⚠️ RSI {rsi:.1f} перекупленность в боковике/падающем, вход LONG заблокирован")
                return "NOOP"
            else:
                print(f"🔥 RSI {rsi:.1f} высокий, но UPTREND активен. РАЗРЕШАЕМ импульсный вход.")
        
        # Фильтрация для медвежьего рынка
        if trend_5m == "DOWNTREND" and rsi > 45:
            return "NOOP"
        if b == "bearish" and rsi > 55:
            return "NOOP"


    # 3.2) Вход в SHORT
    if lots <= 0 and sig == "SELL":
        if rsi < 30:
            if trend_5m != "DOWNTREND":
                print(f"⚠️ RSI {rsi:.1f} перепроданность, вход SHORT заблокирован")
                return "NOOP"
            else:
                print(f"🔥 RSI {rsi:.1f} низкий, но DOWNTREND активен. РАЗРЕШАЕМ вход в шорт.")



def _env_bool(name: str, default: str = "false") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


TRADE_ENABLED = _env_bool("TRADE_ENABLED", "false")


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

    if not TRADE_ENABLED:
        msg = f"🧪 <b>DRY-RUN (Симуляция):</b>\nДействие: {direction} {quantity} лотов\nЦель: {figi}\nОбоснование: {why}"
        print(f"🧪 DRY-RUN: WOULD {direction} {quantity} {figi} | {why}")
        await send_telegram(msg) # Уведомляем, что бот БЫ сделал в реале
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
    token = settings.TINKOFF_TOKEN.get_secret_value()
    analyst = AgentsMarketAnalyst()
    planner = PlannerAgent()
    executor = OrderExecutor(token)
    # --- [ИЗМЕНЕНИЕ: Инициализация нового агрегатора новостей] ---
    news_agent = UnifiedNewsAgent() 
    shadowadapter = MultiAgentShadowAdapter()
      
    # --- ИНИЦИАЛИЗАЦИЯ ТЕНЕВОГО АДАПТЕРА ---
    shadow_adapter = MultiAgentShadowAdapter()  # <--- ДОБАВИТЬ ЭТУ СТРОКУ
    print("👻 Shadow Mode активирован: агенты пишут логи в shadow_agents_log.jsonl")
    await telegram_smoke_test()
    await send_telegram("✅ Predator стартовал. Telegram OK.")

    # ---------------------------------------
    cycle = 0
    trailing_manager: Optional[TrailingStopManager] = None
    position_timer = PositionTimer()

    # Дефолтный контекст новостей и bias
    news_context: Dict[str, Any] = {"bias": None, "summary": ""}
    current_bias: str = "neutral"
    
    # --- Восстановление времени входа позиции при старте бота ---
    try:
        pos_start = await executor.get_position_data(FIGI_NRZ5)
        if pos_start is None:
            print(
                "⏱️ PositionTimer: не удалось получить позицию при старте (сеть), таймер не восстановлен")
        else:
            lots_start = pos_start["lots"]
            avg_price_start = pos_start["average_price"]
            if lots_start != 0 and avg_price_start != 0:
                entry_time = load_last_entry_time_from_history(FIGI_NRZ5)
                if entry_time is not None:
                    position_timer.set_entry_time(entry_time)
                    print(f"⏱️ PositionTimer восстановлен из истории: {entry_time.isoformat()}")
                else:
                    # Если истории нет, стартуем таймер СЕЙЧАС (консервативно)
                    position_timer.start()
                    print(f"⏱️ PositionTimer запущен (позиция восстановлена из портфеля)")
                if position_timer.entry_time:
                    print(f"⏱️ Вход в позицию: {position_timer.entry_time.strftime('%H:%M:%S')} UTC")
            else:
                print(f"⏱️ PositionTimer: на старте позиции нет, таймер не активирован")
    except Exception as e:
        print(f"⚠️ Ошибка при восстановлении таймера позиции на старте: {e}")

    # --- Новости при старте ---
    startup_news = get_fundamental_news()
    if startup_news:
        print(
            f"📰 Новости при запуске: {len(startup_news)} символов из {NEWS_FILE}")
        rules_startup = parse_trading_rules_from_news()
        initial_bias = rules_startup["bias"]
        news_context = {
            "bias": initial_bias,
            "summary": startup_news[:200] if startup_news else "Новостей нет"
        }
        print(f"🧠 Начальный BIAS из файла: {initial_bias.upper()}")
        print(f"📰 Краткое содержание новостей: {news_context['summary']}")
    else:
        print("📰 Новости при запуске: файл пуст или отсутствует, фундаментал отключен.")

    # --- Главный цикл ---
    while True:
        now_msk = dt.datetime.now(pytz.timezone('Europe/Moscow'))
        is_open, status_msg = get_market_status()

        if not is_open:
            global last_sleep_log_time

            now_utc = dt.datetime.now(dt.timezone.utc)
            # логируем не чаще, чем раз в 3 часа
            if (
                last_sleep_log_time is None
                or (now_utc - last_sleep_log_time).total_seconds() >= 3 * 3600
            ):
                print(f"{status_msg}. Бот спит, рынок закрыт.")
                last_sleep_log_time = now_utc

            time.sleep(60)
            continue


        # Если мы здесь - рынок открыт (будни или суббота)
        print(f"✅ {status_msg} | Интерация цикла начала работы...")
        try:
            print("DEBUG: Начало итерации цикла")
            cycle += 1
            print(f"\n⏳ --- {cycle:06d} ---")

            pnl_pct = 0.0

            # 1. Позиция
            pos = await get_position_data_safe(executor, FIGI_NRZ5, retries=3)

            lots = pos["lots"]
            avg_price = pos["average_price"]
            if lots != 0:
                print(f"💼 Позиция: {lots} шт. @ {avg_price:.3f}")

            # 2. Свечи и индикаторы
            print("⚙️ Анализ...")
            candles = await executor.get_candles(FIGI_NRZ5)
            if candles.empty:
                print("⚠️ Нет данных свечей, ждем...")
                await asyncio.sleep(10)
                continue

            data = pipeline_analysis(candles, "NRZ5")
            price = float(data["close"])
            atr = float(data.get("ATR", 0.0))
            print(f"✅ Цена: {price:.3f} | RSI: {data['RSI']:.1f}")

            # 2a. Свечи 5m для тренда
            candles_5m = await executor.get_candles_5m(FIGI_NRZ5)

            # --- Momentum 24h по 5m-свечам ---
            momentum_24h = 0.0
            try:
                if len(candles_5m) >= 288:
                    current_price_5m = float(candles_5m["close"].iloc[-1])
                    price_24h_ago_5m = float(candles_5m["close"].iloc[-288])
                    momentum_24h = (current_price_5m /
                                    price_24h_ago_5m - 1.0) * 100.0
                    print(f"📊 Momentum 24h: {momentum_24h:+.2f}%")
            except Exception as e:
                print(f"⚠️ Ошибка расчёта momentum_24h: {e}")
                momentum_24h = 0.0

            if candles_5m.empty or len(candles_5m) < 200:
                trend_5m = "FLAT"
            else:
                candles_5m["SMA50"] = candles_5m["close"].rolling(
                    window=50).mean()
                candles_5m["SMA200"] = candles_5m["close"].rolling(
                    window=200).mean()
                last_row_5m = candles_5m.iloc[-1]
                sma50_5m = float(last_row_5m["SMA50"])
                sma200_5m = float(last_row_5m["SMA200"])
                sma_diff = abs(sma50_5m - sma200_5m) / \
                    sma200_5m * 100.0 if sma200_5m != 0 else 0.0

                if price > sma50_5m and sma50_5m > sma200_5m:
                    base_trend = "UPTREND"
                elif price < sma50_5m and sma50_5m < sma200_5m:
                    base_trend = "DOWNTREND"
                else:
                    base_trend = "FLAT"

                if base_trend == "FLAT":
                    if momentum_24h > 5:
                        trend_5m = "UPTREND"
                    elif momentum_24h < -5:
                        trend_5m = "DOWNTREND"
                    else:
                        trend_5m = "FLAT"
                elif base_trend == "UPTREND":
                    if momentum_24h < -8:
                        trend_5m = "DOWNTREND"
                    else:
                        trend_5m = "UPTREND"
                elif base_trend == "DOWNTREND":
                    if momentum_24h > 8:
                        trend_5m = "UPTREND"
                    else:
                        trend_5m = "DOWNTREND"

                
            print(f"📊 Тренд 5m: {trend_5m}")

            # 3. PnL
            if lots != 0 and avg_price > 0:
                diff = price - avg_price
                pnl = diff if lots > 0 else -diff
                pnl_pct = pnl / avg_price * 100
                print(f"📈 PnL: {pnl:.3f} ({pnl_pct:.2f}%)")

            # 4. SL/TP
            stop_price = None
            tp1_price = None
            tp2_price = None
            if lots > 0 and avg_price > 0 and atr > 0:
                stop_price_raw = avg_price - 2.0 * atr
                stop_price_min = avg_price * 0.95
                stop_price = min(stop_price_raw, stop_price_min)
                tp1_price = avg_price + 1.5 * atr
                tp2_price = avg_price + 3.0 * atr

            # 5. Дневное изменение и экспирация
            daily_change = calc_daily_change(candles)
            daily_change_abs = abs(daily_change)
            exp_date = await executor.get_future_expiration(FIGI_NRZ5)
            days_to_expiration = (
                (exp_date - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400.0
                if exp_date
                else 999.0
            )

            # 5.3. Получить метрики времени для новой логики фиксации
            minutes_to_clearing = get_minutes_to_clearing()
            holding_hours = position_timer.get_holding_hours()
           
            if cycle % 10 == 0 and lots > 0:
                print(
                    f"⏱️ Метрики: holding={holding_hours:.1f}ч | до клиринга={minutes_to_clearing}мин")

            # 5.4. Проверка новых правил фиксации (ПРИОРИТЕТ 2-4)
            early_exit_action: Optional[str] = None
            early_exit_reason: str = ""

            if check_stale_position(lots, pnl_pct, holding_hours):
                early_exit_action = "SELL_ALL"
                early_exit_reason = "STALE_POSITION"

            elif minutes_to_clearing <= 60:
                clearing_action = check_clearing_profit_take(
                    lots, pnl_pct, minutes_to_clearing,
                    trend_5m, "HOLD", 0
                )
                if clearing_action:
                    early_exit_action = clearing_action
                    early_exit_reason = "CLEARING_TP"

            elif check_intraday_profit_take(lots, pnl_pct, holding_hours, minutes_to_clearing):
                early_exit_action = "SELL_HALF"
                early_exit_reason = "INTRADAY_TP"

            if early_exit_action and lots > 0:
                if early_exit_action == "SELL_ALL":
                    qty = lots
                    ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", qty, why=f"{action} | AI={signal}({confidence}%)")
                    if ok:
                        await send_telegram(
                            f"🎯 {early_exit_reason} по NRZ5: SELL {qty} @ {price:.3f}\n"
                            f"PnL: {pnl_pct:+.2f}% | Holding: {holding_hours:.1f}ч"
                        )
                        await log_trade(
                            action=early_exit_reason,
                            figi=FIGI_NRZ5,
                            lots_before=lots,
                            lots_after=0,
                            price=price,
                            signal=early_exit_reason,
                            confidence=100.0,
                            reason=f"{early_exit_reason}: PnL={pnl_pct:+.2f}%, holding={holding_hours:.1f}h",
                        )
                        trailing_manager = None
                        position_timer.reset()
                        lots = 0

                elif early_exit_action == "SELL_HALF":
                    qty = max(1, lots // 2)
                    ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", qty, why=f"{action} | AI={signal}({confidence}%)")
                    if ok:
                        await send_telegram(
                            f"💰 {early_exit_reason} по NRZ5: SELL {qty} @ {price:.3f}\n"
                            f"PnL: {pnl_pct:+.2f}% | Остаток: {lots - qty} лотов"
                        )
                        await log_trade(
                            action=early_exit_reason,
                            figi=FIGI_NRZ5,
                            lots_before=lots,
                            lots_after=lots - qty,
                            price=price,
                            signal=early_exit_reason,
                            confidence=100.0,
                            reason=f"{early_exit_reason}: partial, PnL={pnl_pct:+.2f}%",
                        )
                        lots -= qty
                        if trailing_manager:
                            breakeven_price = trailing_manager.entry_price * 1.003
                            if breakeven_price > trailing_manager.trailing_stop:
                                trailing_manager.trailing_stop = breakeven_price
                                print(
                                    f"🔒 Trailing Stop подтянут в безубыток: {breakeven_price:.4f} (+0.3%)")

                print(
                    f"⏳ Цикл {cycle} завершён по {early_exit_reason} | price={price:.3f} | lots={lots}")
                
            # ========== STARTUP FIX: восстановить трейлинг, если позиция открыта но менеджер = None ==========
            if lots != 0 and trailing_manager is None and avg_price > 0 and atr > 0:
                if lots > 0:
                    trailing_manager = TrailingStopManager(entry_price=avg_price, atr=atr, trend=trend_5m)
                    print(f"✅ Трейлинг восстановлен: LONG @ {avg_price:.4f}")
                else:
                    trailing_manager = TrailingStopManagerShort(entryprice=avg_price, atr=atr, trend=trend_5m)

                    print(f"✅ Трейлинг восстановлен: SHORT @ {avg_price:.4f}")
                if position_timer.entry_time is None:
                    position_timer.start()
                    
            print(f"DEBUG TRAILING: lots={lots}, tm={'EXISTS' if trailing_manager else 'NONE'}, avgprice={avg_price:.4f}, atr={atr:.4f}")



            if early_exit_action:
                pass
            else:
                # 5.5. Проверка Trailing Stop (ПРИОРИТЕТ 2)
                # ========== 5.5. TRAILING STOP LOGIC ==========
                trailing_stop_triggered = False

                if lots != 0 and trailing_manager is not None:
                    should_exit = trailing_manager.update(price, trend_5m)

                    if should_exit:
                        trailing_stop_triggered = True

                        if lots > 0:
                            direction = "SELL"
                            qty = lots
                            msg_side = "LONG"
                            extra = f"Entry {trailing_manager.entryprice:.4f}, Max {trailing_manager.maxprice:.4f}"
                        else:
                            direction = "BUY"
                            qty = abs(lots)
                            msg_side = "SHORT"
                            extra = f"Entry {trailing_manager.entryprice:.4f}, Min {trailing_manager.minprice:.4f}"

                        ok = await post_order_guarded(
                            executor,
                            FIGI_NRZ5,
                            direction,
                            qty,
                            why=f"TRAILING_STOP | AI={signal}({confidence}%)",
                        )

                        if ok:
                            await send_telegram(
                                f"🧯 Trailing Stop {msg_side} NRZ5: {direction} {qty} @ {price:.3f} | {extra}"
                            )
                            await log_trade(
                                action="TRAILING_STOP",
                                figi=FIGI_NRZ5,
                                lots_before=lots,
                                lots_after=0,
                                price=price,
                                signal=signal,
                                confidence=100.0,
                                reason=f"Trailing stop fired. {extra}",
                            )

                        trailing_manager = None
                        position_timer.reset()
                        lots = 0
                        print(f"⏳ Цикл {cycle} завершён по trailing-stop | price={price:.3f}")



                # ============================================================================
                # ✅ AI АНАЛИЗ - НА ОДНОМ УРОВНЕ С TRAILING STOP!
                # (Не внутри if trailing_stop_triggered!)
                # ============================================================================

                if not trailing_stop_triggered:  
                    # --- [ОБНОВЛЕННЫЙ ГИБРИДНЫЙ СБОР НОВОСТЕЙ] ---
                    # 1. Читаем ручной файл (приоритет)
                    manual_news = get_fundamental_news()
                    
                    # 2. Тянем свежие новости из API (Finnhub + NewsAPI)
                    api_news = await news_agent.get_aggregated_sentiment_context()
                    
                    # 3. Формируем финальный контекст для Planner/Analyst
                    # Если в файле пусто, используем данные из API
                    current_news_context = manual_news if len(manual_news) > 10 else api_news
                    
                    # 4. Обновляем правила на основе контекста
                    rules_current = parse_trading_rules_from_news() # Для лимитов цен
                    current_bias = rules_current["bias"]
                    
                    # 5. Если в файле NEUTRAL, Planner может передумать на основе API_NEWS
                    # Здесь можно добавить вызов planner.create_daily_plan(current_news_context)
                    # ---------------------------------------------

            if current_bias == "neutral":
                if momentum_24h > 8.0:
                    current_bias = "bullish"
                elif momentum_24h < -8.0:
                    current_bias = "bearish"


                    print(f"🧭 Текущий BIAS: {current_bias.upper()}")

                    # ========== AI ANALYSIS - CLEAN MAPPING + ASYNC ==========
                    ai_input_clean = {
                        "ticker": "NRZ5",
                        "close": float(price),
                        "Kalman_Trend": data.get("Kalman_Trend", data.get("kalman_trend", "FLAT")),
                        "Kalman_Price": data.get("Kalman_Price", data.get("kalman_price", 0.0)),
                        "RSI": float(data.get("RSI", 50)),
                        "SMA_50": float(data.get("SMA_50", data.get("sma_50", 0.0))),
                        "BB_Width": float(data.get("BB_Width", data.get("bb_width", 0.0))),
                    }

                    print(
                        f"🔄 AI запрос: bias={current_bias}, RSI={ai_input_clean['RSI']:.1f}, "
                        f"Kalman={ai_input_clean['Kalman_Trend']}"
                    )

                    # ✅ НОВОЕ: Асинхронный вызов с BIAS!
                    try:
                        ai_result = await analyst.analyze(
                            market_data=ai_input_clean,
                            news_context=current_bias,
                            bias=current_bias
                        )

                        signal = ai_result.signal
                        confidence = ai_result.confidence
                        reason = ai_result.reason

                        print(f"🤖 AI Decision: {signal} ({confidence}%) | {reason[:60]}...")
                    
                    except Exception as e:
                        print(f"❌ Ошибка AI анализа: {e}")
                        signal = "HOLD"
                        confidence = 0
                        reason = f"Ошибка: {str(e)[:50]}"

                    # === SHADOW MODE: Логирование работы трёх агентов ===
                    try:
                        shadow_market_data = {
                        "close": price,
                        "RSI": float(data.get("RSI", 50)),
                        "ATR": atr,
                        "trend_5m": trend_5m,
                        "momentum_24h": momentum_24h,
                        "trend_h1": data.get("trend_h1", "FLAT"),
                        "ai_signal": signal if isinstance(signal, str) else str(signal),
                        "ai_confidence": int(confidence) if confidence else 0,
                        "ai_reason": reason if isinstance(reason, str) else str(reason),
                        }

                        shadow_pos_data = {
                            "lots": int(lots),
                            "avg_price": float(avg_price),
                            "pnl_pct": float(pnl_pct),
                        }
                    
                        asyncio.create_task(
                            runshadowanalysisnonblocking(
                                shadowadapter,
                                shadow_market_data,
                                shadow_pos_data,
                                current_bias,
                                signal,
                                confidence,
                                reason,
                            )
                        )

                    except asyncio.TimeoutError:
                        print("⏱️ Shadow timeout (>15s) - пропущено, продолжаем...")
                    except Exception as e:
                        print(f"❌ Shadow Error: {e}")

                    # === ПРОВЕРКА МАКСИМАЛЬНОГО РАЗМЕРА ПОЗИЦИИ ===
                    max_lots_allowed, lots_reason = get_max_lots_allowed()
                    if lots_reason:
                        print(lots_reason)

                    # === ПРОВЕРКА КОМИССИЙ ===
                    action_blocked_by_commission = False

                    if lots > 0 and 0 < pnl_pct < MIN_PROFIT_PCT:
                        if holding_hours < 0.5:
                            action_blocked_by_commission = True
                            print(f"⏸️ PnL +{pnl_pct:.2f}% < мин.{MIN_PROFIT_PCT}% "
                                f"и holding {holding_hours*60:.0f}мин → HOLD (жду роста)")

                    # Вызов решателя (ОБЯЗАТЕЛЬНО добавь rules=rules_current в конец!)
                    action = decide_action(
                    lots=lots,                    # количество лотов сейчас
                    max_lots_allowed=MAX_LOTS,     # ТУТ ПРОВЕРЬ: может у тебя MAX_LOTS?
                    ai_signal=signal,             # сигнал от AI
                    ai_confidence=confidence,     # уверенность AI
                    trend_5m=trend_5m,            # тренд
                    rsi=data['RSI'],              # ТУТ ПРОВЕРЬ: если переменной rsi нет, пиши data['RSI']
                    bias=current_bias,            # текущий байас
                    rules=rules_current           # правила из новостей
                )
                    

                    # === БЛОКИРОВКИ И ФИЛЬТРЫ ===
                    action_reason = ""

                    if action_blocked_by_commission and action in ("SELL_ALL", "SELL_HALF"):
                        action_reason = f"Профит {pnl_pct:.2f}% < мин.{MIN_PROFIT_PCT}%"
                        action = "NOOP"

                    elif action == "BUY_1" and rules_current.get("max_buy_price"):
                        if price > rules_current["max_buy_price"]:
                            action_reason = f"Цена {price:.3f} > лимита {rules_current['max_buy_price']:.3f}"
                            action = "NOOP"

                    elif action in ("SELL_ALL", "SELL_HALF") and rules_current.get("min_sell_price"):
                        if price < rules_current["min_sell_price"]:
                            action_reason = f"Цена {price:.3f} < мин.порога {rules_current['min_sell_price']:.3f}"
                            action = "NOOP"

                    elif action == "BUY_1" and lots >= max_lots_allowed:
                        action_reason = f"Лимит позиции {max_lots_allowed} лот(ов)"
                        action = "NOOP"

                    # === ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ ===
                    log_decision_block(
                        cycle=cycle,
                        price=price,
                        rsi=float(data.get("RSI", 50)),
                        trend=trend_5m,
                        lots=lots,
                        pnl_pct=pnl_pct,
                        holding_hours=holding_hours,
                        ai_signal=signal,
                        ai_confidence=confidence,
                        bias=current_bias,
                        minutes_to_clearing=minutes_to_clearing,
                        rules=rules_current,
                        action=action,
                        reason=action_reason
                    )
                    
                    # Алерт на сильный сигнал, который может быть проигнорирован
                    if confidence >= 85 and action == "NOOP":
                        await send_telegram(
                            f"⚠️ <b>STRONG SIGNAL IGNORED</b>\n"
                            f"🤖 AI: {signal} ({confidence}%)\n"
                            f"📊 RSI: {data['RSI']:.1f} | Trend: {trend_5m}\n"
                            f"📝 Причина AI: {reason[:100]}..."
                        )

                    # 7. Risk-логика и Алерты
                    ignore_emergency = lots == 0 or avg_price <= 0 or atr <= 0
                    action_from_risk: Optional[Literal["SELL_1", "SELL_ALL"]] = None

                    if lots > 0 and avg_price > 0 and atr > 0:
                        emergency_exit = (avg_price - price) >= 2.0 * atr
                        if not ignore_emergency and emergency_exit:
                            msg = f"🚨 <b>EMERGENCY EXIT</b>\nЦена упала ниже 2*ATR от входа!"
                            await send_telegram(msg)
                            action_from_risk = "SELL_ALL"
                        elif stop_price is not None and price <= stop_price:
                            await send_telegram(f"🛑 <b>STOP-LOSS</b>\nСработал общий стоп по цене {price}")
                            action_from_risk = "SELL_ALL"

                    # Алерт на сильный проигнорированный сигнал (для отладки стратегии)
                    if confidence >= 85 and action == "NOOP":
                        await send_telegram(f"⚠️ <b>STRONG SIGNAL IGNORED</b>\nAI: {signal} ({confidence}%)\nПричина AI: {reason[:100]}...")

                    # 8. Исполнение
                    if action_from_risk == "SELL_ALL":
                        qty = abs(lots)
                        if qty > 0:
                            ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", qty, why="Risk Exit")
                            if ok:
                                # Лог и сброс таймеров уже внутри вашего кода...
                                await send_telegram(f"📉 <b>RISK EXIT ИСПОЛНЕН:</b> SELL {qty} @ {price:.3f}")
                                trailing_manager = None
                                position_timer.reset()

                    elif action in ("OPEN_LONG", "ADD_LONG", "OPEN_SHORT", "ADD_SHORT", "CLOSE_LONG", "CLOSE_SHORT"):
                        # Определяем направление и количество
                        direction = "BUY" if "LONG" in action or "CLOSE_SHORT" in action else "SELL"
                        qty = 1 if "ADD" in action or "OPEN" in action else abs(lots)
                        
                        ok = await post_order_guarded(executor, FIGI_NRZ5, direction, qty, why=f"{action} (AI: {confidence}%)")
                        
                        if ok:
                            # Уведомление об успешном действии стратегии
                            icon = "🟢" if direction == "BUY" else "🔴"
                            await send_telegram(f"{icon} <b>{action}:</b> {direction} {qty} @ {price:.3f}\nAI: {signal} ({confidence}%)")
                            
                            # Обновление менеджеров (как в вашем оригинальном коде)
                            if "OPEN" in action:
                                position_timer.start()
                                if "LONG" in action:
                                    trailing_manager = TrailingStopManager(entry_price=price, atr=atr, trend=trend_5m)
                                else:
                                    trailing_manager = TrailingStopManagerShort(entry_price=price, atr=atr, trend=trend_5m)
                            elif "CLOSE" in action:
                                trailing_manager = None
                                position_timer.reset()


                    #    if action == "NOOP":
                    #        status = "ДЕРЖИМ" if lots != 0 else "NOOP"
                    #        print(f"⏳ Цикл {cycle} | {status} | lots={lots} | price={price:.3f} | "
                    #              f"RSI={data['RSI']:.1f} | trend={trend_5m} | "
                    #              f"bias={current_bias} | AI={signal}({confidence}%) | "
                    #              f"momentum_24h={momentum_24h:+.2f}%")

                        elif action == "BUY_1":
                            if lots < 0:
                                qty = abs(lots)
                                ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", qty, why=f"{action} | AI={signal}({confidence}%)")
                                if ok:
                                    await send_telegram(f"🟢 Закрытие шорта по NRZ5: BUY {qty} @ {price:.3f}")
                                    await log_trade(
                                        action="CLOSE_SHORT",
                                        figi=FIGI_NRZ5,
                                        lots_before=lots,
                                        lots_after=0,
                                        price=price,
                                        signal=signal,
                                        confidence=confidence,
                                        reason=reason,
                                    )

                            elif 0 <= lots < MAX_LOTS:
                                ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", qty, why=f"{action} | AI={signal}({confidence}%)")
                                if ok:
                                    if lots == 0:
                                        trailing_manager = TrailingStopManager(
                                            entry_price=price,
                                            atr=atr,
                                            trend=trend_5m,
                                        )
                                        position_timer.start()

                                        await send_telegram(
                                            f"🟢 Открытие LONG по NRZ5: BUY 1 @ {price:.3f} "
                                            f"(AI {signal}, {confidence:.0f}%)"
                                        )
                                        lots_after = lots + 1
                                        await log_trade(
                                            action="OPEN_LONG",
                                            figi=FIGI_NRZ5,
                                            lots_before=lots,
                                            lots_after=lots_after,
                                            price=price,
                                            signal=signal,
                                            confidence=confidence,
                                            reason=reason,
                                        )
                                    else:
                                        await send_telegram(
                                            f"🟢 Добор LONG по NRZ5: BUY 1 @ {price:.3f} "
                                            f"(lots -> {lots + 1}, AI {signal}, {confidence:.0f}%)"
                                        )
                                        lots_after = lots + 1
                                        await log_trade(
                                            action="ADD_LONG",
                                            figi=FIGI_NRZ5,
                                            lots_before=lots,
                                            lots_after=lots_after,
                                            price=price,
                                            signal=signal,
                                            confidence=confidence,
                                            reason=reason,
                                        )

                        elif action == "SELL_ALL":
                            if lots > 0:
                                qty = lots
                                ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", qty, why=f"{action} | AI={signal}({confidence}%)")
                                if ok:
                                    await send_telegram(
                                        f"📉 Полный выход по AI по NRZ5: SELL {qty} @ {price:.3f} "
                                        f"(AI {signal}, {confidence:.0f}%)"
                                    )
                                    await log_trade(
                                        action="CLOSE_ALL_AI",
                                        figi=FIGI_NRZ5,
                                        lots_before=lots,
                                        lots_after=0,
                                        price=price,
                                        signal=signal,
                                        confidence=confidence,
                                        reason=reason,
                                    )
                                    trailing_manager = None
                                    position_timer.reset()

                       # if action != "NOOP":
                       #      print(f"⏳ Цикл {cycle} | ACTION={action} | lots={lots} | "
                       #         f"price={price:.3f} | RSI {data['RSI']:.1f} | {signal} ({confidence}%)")
                    # --- [ФИНАЛЬНЫЙ ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ЦИКЛА] ---
        except Exception as e:
            error_msg = f"💥 <b>ГЛОБАЛЬНАЯ ОШИБКА ЦИКЛА</b>\n<code>{str(e)[:500]}</code>"
            print(error_msg)
            await send_telegram(error_msg) # Обязательно шлем алерт о падении цикла
            await asyncio.sleep(CHECK_INTERVAL_SEC)

            # Умный режим сна
            msk_now = dt.datetime.now(dt.timezone.utc) + timedelta(hours=3)
            current_weekday = msk_now.weekday()
            current_hour = msk_now.hour
            current_minute = msk_now.minute

            sleep_time = 60
            mode_name = "🔥 Активная сессия"

            minutes_now = current_hour * 60 + current_minute

            if current_weekday == 6:
                sleep_time = 300
                mode_name = "🌙 Выходные"
            elif minutes_now < (8 * 60 + 45):
                sleep_time = 180
                mode_name = "🌅 Перед открытием (Ждём 08:45 МСК)"
            elif 8 * 60 + 45 <= minutes_now < 10 * 60:
                sleep_time = 60
                mode_name = "🌅 Предоткрытие"
            elif 10 * 60 <= minutes_now < 14 * 60:
                sleep_time = 120
                mode_name = "☕ День (Ждём Америку)"
            elif 14 * 60 <= minutes_now < 19 * 60:
                sleep_time = 90
                mode_name = "🇺🇸 Америка просыпается"
            elif 19 * 60 <= minutes_now < 24 * 60:
                sleep_time = 60
                mode_name = "🔥 Активная сессия (США)"
            else:
                sleep_time = 180
                mode_name = "😴 Ночь"

            print(f"{mode_name}. Пауза {sleep_time} сек.\n")
            await asyncio.sleep(sleep_time)

        except Exception as e:
            print(f"💥 Глобальная ошибка цикла: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main_loop())
