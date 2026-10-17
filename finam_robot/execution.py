"""Быстрый путь исполнения: потребитель стакана никогда не ждёт заявку.

Стратегия кладёт намерение «целевая позиция = N лотов» через `submit()`
(call_soon_threadsafe, единицы мкс) и сразу идёт к следующему снимку.
Исполнитель — отдельная задача со своим HTTP/2-клиентом:

  * сливает намерения (latest-wins): пока летит одна заявка, новые цели не
    порождают шторм ордеров — после ответа досылается только разница до
    последней цели;
  * держит token-bucket лимит ордеров/с (лимиты брокера и сборы биржи за
    транзакционную активность);
  * выход (urgent) повторяет до плоской позиции с переоценкой цены по свежей
    котировке, а после таймаута/обрыва СНАЧАЛА сверяет позицию с биржей —
    иначе заявка «с неизвестным статусом» превращается в двойное закрытие;
  * отчитывается в loop стратегии через call_soon_threadsafe.

threaded=False (по умолчанию) — задача в том же event loop. threaded=True —
отдельный поток со своим loop. Замер (`python -m finam_robot.bench`): без
нагрузки поток добавляет ~0.1 мс на пробуждение, а под потоком стакана
поток исполнения ждёт GIL — p50 единицы мс, p99 десятки–сотни мс против
~0.2 / 0.4 мс в том же loop. В CPython с GIL отдельный поток для заявок вреден; изоляция,
если она понадобится, — это отдельный ПРОЦЕСС, не поток. Режим оставлен для
free-threaded сборок Python.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .gateway import BUY, SELL, new_client_order_id
from .pipeline import Doorbell

logger = logging.getLogger("ContestRobot")


def new_event_loop() -> asyncio.AbstractEventLoop:
    try:
        import uvloop

        return uvloop.new_event_loop()
    except ImportError:
        return asyncio.new_event_loop()


@dataclass(slots=True)
class OrderIntent:
    target: int          # целевая позиция в лотах: +лонг, -шорт, 0 — вне рынка
    limit_price: float   # 0.0 — рыночная
    ref_price: float     # котировка в момент решения (оценка цены сделки)
    reason: str
    urgent: bool         # выход/риск: повторять до исполнения
    created_ns: int      # time.perf_counter_ns() в момент решения ядра


@dataclass(slots=True)
class ExecReport:
    kind: str            # "done" | "sync" | "price_fix"
    position: int
    avg_price: float     # средняя цена текущей позиции
    fill_price: float    # средняя цена сделок этого намерения
    filled: int
    orders: int
    reason: str
    error: str = ""
    queue_ms: float = 0.0    # решение ядра -> первая заявка ушла
    total_ms: float = 0.0    # решение ядра -> конечный статус


class ExecutionEngine:
    def __init__(self, gateway_factory, symbol: str, on_report, report_loop: asyncio.AbstractEventLoop,
                 *, threaded: bool = True, max_orders_per_sec: float = 5.0, order_timeout: float = 2.0,
                 exit_retries: int = 5, quote_fn=None, tick: float = 0.001, slip_ticks: float = 5.0,
                 tif: str = "TIME_IN_FORCE_IOC") -> None:
        self._gateway_factory = gateway_factory
        self.symbol = symbol
        self._on_report = on_report
        self._report_loop = report_loop
        self.threaded = threaded
        self._rate = max(max_orders_per_sec, 0.1)
        self._burst = max(1.0, self._rate)
        self._tokens = self._burst
        self._tokens_at = time.monotonic()
        self.order_timeout = order_timeout
        self.exit_retries = max(1, exit_retries)
        self.quote_fn = quote_fn
        self.tick = tick
        self.slip_ticks = slip_ticks
        self.tif = tif

        self.position = 0
        self.avg_price = 0.0
        self.orders_sent = 0
        self.coalesced = 0

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._ready = threading.Event()
        self._init_error: BaseException | None = None
        self._doorbell: Doorbell | None = None
        self._pending: OrderIntent | None = None
        self._sync_requested = False
        self._stopping = False
        self._gw = None

    # --- управление жизненным циклом (вызывается из loop стратегии) ---
    async def start(self) -> None:
        if self.threaded:
            self._thread = threading.Thread(target=self._thread_main, name="exec-fastpath", daemon=True)
            self._thread.start()
        else:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._main(), name="exec-fastpath")
        ok = await asyncio.to_thread(self._ready.wait, 30.0)
        if self._init_error is not None:
            raise RuntimeError(f"Исполнитель не стартовал: {self._init_error!r}") from self._init_error
        if not ok:
            raise TimeoutError("Исполнитель не стартовал за 30 с")

    async def stop(self) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._request_stop)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 10.0)
        elif self._task is not None:
            await asyncio.wait({self._task}, timeout=10.0)

    def alive(self) -> bool:
        if self._thread is not None:
            return self._thread.is_alive()
        return self._task is not None and not self._task.done()

    # --- API для стратегии: неблокирующие, потокобезопасные ---
    def submit(self, intent: OrderIntent) -> None:
        self._loop.call_soon_threadsafe(self._accept, intent)

    def request_sync(self) -> None:
        self._loop.call_soon_threadsafe(self._accept_sync)

    # --- внутри loop исполнителя ---
    def _thread_main(self) -> None:
        loop = new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.close()

    def _request_stop(self) -> None:
        self._stopping = True
        if self._doorbell is not None:
            self._doorbell.ring()

    def _accept(self, intent: OrderIntent) -> None:
        cur = self._pending
        if cur is not None:
            self.coalesced += 1
            intent.urgent = intent.urgent or cur.urgent
            intent.created_ns = min(intent.created_ns, cur.created_ns)
        self._pending = intent
        self._doorbell.ring()

    def _accept_sync(self) -> None:
        self._sync_requested = True
        self._doorbell.ring()

    def _report(self, rep: ExecReport) -> None:
        self._report_loop.call_soon_threadsafe(self._on_report, rep)

    async def _main(self) -> None:
        self._doorbell = Doorbell()
        try:
            self._gw = await self._gateway_factory()
            await self._sync()
        except BaseException as e:
            self._init_error = e
            self._ready.set()
            return
        self._ready.set()
        try:
            while not self._stopping:
                await self._doorbell.wait()
                if self._sync_requested:
                    self._sync_requested = False
                    try:
                        await self._sync()
                    except Exception as e:  # сбой сверки не должен убивать поток заявок
                        logger.error("[EXEC] сверка позиции не удалась: %r", e)
                intent = self._pending
                self._pending = None
                if intent is not None:
                    await self._execute(intent)
        finally:
            try:
                await self._gw.close()
            except Exception:
                pass

    async def _sync(self) -> None:
        pos, avg = await self._gw.get_position(self.symbol)
        self.position, self.avg_price = pos, avg
        self._report(ExecReport("sync", pos, avg, 0.0, 0, 0, "sync"))

    async def _throttle(self) -> None:
        while True:
            now = time.monotonic()
            self._tokens = min(self._burst, self._tokens + (now - self._tokens_at) * self._rate)
            self._tokens_at = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            await asyncio.sleep((1.0 - self._tokens) / self._rate)

    def _quote_price(self, side: str) -> tuple[float, float]:
        """(лимит, оценка) по свежей котировке для повторной заявки."""
        if self.quote_fn is None:
            return 0.0, 0.0
        bid, ask = self.quote_fn()
        if side == BUY:
            return (ask + self.slip_ticks * self.tick if ask > 0 else 0.0), ask
        return (bid - self.slip_ticks * self.tick if bid > 0 else 0.0), bid

    def _apply_fill(self, signed: int, px: float) -> None:
        pos = self.position
        new = pos + signed
        if new == 0:
            self.avg_price = 0.0
        elif pos == 0 or (pos > 0) != (new > 0):
            self.avg_price = px
        elif abs(new) > abs(pos):
            self.avg_price = (self.avg_price * abs(pos) + px * abs(signed)) / abs(new)
        self.position = new

    async def _execute(self, intent: OrderIntent) -> None:
        queue_ms = 0.0
        attempts = self.exit_retries if intent.urgent else 1
        orders = filled_total = 0
        notional = 0.0
        err = ""
        for attempt in range(attempts):
            delta = intent.target - self.position
            if delta == 0:
                break
            await self._throttle()
            side = BUY if delta > 0 else SELL
            qty = abs(delta)
            if attempt == 0:
                limit, ref = intent.limit_price, intent.ref_price
            else:
                limit, ref = self._quote_price(side)
            if orders == 0:
                queue_ms = (time.perf_counter_ns() - intent.created_ns) / 1e6
            since = datetime.now(timezone.utc)
            prev_pos = self.position
            try:
                res = await asyncio.wait_for(
                    self._gw.execute(self.symbol, side, qty, limit, new_client_order_id(),
                                     self.order_timeout, self.tif),
                    self.order_timeout + 3.0,
                )
            except Exception as e:
                err = repr(e)
                logger.error("[EXEC] %s %s x%d: %s", intent.reason, side, qty, err)
                # Статус заявки неизвестен: она могла исполниться. Сначала сверка.
                try:
                    await self._sync()
                except Exception as e2:
                    logger.error("[EXEC] сверка позиции не удалась: %r", e2)
                if intent.urgent:
                    await asyncio.sleep(min(0.1 * 2**attempt, 2.0))
                continue
            orders += 1
            self.orders_sent += 1
            if res.filled:
                signed = res.filled if side == BUY else -res.filled
                px = res.price or ref or limit
                self._apply_fill(signed, px)
                filled_total += res.filled
                notional += res.filled * px
                if not res.price and res.order_id and prev_pos == 0:
                    asyncio.create_task(self._fix_price(res.order_id, since))
            if res.filled < qty and intent.urgent:
                await asyncio.sleep(min(0.05 * 2**attempt, 1.0))
        self._report(
            ExecReport(
                kind="done",
                position=self.position,
                avg_price=self.avg_price,
                fill_price=notional / filled_total if filled_total else 0.0,
                filled=filled_total,
                orders=orders,
                reason=intent.reason,
                error=err,
                queue_ms=queue_ms,
                total_ms=(time.perf_counter_ns() - intent.created_ns) / 1e6,
            )
        )
        if intent.urgent and self.position != intent.target:
            logger.critical("[EXEC] цель %d не достигнута, позиция %d", intent.target, self.position)

    async def _fix_price(self, order_id: str, since: datetime) -> None:
        await asyncio.sleep(0.3)
        try:
            px = await self._gw.fill_price(order_id, since)
        except Exception as e:
            logger.warning("[EXEC] цена сделки %s не получена: %r", order_id, e)
            return
        if px > 0 and self.position != 0:
            self.avg_price = px
            self._report(ExecReport("price_fix", self.position, px, px, 0, 0, "price_fix"))
