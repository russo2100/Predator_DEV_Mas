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


def log_decision_block(cycle: int, price: float, rsi: float, trend: str, lots: int, pnl_pct: float, 
                       holding_hours: float, ai_signal: str, ai_confidence: float, bias: str, 
                       minutes_to_clearing: int, rules: dict, action: str, reason: str):
    """
    Выводит блок принятия решения в консоль и записывает его в shadow_agents_log.jsonl.
    """
    import json
    import os
    from datetime import datetime
    import pytz

    # 1. Визуальный вывод в консоль
    print("\n" + "="*70)
    print(f"📊 DECISION BLOCK | Cycle: {cycle} | {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M:%S')}")
    print("-" * 70)
    print(f"💰 Price: {price:.3f} | RSI: {rsi:.1f} | Trend: {trend}")
    print(f"📦 Lots: {lots} | PnL: {pnl_pct:.2f}% | Holding: {holding_hours:.1f}h")
    print(f"🤖 AI: {ai_signal} ({ai_confidence:.0f}%) | BIAS: {bias.upper()}")
    
    if rules.get('max_buy_price'):
        print(f"🟢 MAX_BUY: {rules['max_buy_price']:.3f}")
    if rules.get('min_sell_price'):
        print(f"🔴 MIN_SELL: {rules['min_sell_price']:.3f}")

    emoji_map = {"BUY": "🚀", "SELL_ALL": "💥", "SELL_HALF": "⚖️", "NOOP": "😴"}
    emoji = emoji_map.get(action, "🔍")
    print(f"➡️ ACTION: {emoji} {action}")
    if reason:
        print(f"📝 Reason: {reason}")
    print("="*70 + "\n")

    # 2. Формируем расширенный лог (похожий на ваш старый формат)
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
            "bias": bias
        },
        "decision": {
            "ai_signal": ai_signal,
            "ai_confidence": ai_confidence,
            "action": action,
            "reason": reason,
            "rules": rules
        }
    }

    # 3. Запись в файл shadow_agents_log.jsonl
    try:
        log_file = "shadow_agents_log.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"❌ Ошибка записи в shadow_agents_log.jsonl: {e}")



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
    # 1. Нормализация входа
    sig = (ai_signal or "HOLD").upper().strip()
    b = (bias or "neutral").lower().strip()
    rules = rules or {}

    # 2. FORCE BUY (как у тебя было)
    if rules.get("force_buy") is True:
        if lots == 0:
            print("🚨 FORCE_BUY ОБНАРУЖЕН! Игнорируем AI и входим.")
            return "OPEN_LONG"
        elif 0 < lots < max_lots_allowed:
            return "ADD_LONG"

    # 3. AI слишком неуверен — ничего не делаем
    if ai_confidence < 60:
        return "NOOP"

    # 4. HOLD как явный сигнал
    if sig == "HOLD":
        return "NOOP"

    # 5. LONG‑логика (покупка)
    if sig == "BUY" and lots >= 0:
        # жесткий фильтр по BIAS: при явном bearish не лезем, если рынок не дикого перепродана
        if b == "bearish" and rsi > 25:
            return "NOOP"

        # перекупленность без ап‑тренда не берем
        if rsi > 70 and trend_5m != "UPTREND":
            print(f"⚠️ RSI {rsi:.1f} перекупленность без ап‑тренда, вход LONG заблокирован")
            return "NOOP"

        # медвежий тренд + высокий RSI — не усредняемся против движения
        if trend_5m == "DOWNTREND" and rsi > 50:
            return "NOOP"

        # ВХОД: если позы нет — открываем 1 лот
        if lots == 0:
            if max_lots_allowed > 0:
                return "BUY_1"
            return "NOOP"

        # ДОБОР: есть позиция, но не превышаем лимит
        if 0 < lots < max_lots_allowed and rsi < 60 and trend_5m in ("UPTREND", "FLAT"):
            return "BUY_1"

        return "NOOP"

    # 6. SHORT/выход (SELL‑логика)
    if sig == "SELL" and lots > 0:
        # при явном bullish не закрываем всё сразу, если нет перекупленности
        if b == "bullish" and rsi < 75:
            return "NOOP"

        # перепроданность без даун‑тренда — не рубим всё
        if rsi < 30 and trend_5m != "DOWNTREND":
            print(f"⚠️ RSI {rsi:.1f} перепроданность без даун‑тренда, полный выход заблокирован")
            return "NOOP"

        # сильный SELL — полный выход
        if ai_confidence >= 70 or trend_5m == "DOWNTREND":
            return "SELL_ALL"

        # если уверенность поменьше — можно оставить как NOOP или SELL_HALF (если поддерживаешь)
        return "NOOP"

    # 7. Если сигналы не BUY/SELL — ничего не делаем
    return "NOOP"



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
    news_agent = UnifiedNewsAgent() 
    shadowadapter = MultiAgentShadowAdapter()
      
    shadow_adapter = MultiAgentShadowAdapter()
    print("👻 Shadow Mode активирован: агенты пишут логи в shadow_agents_log.jsonl")
    await send_telegram("✅ Predator стартовал. Telegram OK.")

    cycle = 0
    trailing_manager: Optional[TrailingStopManager] = None
    position_timer = PositionTimer()
    
    last_api_news_time = None
    cached_api_news = ""

    news_context: Dict[str, Any] = {"bias": None, "summary": ""}
    current_bias: str = "neutral"

    # Восстановление позиции при старте
    try:
        pos_start = await executor.get_position_data(FIGI_NRZ5)
        if pos_start is None:
            print("⏱️ PositionTimer: не удалось получить позицию при старте (сеть)")
        else:
            lots_start = pos_start["lots"]
            avg_price_start = pos_start["average_price"]
            if lots_start != 0 and avg_price_start != 0:
                entry_time = load_last_entry_time_from_history(FIGI_NRZ5)
                if entry_time is not None:
                    position_timer.set_entry_time(entry_time)
                    print(f"⏱️ PositionTimer восстановлен: {entry_time.isoformat()}")
                else:
                    position_timer.start()
                    print(f"⏱️ PositionTimer запущен (позиция восстановлена)")
    except Exception as e:
        print(f"⚠️ Ошибка восстановления таймера: {e}")

    # Новости при старте
    startup_news = get_fundamental_news()
    if startup_news:
        print(f"📰 Новости: {len(startup_news)} символов")
        rules_startup = parse_trading_rules_from_news()
        initial_bias = rules_startup["bias"]
        news_context = {"bias": initial_bias, "summary": startup_news[:200] if startup_news else ""}
        print(f"🧠 Начальный BIAS: {initial_bias.upper()}")
    else:
        print("📰 Новости: файл пуст")

    # Главный цикл
    while True:
        now_msk = dt.datetime.now(pytz.timezone('Europe/Moscow'))
        is_open, status_msg = get_market_status()

        if not is_open:
            # Используем глобальную переменную для контроля логов сна
            global last_sleep_log_time
            now_utc = dt.datetime.now(dt.timezone.utc)
            
            if last_sleep_log_time is None or (now_utc - last_sleep_log_time).total_seconds() >= 3 * 3600:
                print(f"😴 {status_msg}. Бот в режиме ожидания.")
                last_sleep_log_time = now_utc
                
            await asyncio.sleep(60) # <--- ТЕПЕРЬ ПРАВИЛЬНО
            continue

        # Рынок открыт
        print(f"✅ {status_msg} | Цикл начинается...")
        try:
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
                print("⚠️ Нет свечей, ждем...")
                await asyncio.sleep(10)
                continue

            data = pipeline_analysis(candles, "NRZ5")
            price = float(data["close"])
            atr = float(data.get("ATR", 0.0))
            print(f"✅ Цена: {price:.3f} | RSI: {data['RSI']:.1f}")

            # 2a. Свечи 5m для тренда (упрощенно)
            candles_5m = await executor.get_candles_5m(FIGI_NRZ5)
            
            momentum_24h = 0.0
            try:
                if len(candles_5m) >= 288:
                    current_price_5m = float(candles_5m["close"].iloc[-1])
                    price_24h_ago_5m = float(candles_5m["close"].iloc[-288])
                    momentum_24h = (current_price_5m / price_24h_ago_5m - 1.0) * 100.0
                    print(f"📊 Momentum 24h: {momentum_24h:+.2f}%")
            except Exception as e:
                print(f"⚠️ Ошибка momentum: {e}")
                momentum_24h = 0.0

            if candles_5m.empty or len(candles_5m) < 200:
                trend_5m = "FLAT"
            else:
                candles_5m["SMA50"] = candles_5m["close"].rolling(window=50).mean()
                candles_5m["SMA200"] = candles_5m["close"].rolling(window=200).mean()
                last_row_5m = candles_5m.iloc[-1]
                sma50_5m = float(last_row_5m["SMA50"])
                sma200_5m = float(last_row_5m["SMA200"])

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
                else:
                    trend_5m = base_trend

            print(f"📊 Тренд 5m: {trend_5m}")

            # 3. PnL
            if lots != 0 and avg_price > 0:
                diff = price - avg_price
                pnl = diff if lots > 0 else -diff
                pnl_pct = pnl / avg_price * 100
                print(f"📈 PnL: {pnl:.3f} ({pnl_pct:.2f}%)")

            # 4. SL/TP
            stop_price = None
            if lots > 0 and avg_price > 0 and atr > 0:
                stop_price_raw = avg_price - 2.0 * atr
                stop_price_min = avg_price * 0.95
                stop_price = min(stop_price_raw, stop_price_min)

            # 5. Дневное изменение и Экспирация
            daily_change = calc_daily_change(candles)
            exp_date = await executor.get_future_expiration(FIGI_NRZ5)
            days_to_expiration = (exp_date - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400.0 if exp_date else 999.0

            # 5.3. Метрики времени
            minutes_to_clearing = get_minutes_to_clearing()
            holding_hours = position_timer.get_holding_hours()

            # 5.4. Ранние выходы (SELL_ALL, SELL_HALF)
            early_exit_action: Optional[str] = None
            early_exit_reason: str = ""

            if check_stale_position(lots, pnl_pct, holding_hours):
                early_exit_action = "SELL_ALL"
                early_exit_reason = "STALE_POSITION"

            elif minutes_to_clearing <= 60:
                clearing_action = check_clearing_profit_take(
                    lots, pnl_pct, minutes_to_clearing, trend_5m, "HOLD", 0
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
                    ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", qty, why=f"Early exit | {early_exit_reason}")
                    if ok:
                        await send_telegram(f"🎯 {early_exit_reason}: SELL {qty} @ {price:.3f}")
                        trailing_manager = None
                        position_timer.reset()
                        lots = 0

                elif early_exit_action == "SELL_HALF":
                    qty = max(1, lots // 2)
                    ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", qty, why=f"Early exit | {early_exit_reason}")
                    if ok:
                        await send_telegram(f"💰 {early_exit_reason}: SELL {qty} @ {price:.3f}")
                        lots -= qty
                        if trailing_manager:
                            breakeven_price = trailing_manager.entry_price * 1.003
                            if breakeven_price > trailing_manager.trailing_stop:
                                trailing_manager.trailing_stop = breakeven_price

                print(f"⏳ Цикл {cycle} завершен по {early_exit_reason}")

            # Восстановление trailing stop
            if lots != 0 and trailing_manager is None and avg_price > 0 and atr > 0:
                if lots > 0:
                    trailing_manager = TrailingStopManager(entry_price=avg_price, atr=atr, trend=trend_5m)
                    print(f"✅ Трейлинг восстановлен: LONG @ {avg_price:.4f}")

            print(f"DEBUG TRAILING: lots={lots}, tm={'EXISTS' if trailing_manager else 'NONE'}")

            if not early_exit_action:
                # 5.5. Trailing Stop Logic
                trailing_stop_triggered = False

                if lots != 0 and trailing_manager is not None:
                    should_exit = trailing_manager.update(price, trend_5m)
                    if should_exit:
                        trailing_stop_triggered = True
                        direction = "SELL" if lots > 0 else "BUY"
                        qty = abs(lots)
                        ok = await post_order_guarded(executor, FIGI_NRZ5, direction, qty, why="TRAILING_STOP")
                        if ok:
                            trailing_manager = None
                            position_timer.reset()
                            lots = 0
                            print(f"⏳ Цикл {cycle} завершен по trailing-stop")

                if not trailing_stop_triggered:
                    # --- [ПОЛНОСТЬЮ АВТОНОМНЫЙ БЛОК: НОВОСТИ + AI] ---
                    rules_current = {}
                    current_news_context = "Нет данных"
                    action_blocked_by_commission = False  # Гарантируем наличие

                    # 1. Сбор новостей и BIAS
                    try:
                        manual_news = get_fundamental_news()
                        now_utc = dt.datetime.now(dt.timezone.utc)
                        if last_api_news_time is None or (now_utc - last_api_news_time).total_seconds() > 1200:
                            cached_api_news = await news_agent.get_aggregated_sentiment_context()
                            last_api_news_time = now_utc

                        # Жесткая очистка от словарей (dict -> str)
                        if isinstance(manual_news, dict):
                            news_text = str(manual_news.get('summary', manual_news))
                        else:
                            news_text = str(manual_news)
                        
                        current_news_context = news_text if len(news_text) > 10 else str(cached_api_news)
                        
                        rules_current = parse_trading_rules_from_news()
                        current_bias = str(rules_current.get("bias", "neutral"))
                    except Exception as e:
                        print(f"⚠️ Ошибка новостей: {e}")
                        current_bias = "neutral"

                    # Динамический BIAS
                    if current_bias == "neutral":
                        if momentum_24h > 8.0: current_bias = "bullish"
                        elif momentum_24h < -8.0: current_bias = "bearish"
                    print(f"🧭 BIAS: {current_bias.upper()}")

                    # 2. Расчет блокировки по комиссии (возвращаем расчет внутрь)
                    if lots > 0 and 0 < pnl_pct < MIN_PROFIT_PCT:
                        if holding_hours < 0.5:
                            action_blocked_by_commission = True
                            print(f"⏸️ Комиссия: PnL {pnl_pct:.2f}% < {MIN_PROFIT_PCT}% → HOLD")

                    # 3. AI АНАЛИЗ (ФИНАЛЬНОЕ РЕШЕНИЕ ОШИБКИ ТИПОВ)
                    # Берем значения напрямую из data, чтобы не было NameError
                    current_rsi = data.get('RSI', 50)
                    
                    market_str = (
                        f"Ticker: NRZ5, Price: {price:.3f}, RSI: {current_rsi:.1f}, "
                        f"Trend: {trend_5m}, Momentum: {momentum_24h:.2f}%"
                    )

                    print(f"🔄 AI запрос (текстовый режим)... BIAS: {current_bias}")
                    try:
                        ai_result = await asyncio.wait_for(
                            analyst.analyze(
                                market_data=market_str,  # Передаем строку
                                news_context=str(current_news_context),
                                bias=str(current_bias)
                            ),
                            timeout=30.0
                        )
                        signal, confidence, reason = ai_result.signal, ai_result.confidence, ai_result.reason
                        print(f"🤖 AI Decision: {signal} ({confidence}%)")
                    except Exception as e:
                        print(f"❌ Ошибка в analyst.analyze: {e}")
                        signal, confidence, reason = "HOLD", 0, f"AI Error: {e}"



                    # 4. Решение и Лимиты
                    max_lots_allowed, _ = get_max_lots_allowed()
                    action = decide_action(
                        lots=lots, max_lots_allowed=max_lots_allowed,
                        ai_signal=signal, ai_confidence=confidence,
                        trend_5m=trend_5m, rsi=data.get('RSI', 50),
                        bias=current_bias, rules=rules_current
                    )

                    # 5. Блокировки и Риски
                    action_reason = ""
                    if action == "BUY_1" and lots >= max_lots_allowed:
                        action, action_reason = "NOOP", f"Лимит {max_lots_allowed} лотов"
                    elif action_blocked_by_commission and action in ("SELL_ALL", "SELL_HALF"):
                        action, action_reason = "NOOP", "Блокировка комиссией"

                    # 6. Экстренный выход
                    if lots > 0 and avg_price > 0 and atr > 0:
                        if (avg_price - price) >= 2.0 * atr or (stop_price and price <= stop_price):
                            print("🚨 EMERGENCY EXIT!")
                            ok = await post_order_guarded(executor, FIGI_NRZ5, "SELL", abs(lots), why="Risk Exit")
                            if ok:
                                action, action_reason = "SELL_ALL", "RISK_EXIT_EXECUTED"
                                trailing_manager, lots = None, 0
                                position_timer.reset()

                    # 7. ФИНАЛЬНОЕ ЛОГИРОВАНИЕ
                    log_decision_block(
                        cycle=cycle, price=price, rsi=data.get('RSI', 50),
                        trend=trend_5m, lots=lots, pnl_pct=pnl_pct,
                        holding_hours=holding_hours, ai_signal=signal,
                        ai_confidence=confidence, bias=current_bias,
                        minutes_to_clearing=minutes_to_clearing,
                        rules=rules_current, action=action,
                        reason=action_reason if action_reason else reason
                    )



            # Пауза 60 секунд после завершения цикла
            print(f"⏳ Итерация {cycle} завершена. Спим 60 секунд...")
            await asyncio.sleep(60)

        except Exception as e:
            print(f"❌ Critical error in main_loop: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main_loop())
