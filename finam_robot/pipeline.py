"""Кольцевой буфер снимков стакана и событийный «звонок» вместо asyncio.Queue.

Почему не Queue(maxsize=50):
  * для стакана важен только последний снимок; очередь из 50 штук означает, что
    в пиковый момент потребитель считает сигнал по данным 50 апдейтов назад;
  * каждый put/get — это аллокации (deque-узлы, future, wakeup), а drop через
    get_nowait() выбрасывает самое ценное — порядок, а не устаревшие данные;
  * для дельта-стакана (Finam gRPC шлёт ADD/UPDATE/REMOVE) выброшенное
    сообщение необратимо ломает локальную книгу.

Здесь: продюсер применяет КАЖДОЕ сообщение к локальной книге, пишет top-N
прямо в слот кольца (memoryview поверх numpy, без новых объектов-массивов),
увеличивает seq и «звонит». N звонков до пробуждения потребителя = одно
пробуждение (conflation), и потребитель всегда берёт самый свежий слот.
Однопоточный event loop гарантирует, что слот не «рвётся»: запись слота и
публикация seq происходят без await между ними.
"""

from __future__ import annotations

import asyncio

import numpy as np


class Doorbell:
    """Однослотовый будильник для одного ожидающего (SPSC внутри одного loop)."""

    __slots__ = ("_waiter", "_rung")

    def __init__(self) -> None:
        self._waiter: asyncio.Future | None = None
        self._rung = False

    def ring(self) -> None:
        self._rung = True
        w = self._waiter
        if w is not None and not w.done():
            w.set_result(None)

    async def wait(self) -> None:
        if not self._rung:
            self._waiter = asyncio.get_running_loop().create_future()
            try:
                await self._waiter
            finally:
                self._waiter = None
        self._rung = False


class SnapshotRing:
    """Преаллоцированное кольцо снимков: books[slot, 4, depth], nbs/nas/tss[slot].

    Пишет один продюсер, читает один потребитель, оба в одном потоке. Ёмкость —
    степень двойки (индекс через маску). История слотов доступна для признаков,
    которым нужны соседние снимки (OFI и т.п.), и для режима «обработать все».
    """

    __slots__ = ("capacity", "mask", "depth", "stride", "books", "nbs", "nas", "tss", "seq", "mv")

    def __init__(self, capacity: int = 256, depth: int = 10) -> None:
        if capacity <= 0 or capacity & (capacity - 1):
            raise ValueError("capacity must be a power of two")
        self.capacity = capacity
        self.mask = capacity - 1
        self.depth = depth
        self.stride = 4 * depth
        self.books = np.zeros((capacity, 4, depth), dtype=np.float64)
        self.nbs = np.zeros(capacity, dtype=np.int64)
        self.nas = np.zeros(capacity, dtype=np.int64)
        self.tss = np.zeros(capacity, dtype=np.float64)
        self.seq = 0  # сколько снимков опубликовано всего
        # Плоский memoryview на ту же память: присваивание скаляра через
        # memoryview ~1.6x быстрее, чем ndarray.__setitem__, и ничего не создаёт.
        self.mv = memoryview(self.books.reshape(-1))

    def next_slot(self) -> int:
        return self.seq & self.mask

    def publish(self, idx: int, nb: int, na: int, ts: float) -> None:
        self.nbs[idx] = nb
        self.nas[idx] = na
        self.tss[idx] = ts
        self.seq += 1

    def latest(self) -> int:
        """Индекс последнего опубликованного слота (-1 — пусто)."""
        return (self.seq - 1) & self.mask if self.seq else -1


class ConflatingConsumer:
    """Потребитель, который будят звонком и который обрабатывает только свежий слот.

    `process(idx)` — синхронный колбэк, он не должен делать await: всё
    медленное (заявки, Telegram, диск) уходит через неблокирующие submit/notify.
    """

    def __init__(self, ring: SnapshotRing, process) -> None:
        self.ring = ring
        self.process = process
        self.doorbell = Doorbell()
        self.processed = 0
        self.conflated = 0  # снимков, которые пропущены как устаревшие
        self._last_seq = 0
        self.active = True

    def drain(self) -> None:
        seq = self.ring.seq
        if seq == self._last_seq:
            return
        self.conflated += seq - self._last_seq - 1
        self._last_seq = seq
        self.processed += 1
        self.process((seq - 1) & self.ring.mask)

    async def run(self) -> None:
        while self.active:
            await self.doorbell.wait()
            self.drain()
