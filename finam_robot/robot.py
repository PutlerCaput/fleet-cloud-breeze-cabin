"""Торговый робот: маркет-дата -> ядро Numba -> намерение -> поток исполнения.

Горячий путь (один поток, без await, без аллокаций массивов):
    сообщение API -> book.* пишет top-N в слот SnapshotRing (memoryview)
                  -> kernels.on_tick(...) читает слот по индексу
                  -> редкий случай: OrderIntent -> ExecutionEngine.submit()
Всё медленное (баланс, ГО, Telegram, диск, ML) — фоновые задачи, которые
лишь обновляют атрибуты/«гейты», читаемые горячим путём.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from datetime import datetime

import numpy as np

from . import kernels as K
from .book import LevelsAssembler, RowBook
from .config import TZ_MOSCOW, Settings, risk_to_array
from .execution import ExecReport, ExecutionEngine, OrderIntent
from .ml import MarketMlOptimizer, load_weights, save_session, save_weights
from .pipeline import ConflatingConsumer, SnapshotRing
from .bar_strategy import BarStrategyRunner, FinamBarFeed, load_strategy
from .recorder import BookRecorder
from .sources import LEVELS, ROWS_SNAPSHOT

logger = logging.getLogger("ContestRobot")

REC_CAPACITY = 1 << 19  # ~14 ч при rec_interval=0.25 с, 21 МБ


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


class InstitutionalContestRobot:
    def __init__(self, settings: Settings, *, account_factory, source_factory, notifier,
                 md_gateway=None, gc_monitor=None, bar_feed_factory=None) -> None:
        self.settings = settings
        self.symbol = settings.symbol
        self.account_factory = account_factory   # async () -> подключённый шлюз счёта
        self.source_factory = source_factory     # () -> источник с .messages()
        self.md_gateway = md_gateway             # справочник/часы/REST-стакан (может быть None)
        self.notifier = notifier
        self.gc_monitor = gc_monitor

        s = settings
        self.ring = SnapshotRing(s.ring_capacity, s.book_depth)
        self.levels_asm = LevelsAssembler()
        self.row_book = RowBook()
        self.P = risk_to_array(s.risk)
        self.S = K.new_state()
        self.out = K.new_out()
        self.rec = np.zeros((REC_CAPACITY, K.REC_COLS), dtype=np.float64)
        self.feature_dir = os.path.join(s.data_dir, "features")
        self.ml_weights_path = os.path.join(s.data_dir, "ml_weights.json")
        self.ml_weights = load_weights(self.ml_weights_path)
        self.W = K.weights_array(self.ml_weights)
        self._inline = s.inline_consumer
        self._pending_timeout = max(s.pending_timeout, s.exit_retries * (s.order_timeout + 4.0))

        # Режим bars: сделки открывает стратегия на минутных свечах, ядро на
        # стакане только держит аварийный стоп (без трейлинга и без входов).
        self.bar_mode = s.strategy == "bars"
        self.bar_feed_factory = bar_feed_factory
        self.bar_runner: BarStrategyRunner | None = None
        self._bar_sig = 0
        self._bar_since = 0.0
        self._bar_blocked = False
        if self.bar_mode:
            self.P[K.P_HARD_MIN] = self.P[K.P_HARD_MAX] = s.bar_disaster_ticks
            self.P[K.P_ACT_MULT] = 1e9
            self.P[K.P_EXIT_ON_REVERSAL] = 0.0

        self.is_active = False
        self.position = 0
        self.entry_price = 0.0
        self.start_day_equity = 0.0
        self.equity = 0.0
        self.daily_target_achieved = False
        self.lots_long = 0
        self.lots_short = 0

        self._gates = {"session": False, "infra": True, "time": True, "loss": True,
                       "target": True, "md": False, "exec": False}
        self._allow_entry = 0
        self._tasks: list[asyncio.Task] = []
        self._exec: ExecutionEngine | None = None
        self._account = None
        self._consumer: ConflatingConsumer | None = None
        self.book_recorder: BookRecorder | None = None
        self._flat_event: asyncio.Event | None = None
        self._pending_since = 0.0
        self._signal_p = 0.5
        self._last_md = 0.0

        self.ticks = 0
        self.trades = 0
        self.exec_ms: deque[float] = deque(maxlen=1000)
        self.queue_ms: deque[float] = deque(maxlen=1000)

    # ------------------------------------------------------------------ гейты
    def set_gate(self, name: str, value: bool) -> None:
        if self._gates.get(name) == value:
            return
        self._gates[name] = value
        allow = int(all(self._gates.values()))
        if allow != self._allow_entry:
            self._allow_entry = allow
            closed = [k for k, v in self._gates.items() if not v]
            logger.info("[GATES] входы %s %s", "разрешены" if allow else "запрещены", closed or "")

    @property
    def entries_allowed(self) -> bool:
        return bool(self._allow_entry)

    def quote(self) -> tuple[float, float]:
        return float(self.S[K.S_BID]), float(self.S[K.S_ASK])

    async def api_healthy(self) -> bool:
        gw = self.md_gateway or self._account
        if gw is None:
            return True
        try:
            await asyncio.wait_for(gw.server_time(), 3.0)
            return True
        except Exception:
            return False

    def exec_alive(self) -> bool:
        return not self.is_active or (self._exec is not None and self._exec.alive())

    # ------------------------------------------------------------ сессия
    async def start_trading_session(self) -> None:
        if self.is_active:
            return
        s = self.settings
        logger.info("Запуск торговой сессии: кольцо %d x %d уровней, inline=%s, exec_threaded=%s",
                    s.ring_capacity, s.book_depth, self._inline, s.exec_threaded)
        loop = asyncio.get_running_loop()
        self._flat_event = asyncio.Event()
        self.S[:] = 0.0
        self.row_book.reset()
        self.daily_target_achieved = False
        self.ticks = self.trades = 0
        if self.gc_monitor:
            self.gc_monitor.reset()
        for g in ("infra", "time", "loss", "target"):
            self.set_gate(g, True)
        if s.record_books:
            self.book_recorder = BookRecorder(os.path.join(s.data_dir, "books"), s.book_depth)
        try:
            self._account = await self.account_factory()
            if self.md_gateway is not None and os.getenv("RISK_TICK") is None:
                asset = await self.md_gateway.get_asset_info(self.symbol)
                self.P[K.P_TICK] = asset.tick
            self.start_day_equity = self.equity = await self._account.get_equity()
            await self._refresh_sizing()
            self._exec = ExecutionEngine(
                self.account_factory, self.symbol, self._on_exec_report, loop,
                threaded=s.exec_threaded, max_orders_per_sec=s.max_orders_per_sec,
                order_timeout=s.order_timeout, exit_retries=s.exit_retries, quote_fn=self.quote,
                tick=float(self.P[K.P_TICK]), slip_ticks=float(self.P[K.P_EXIT_SLIP]),
            )
            await self._exec.start()
        except Exception as e:
            logger.critical("Ошибка старта сессии: %r", e)
            self.notifier.notify(f"⛔ Сессия не запущена: {e!r}")
            await self._close_account()
            return

        self.is_active = True
        self.set_gate("exec", True)
        self.set_gate("session", True)
        self._tasks = [asyncio.create_task(self._producer(), name="md-producer"),
                       asyncio.create_task(self._housekeeping(), name="housekeeping"),
                       asyncio.create_task(self._sizing_loop(), name="sizing")]
        if not self._inline:
            self._consumer = ConflatingConsumer(self.ring, self._process)
            self._tasks.append(asyncio.create_task(self._consumer.run(), name="md-consumer"))
        if self.bar_mode:
            fn, params = load_strategy(s.bar_strategy)
            feed = self.bar_feed_factory() if self.bar_feed_factory else FinamBarFeed(self.md_gateway, self.symbol)
            self.bar_runner = BarStrategyRunner(feed, fn, params, self._on_bar_signal,
                                                history_days=s.bar_history_days)
            self._bar_sig, self._bar_blocked = 0, False
            self._tasks.append(asyncio.create_task(self._bar_loop(), name="bar-strategy"))
        self.notifier.notify(
            f"🚀 Робот в игре ({self.symbol}, {'свечи: ' + s.bar_strategy if self.bar_mode else 'стакан'}). "
            f"Эквити: {self.start_day_equity:.2f} руб. "
            f"Тик: {self.P[K.P_TICK]:g}. Лотов: {self.lots_long}/{self.lots_short}."
        )

    async def stop_trading_session(self) -> None:
        if not self.is_active:
            return
        self.set_gate("session", False)
        flat = await self.flatten("session_end")
        self.is_active = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.set_gate("exec", False)
        if self._exec is not None:
            await self._exec.stop()

        profit = 0.0
        try:
            self.equity = await self._account.get_equity()
            if self.start_day_equity:
                profit = (self.equity - self.start_day_equity) / self.start_day_equity * 100
        except Exception as e:
            logger.error("[ERR BAL] %r", e)
        path = None
        try:
            path = await asyncio.to_thread(save_session, self.rec, int(self.S[K.S_REC_N]),
                                           self.feature_dir, datetime.now(TZ_MOSCOW))
        except Exception as e:
            logger.error("[ML] признаки сессии не сохранены: %r", e)
        if self.book_recorder is not None:
            try:
                await self.book_recorder.close()
            except Exception as e:
                logger.error("[REC] стакан не сохранён: %r", e)
            self.book_recorder = None
        await self._close_account()

        lines = [
            f"🏁 Сессия закрыта{'' if flat else ' — ПОЗИЦИЯ НЕ ЗАКРЫТА, проверьте терминал'}.",
            f"Итог дня: {profit:+.2f}%. Эквити: {self.equity:.2f} руб.",
            f"Сделок: {self.trades}. Обновлений стакана: {self.ticks}.",
        ]
        if self._consumer is not None:
            lines.append(f"Свёрнуто устаревших снимков: {self._consumer.conflated}.")
        if self.exec_ms:
            lines.append(f"Исполнение p50/p95: {self._pct(self.exec_ms, 50):.1f}/{self._pct(self.exec_ms, 95):.1f} мс, "
                         f"очередь p95: {self._pct(self.queue_ms, 95):.2f} мс.")
        if self.gc_monitor:
            lines.append(self.gc_monitor.summary())
        if path:
            lines.append(f"Признаки для ML: {os.path.basename(path)}")
        self.notifier.notify("\n".join(lines))

    async def _close_account(self) -> None:
        if self._account is not None:
            with contextlib.suppress(Exception):
                await self._account.close()
            self._account = None

    @staticmethod
    def _pct(values, q: float) -> float:
        data = sorted(values)
        if not data:
            return 0.0
        return data[min(len(data) - 1, int(len(data) * q / 100))]

    async def flatten(self, reason: str, timeout: float = 30.0) -> bool:
        """Закрыть позицию с подтверждением исполнителя."""
        if self._exec is None:
            return True
        if self.position == 0 and self.S[K.S_PENDING] == 0.0:
            return True
        self._flat_event.clear()
        bid, ask = self.quote()
        slip = self.P[K.P_EXIT_SLIP] * self.P[K.P_TICK]
        if self.position > 0:
            limit, ref = (bid - slip if bid > 0 else 0.0), bid
        else:
            limit, ref = (ask + slip if ask > 0 else 0.0), ask
        self.S[K.S_PENDING] = 1.0
        self._pending_since = time.monotonic()
        self._exec.submit(OrderIntent(0, limit, ref, reason, True, time.perf_counter_ns()))
        try:
            await asyncio.wait_for(self._flat_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            logger.critical("[FLATTEN] позиция %d не закрыта за %.0f с", self.position, timeout)
            self.notifier.notify(f"🚨 Не удалось закрыть позицию ({self.position} лот.) за {timeout:.0f} с!")
            return False

    # -------------------------------------------------------- горячий путь
    async def _producer(self) -> None:
        backoff = 0.5
        while self.is_active:
            self.row_book.reset()
            source = self.source_factory()
            try:
                async with contextlib.aclosing(source.messages()) as stream:
                    async for kind, payload in stream:
                        self._on_md(kind, payload)
                        backoff = 0.5
                        if self.row_book.crossed_streak > 50:
                            logger.warning("[MD] книга пересечена 50 раз подряд — переподписка")
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[PRODUCER ERR] %r — переподключение через %.1f с", e, backoff)
            self.set_gate("md", False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10.0)

    def _on_md(self, kind: int, payload) -> None:
        ring = self.ring
        idx = ring.seq & ring.mask
        base = idx * ring.stride
        if kind == LEVELS:
            asm = self.levels_asm
            asm.write(payload, ring.mv, base, ring.depth)
        else:
            asm = self.row_book
            asm.apply(payload, kind == ROWS_SNAPSHOT)
            asm.write(ring.mv, base, ring.depth)
        ring.publish(idx, asm.nb, asm.na, time.time())
        self._last_md = time.monotonic()
        if self.book_recorder is not None:
            self.book_recorder.push(ring, idx)
        if self._inline:
            self._process(idx)
        else:
            self._consumer.doorbell.ring()

    def _process(self, idx: int) -> None:
        ring = self.ring
        act = K.on_tick(ring.books, ring.nbs, ring.nas, ring.tss, idx,
                        self.P, self.S, self.W, 0 if self.bar_mode else self._allow_entry, self.out, self.rec)
        self.ticks += 1
        if act:
            self._dispatch(act)

    def _dispatch(self, act: int) -> None:
        S = self.S
        reason = K.REASON_NAMES.get(int(self.out[K.O_REASON]), "?")
        limit = float(self.out[K.O_PRICE])
        if act == K.ACT_EXIT:
            target, urgent = 0, True
            ref = float(S[K.S_BID] if S[K.S_SIDE] > 0 else S[K.S_ASK])
        elif act == K.ACT_BUY:
            target, urgent, ref = self.lots_long, False, float(S[K.S_ASK])
        else:
            target, urgent, ref = -self.lots_short, False, float(S[K.S_BID])
        if target == 0 and act != K.ACT_EXIT:
            K.on_reject(S)  # нет ГО под лот — сигнал не исполняем
            return
        self._pending_since = time.monotonic()
        self._signal_p = float(S[K.S_PROB])
        self._exec.submit(OrderIntent(target, limit, ref, reason, urgent, time.perf_counter_ns()))

    # ------------------------------------------------- отчёты исполнителя
    def _on_exec_report(self, rep: ExecReport) -> None:
        S, P = self.S, self.P
        self.position = rep.position
        new_side = _sign(rep.position)
        kernel_side = int(S[K.S_SIDE])
        now = time.time()

        if rep.kind == "price_fix":
            K.on_price_fix(S, float(rep.avg_price))
            self.entry_price = rep.avg_price
            return

        if rep.kind == "sync":
            if new_side != kernel_side:
                price = float(rep.avg_price) or float(S[K.S_MID])
                K.on_fill(P, S, new_side, price, now)
                self.entry_price = price
                if new_side:
                    self.notifier.notify(f"♻️ Принята позиция с биржи: {rep.position} лот. по {price:g}. "
                                         f"Жёсткий стоп выставлен.")
            self._check_flat()
            return

        if rep.orders:
            self.exec_ms.append(rep.total_ms)
            self.queue_ms.append(rep.queue_ms)
        if rep.error:
            logger.error("[EXEC] %s: %s", rep.reason, rep.error)

        if new_side == 0 and kernel_side != 0:
            ticks = kernel_side * (rep.fill_price - float(S[K.S_ENTRY])) / float(P[K.P_TICK])
            K.on_fill(P, S, 0, float(rep.fill_price), now)
            if self.bar_mode and not rep.reason.startswith("bars:") and rep.reason not in ("session_end", "daily_loss"):
                self._bar_blocked = True  # аварийный стоп: повторно не входим до смены сигнала
            self.trades += 1
            self.notifier.notify(f"⏹ Выход ({rep.reason}) по {rep.fill_price:g}: {ticks:+.0f} тик., "
                                 f"{rep.total_ms:.0f} мс")
        elif new_side != 0 and new_side != kernel_side:
            K.on_fill(P, S, new_side, float(rep.avg_price), now)
            self.entry_price = rep.avg_price
            self.notifier.notify(
                f"{'🟢 LONG' if new_side > 0 else '🔴 SHORT'} {abs(rep.position)} лот. по {rep.avg_price:g} "
                f"(P={self._signal_p:.2f}, трейл {S[K.S_DIST]:.0f} тик., стоп {S[K.S_STOP]:g}), {rep.total_ms:.0f} мс"
            )
        else:
            K.on_reject(S)  # IOC не исполнился / частичный выход — ядро решит на следующем тике
        self._check_flat()

    # -------------------------------------------- стратегия на минутных свечах
    async def _bar_loop(self) -> None:
        await self.bar_runner.warmup()
        self._bar_sig = self.bar_runner.state.target
        self._bar_since = 0.0  # сигнал до старта робота не догоняем
        if _sign(self.position) != 0 and _sign(self.position) != self._bar_sig:
            self._bar_reconcile()  # перезапуск с позицией, которой стратегия уже не хочет
        await self.bar_runner.run_loop()

    def _on_bar_signal(self, sig: int, changed: bool, bar_begin: str) -> None:
        if changed:
            self._bar_sig, self._bar_since, self._bar_blocked = sig, time.monotonic(), False
            logger.info("[BARS] %s сигнал %+d", bar_begin, sig)
        self._bar_reconcile()

    def _bar_reconcile(self) -> None:
        """Подогнать позицию под сигнал стратегии, не опережая заявку в пути."""
        if self._exec is None or not self.is_active or self.S[K.S_PENDING] != 0.0:
            return
        sig, cur = self._bar_sig, _sign(self.position)
        if sig == cur:
            return
        bid, ask = self.quote()
        tick = float(self.P[K.P_TICK])
        exit_slip = float(self.P[K.P_EXIT_SLIP]) * tick
        entry_slip = float(self.P[K.P_ENTRY_SLIP]) * tick
        can_enter = (sig != 0 and self.entries_allowed and not self._bar_blocked
                     and time.monotonic() - self._bar_since <= self.settings.bar_entry_window_s)
        if cur != 0 and not can_enter:
            target, urgent = 0, True
            limit, ref = ((bid - exit_slip, bid) if cur > 0 else (ask + exit_slip, ask))
        elif can_enter:
            lots = self.lots_long if sig > 0 else self.lots_short
            if lots <= 0:
                return
            target, urgent = sig * lots, cur != 0
            limit, ref = ((ask + entry_slip, ask) if sig > 0 else (bid - entry_slip, bid))
        else:
            return
        if not (bid > 0 and ask > bid):
            limit = 0.0  # котировок ещё нет — рыночная заявка
        self.S[K.S_PENDING] = 1.0
        self._pending_since = time.monotonic()
        self._exec.submit(OrderIntent(target, limit, ref, f"bars:{'flat' if target == 0 else 'signal'}",
                                      urgent, time.perf_counter_ns()))

    def _check_flat(self) -> None:
        if self._flat_event is None:
            return
        if self.position == 0 and self.S[K.S_PENDING] == 0.0:
            self._flat_event.set()
        else:
            self._flat_event.clear()

    # ------------------------------------------------------ фоновые задачи
    def _in_no_entry_window(self, now) -> bool:
        return any(a <= now < b for a, b in self.settings.no_entry_windows)

    async def _housekeeping(self) -> None:
        while self.is_active:
            self.set_gate("time", not self._in_no_entry_window(datetime.now(TZ_MOSCOW).time()))
            self.set_gate("md", time.monotonic() - self._last_md < self.settings.md_stale_after)
            if self.S[K.S_PENDING] != 0.0 and time.monotonic() - self._pending_since > self._pending_timeout:
                logger.critical("[PENDING] заявка без ответа %.0f с — сверка с биржей", self._pending_timeout)
                self._exec.request_sync()
                K.on_reject(self.S)
                self._pending_since = time.monotonic()
            await asyncio.sleep(1.0)

    async def _refresh_sizing(self) -> None:
        s = self.settings
        m_long, m_short = await self._account.get_margin_per_lot(self.symbol)
        budget = self.equity * s.margin_utilization
        self.lots_long = min(s.max_lots, int(budget // m_long)) if m_long > 0 else 0
        self.lots_short = min(s.max_lots, int(budget // m_short)) if m_short > 0 else 0

    async def _sizing_loop(self) -> None:
        """Эквити, ГО и дневные лимиты — раз в 15 с, вне горячего пути.

        Исходник делал 2 REST-запроса (портфель + инструмент) ПЕРЕД каждой
        заявкой, т.е. +100..400 мс к каждому входу. Прибыль считалась по
        свободным деньгам, которые падают на величину ГО при открытой позиции.
        """
        s = self.settings
        while self.is_active:
            await asyncio.sleep(15.0)
            try:
                self.equity = await self._account.get_equity()
                if self.position == 0:
                    await self._refresh_sizing()
            except Exception as e:
                logger.error("[MARGIN ERR] %r", e)
                continue
            if not self.start_day_equity:
                continue
            pnl = (self.equity - self.start_day_equity) / self.start_day_equity * 100
            if pnl >= s.daily_target_pct and not self.daily_target_achieved:
                self.daily_target_achieved = True
                self.notifier.notify(f"🏆 ЦЕЛЬ {s.daily_target_pct:g}% ДОСТИГНУТА! +{pnl:.2f}%, "
                                     f"эквити {self.equity:.2f} руб.")
                if s.stop_after_target:
                    self.set_gate("target", False)
            if pnl <= -s.max_daily_loss_pct and self._gates["loss"]:
                self.set_gate("loss", False)
                self.notifier.notify(f"🛑 Дневной лимит убытка {pnl:.2f}%. Входы остановлены до завтра.")
                await self.flatten("daily_loss")

    # ------------------------------------------------------------- ML
    async def weekly_learning_job(self) -> None:
        r = self.settings.risk
        optimizer = MarketMlOptimizer(self.feature_dir, rec_interval=r.rec_interval,
                                      p_buy=r.p_buy, p_sell=r.p_sell)
        result = await asyncio.to_thread(optimizer.train_and_optimize)
        if result.get("status") == "APPROVED":
            self.ml_weights = result
            self.W[:] = K.weights_array(result)  # in-place: ядро подхватит на следующем тике
            await asyncio.to_thread(save_weights, self.ml_weights_path, result)
            self.notifier.notify(f"🧠 ML обновлён: hit-rate {result['hit_rate']:.3f}, "
                                 f"покрытие {result['coverage']:.3f}, выборка {result['samples']}.")
        else:
            self.notifier.notify(f"🧠 ML: веса не изменены ({result.get('reason') or result.get('status')}).")
