"""Бенчмарк горячего пути: `python -m finam_robot.bench`.

1. Подготовка признаков: исходный путь (np.array из ob.bids/ob.asks на каждом
   тике + calculate_hft_signals) против LevelsAssembler -> кольцо -> on_tick.
2. Очередь под нагрузкой: Queue(50)+drop-oldest с `await send_order` внутри
   потребителя против кольца со звонком и неблокирующего submit.
3. Передача намерения в поток исполнения: тот же loop против отдельного потока.
"""

from __future__ import annotations

import asyncio
import gc
import time
from types import SimpleNamespace as NS

import numpy as np

from . import kernels as K
from .book import LevelsAssembler
from .config import RiskParams, risk_to_array
from .execution import ExecutionEngine, OrderIntent
from .gateway import PaperBroker, PaperGateway
from .pipeline import ConflatingConsumer, SnapshotRing


def pct(a, q):
    return float(np.percentile(np.asarray(a), q))


def make_book(depth=10, decimal=True):
    def px(v):
        return NS(num=int(round(v * 1000)), scale=3) if decimal else v

    return NS(
        bids=[NS(price=px(11.500 - i * 0.001), quantity=100 + i) for i in range(depth)],
        asks=[NS(price=px(11.501 + i * 0.001), quantity=90 + i) for i in range(depth)],
    )


def bench_prep(n=200_000):
    ob = make_book(depth=10)  # в сообщении 10 уровней, как у реального стакана
    W2 = np.array([[2.5, 1.2]])
    dec = lambda d: d.num / (10 ** d.scale)  # noqa: E731

    def original():
        bp = np.array([dec(b.price) for b in ob.bids[:3]], dtype=np.float64)
        bv = np.array([b.quantity for b in ob.bids[:3]], dtype=np.float64)
        ap = np.array([dec(a.price) for a in ob.asks[:3]], dtype=np.float64)
        av = np.array([a.quantity for a in ob.asks[:3]], dtype=np.float64)
        return K.calculate_hft_signals(bp, bv, ap, av, W2, 0.0)

    def make_new(depth):
        ring = SnapshotRing(256, depth)
        asm = LevelsAssembler()
        P = risk_to_array(RiskParams(warmup_n=1e18))  # без входов: меряем чистый тракт
        S, out, W = K.new_state(), K.new_out(), K.weights_array(None)
        rec = np.zeros((1024, K.REC_COLS))
        ts = [time.time()]

        def new():
            idx = ring.seq & ring.mask
            asm.write(ob, ring.mv, idx * ring.stride, ring.depth)
            ts[0] += 0.001
            ring.publish(idx, asm.nb, asm.na, ts[0])
            return K.on_tick(ring.books, ring.nbs, ring.nas, ring.tss, idx, P, S, W, 0, out, rec)

        return new

    print("1) Стакан -> признаки -> сигнал, на один тик. Исходный путь создаёт 4 list + 4 ndarray +")
    print("   кортеж на тик; новый — ни одного массива, и в то же время считает ещё и риск-движок:")
    cases = (("исходный: 4x np.array, top-3", original),
             ("кольцо + on_tick, top-3", make_new(3)),
             ("кольцо + on_tick, top-10", make_new(10)))
    for name, fn in cases:
        for _ in range(2_000):
            fn()
        gc.collect()
        samples = np.empty(n)
        for i in range(n):
            t = time.perf_counter_ns()
            fn()
            samples[i] = time.perf_counter_ns() - t
        print(f"   {name:30s} p50 {pct(samples, 50)/1e3:5.2f} мкс  p99 {pct(samples, 99)/1e3:6.2f} мкс")


async def bench_queue(n_msgs=4_000, rate_hz=4_000, order_every=400, order_ms=60.0):
    """Поток стакана rate_hz; каждый order_every-й снимок даёт сигнал.
    Исходник: `await send_order()` прямо в потребителе (order_ms).
    Новое: кольцо + звонок, заявка уходит через submit (не ждём)."""
    period = 1.0 / rate_hz

    async def producer(publish):
        t_next = time.perf_counter()
        for i in range(n_msgs):
            publish(i, time.perf_counter())
            t_next += period
            while time.perf_counter() < t_next:
                await asyncio.sleep(0)

    # --- исходная схема ---
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    ages_old, dropped = [], [0]

    def put(i, ts):
        if q.full():
            q.get_nowait()
            dropped[0] += 1
        q.put_nowait((i, ts))

    async def old_consumer():
        got = 0
        while got < n_msgs - dropped[0]:
            i, ts = await q.get()
            got += 1
            ages_old.append((time.perf_counter() - ts) * 1e3)
            if i % order_every == 0:
                await asyncio.sleep(order_ms / 1e3)  # await send_order(...)

    cons = asyncio.create_task(old_consumer())
    await producer(put)
    await asyncio.wait_for(cons, 10.0)

    # --- кольцо + звонок ---
    ring = SnapshotRing(256, 1)
    ages_new = []
    pending_orders = []

    def process(idx):
        ages_new.append((time.perf_counter() - ring.tss[idx]) * 1e3)
        if int(ring.nbs[idx]) % order_every == 0:
            pending_orders.append(asyncio.get_running_loop().create_task(asyncio.sleep(order_ms / 1e3)))

    consumer = ConflatingConsumer(ring, process)

    def publish(i, ts):
        idx = ring.seq & ring.mask
        ring.publish(idx, i, 0, ts)  # nbs хранит номер сообщения для теста
        consumer.doorbell.ring()

    task = asyncio.create_task(consumer.run())
    await producer(publish)
    await asyncio.sleep(0.01)
    consumer.active = False
    task.cancel()
    await asyncio.gather(*pending_orders, return_exceptions=True)

    print(f"\n2) Поток {rate_hz} Гц, каждый {order_every}-й снимок — заявка {order_ms:.0f} мс:")
    for name, ages, extra in (
        ("Queue(50)+await order", ages_old, f"выброшено {dropped[0]}"),
        ("кольцо+звонок+submit", ages_new, f"свёрнуто {consumer.conflated}"),
    ):
        stale = sum(a > 5.0 for a in ages)
        print(f"   {name:22s} возраст снимка p50 {pct(ages, 50):6.2f} мс  p99 {pct(ages, 99):6.2f} мс  "
              f"макс {max(ages):6.2f} мс  решений по данным >5 мс: {stale:5d}  ({extra})")


async def bench_handoff(n=200):
    """Решение ядра -> заявка ушла. Нагрузка — имитация потока стакана в том же
    loop: ~5 мкс Python-работы на сообщение, yield раз в 64 сообщения."""
    print("\n3) Передача намерения исполнителю (решение ядра -> заявка ушла):")
    for load in (False, True):
        for threaded in (False, True):
            broker = PaperBroker(latency=0.0, quote_fn=lambda: (11.5, 11.501))
            gw = PaperGateway(broker)
            reports = []

            async def factory(gw=gw):
                return gw

            eng = ExecutionEngine(factory, "X", reports.append, asyncio.get_running_loop(),
                                  threaded=threaded, max_orders_per_sec=1e6)
            await eng.start()
            stop = [False]

            async def busy(stop=stop):
                x = 0
                while not stop[0]:
                    for _ in range(64 * 40):
                        x += 1
                    await asyncio.sleep(0)

            bg = asyncio.create_task(busy()) if load else None
            for i in range(n):
                eng.submit(OrderIntent(1 if i % 2 == 0 else 0, 0.0, 0.0, "b", False, time.perf_counter_ns()))
                await asyncio.sleep(0.003)
            stop[0] = True
            if bg:
                await bg
            await asyncio.sleep(0.05)
            await eng.stop()
            q = [r.queue_ms * 1e3 for r in reports if r.kind == "done" and r.orders]
            label = f"{'отдельный поток' if threaded else 'тот же loop'}, {'под нагрузкой' if load else 'без нагрузки'}"
            print(f"   {label:32s} p50 {pct(q, 50):8.1f} мкс  p99 {pct(q, 99):9.1f} мкс")


def main():
    K.warmup()
    bench_prep()
    asyncio.run(bench_queue())
    asyncio.run(bench_handoff())


if __name__ == "__main__":
    main()
