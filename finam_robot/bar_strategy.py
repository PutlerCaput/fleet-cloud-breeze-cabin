"""Стратегия на минутных свечах в боевом роботе.

Та же функция `positions(bars, **params)`, что проверялась в
research/harness.py, вызывается после закрытия каждой минутной свечи на
скользящем окне истории; последнее значение — целевая позиция (−1/0/+1),
которая масштабируется в лоты и уходит в ExecutionEngine. Окно истории
должно покрывать самый длинный lookback стратегии (`history_days`), иначе
живые позиции разойдутся с бэктестом — это проверяет tests/test_bar_strategy.py.

Источник свечей — Finam REST /instruments/{symbol}/bars (TIME_FRAME_M1).
Свеча считается закрытой, когда её начало не позже now − 60 с − grace.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import TZ_MOSCOW
from .gateway import dec

logger = logging.getLogger("ContestRobot")

COLUMNS = ["begin", "open", "high", "low", "close", "volume", "secid"]


def prepare_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Сырые свечи (begin в МСК, OHLCV) -> формат research/harness.load_bars."""
    out = df.copy()
    out["dt"] = pd.to_datetime(out["begin"])
    out = out[out["dt"].dt.weekday < 5].sort_values("dt").drop_duplicates("begin", keep="last").reset_index(drop=True)
    out["day"] = out["dt"].dt.strftime("%Y-%m-%d")
    out["tod"] = out["dt"].dt.hour * 60 + out["dt"].dt.minute
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = out[c].astype(np.float64)
    return out


def finam_bars_to_rows(payload: dict, secid: str) -> list[list]:
    """Ответ Finam /bars -> строки [begin(МСК), o, h, l, c, v, secid]."""
    rows = []
    for b in payload.get("bars", []):
        ts = datetime.fromisoformat(str(b["timestamp"]).replace("Z", "+00:00"))
        begin = ts.astimezone(TZ_MOSCOW).strftime("%Y-%m-%d %H:%M:%S")
        rows.append([begin, dec(b.get("open")), dec(b.get("high")), dec(b.get("low")),
                     dec(b.get("close")), dec(b.get("volume")), secid])
    return rows


class FinamBarFeed:
    """Минутные свечи через FinamRestGateway (постоянное HTTP/2-соединение)."""

    def __init__(self, gateway, symbol: str) -> None:
        self.gateway = gateway
        self.symbol = symbol
        self.secid = symbol.split("@")[0]

    async def fetch(self, start: datetime, end: datetime) -> list[list]:
        rows: list[list] = []
        # API отдаёт ограниченное окно за запрос: идём кусками по суткам
        cur = start
        while cur < end:
            nxt = min(end, cur + timedelta(days=1))
            payload = await self.gateway._request("GET", f"/instruments/{self.symbol}/bars", params={
                "timeframe": "TIME_FRAME_M1",
                "interval.start_time": cur.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "interval.end_time": nxt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
            rows.extend(finam_bars_to_rows(payload, self.secid))
            cur = nxt
        return rows


def load_strategy(spec: str):
    """'module:function' или '/path/strategy.py' (функция positions, параметры BEST_PARAMS)."""
    if spec.endswith(".py"):
        loader_spec = importlib.util.spec_from_file_location("bar_strategy_user", spec)
        mod = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(mod)
        return mod.positions, dict(getattr(mod, "BEST_PARAMS", {}))
    mod_name, _, fn_name = spec.partition(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name or "positions"), dict(getattr(mod, "BEST_PARAMS", {}))


@dataclass
class BarRunnerState:
    bars: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=COLUMNS))
    last_begin: str = ""
    target: int = 0
    evaluations: int = 0


class BarStrategyRunner:
    """Каждую минуту: дочитать закрытые свечи, пересчитать positions(), отдать сигнал.

    `on_signal(signal: int, changed: bool, bar_begin: str)` вызывается на каждой
    новой закрытой свече: робот сам сверяет позицию с сигналом (повтор входа,
    если IOC не исполнился; запрет повторного входа после аварийного стопа).
    """

    def __init__(self, feed, positions_fn, params: dict, on_signal, *, history_days: int = 10,
                 grace_s: float = 2.0, clock=None) -> None:
        self.feed = feed
        self.positions_fn = positions_fn
        self.params = params
        self.on_signal = on_signal
        self.history_days = history_days
        self.grace_s = grace_s
        self.clock = clock or (lambda: datetime.now(TZ_MOSCOW))
        self.state = BarRunnerState()

    def _closed_only(self, rows: list[list], now: datetime) -> list[list]:
        limit = (now - timedelta(seconds=60 + self.grace_s)).strftime("%Y-%m-%d %H:%M:%S")
        return [r for r in rows if r[0] <= limit]

    def ingest(self, rows: list[list], now: datetime) -> bool:
        """Добавить закрытые свечи; True — появились новые."""
        rows = [r for r in self._closed_only(rows, now) if r[0] > self.state.last_begin]
        if not rows:
            return False
        add = pd.DataFrame(rows, columns=COLUMNS)
        bars = pd.concat([self.state.bars, add], ignore_index=True) if len(self.state.bars) else add
        cutoff = (now - timedelta(days=self.history_days + 4)).strftime("%Y-%m-%d")
        bars = bars[bars["begin"] >= cutoff].drop_duplicates("begin", keep="last").sort_values("begin")
        self.state.bars = bars.reset_index(drop=True)
        self.state.last_begin = str(self.state.bars["begin"].iloc[-1])
        return True

    def evaluate(self) -> int:
        bars = prepare_bars(self.state.bars)
        if bars.empty:
            return 0
        pos = np.asarray(self.positions_fn(bars, **self.params), dtype=np.float64)
        self.state.evaluations += 1
        sig = int(np.sign(pos[-1])) if len(pos) else 0
        return sig

    def step(self, rows: list[list]) -> int | None:
        """Синхронный шаг: сигнал по последней закрытой свече или None (новых свечей нет)."""
        now = self.clock()
        if not self.ingest(rows, now):
            return None
        sig = self.evaluate()
        changed = sig != self.state.target
        self.state.target = sig
        self.on_signal(sig, changed, self.state.last_begin)
        return sig

    async def warmup(self) -> None:
        now = self.clock()
        rows = await self.feed.fetch(now - timedelta(days=self.history_days + 3), now)
        self.ingest(rows, now)
        if len(self.state.bars):
            # Цель на момент старта только запоминаем: входим по СМЕНЕ сигнала,
            # а не по сигналу, возникшему до запуска робота (цена уже ушла).
            self.state.target = self.evaluate()
            logger.info("[BARS] история %d свечей, последняя %s, цель стратегии %+d",
                        len(self.state.bars), self.state.last_begin, self.state.target)

    async def run(self) -> None:
        await self.warmup()
        await self.run_loop()

    async def run_loop(self) -> None:
        while True:
            now = self.clock()
            nxt = (now.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=self.grace_s))
            await asyncio.sleep(max(0.2, (nxt - now).total_seconds()))
            t0 = time.monotonic()
            try:
                rows = await self.feed.fetch(self.clock() - timedelta(minutes=10), self.clock())
                self.step(rows)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[BARS] шаг стратегии не выполнен: %r", e)
            if time.monotonic() - t0 > 20:
                logger.warning("[BARS] шаг занял %.1f с", time.monotonic() - t0)
