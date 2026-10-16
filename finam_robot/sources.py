"""Источники стакана. Каждый — асинхронный генератор пар (kind, payload).

  LEVELS        — payload с .bids/.asks (снимок уровней)
  ROWS_DELTA    — список строк Finam, применяется поверх текущей книги
  ROWS_SNAPSHOT — список строк Finam, полный снимок (книга сбрасывается)
"""

from __future__ import annotations

import asyncio
import math
import random
import time

LEVELS = 0
ROWS_DELTA = 1
ROWS_SNAPSHOT = 2


class RestPollingBookSource:
    """Запасной вариант: GET /instruments/{symbol}/orderbook с фиксированным шагом.
    Это НЕ HFT (шаг ограничен лимитами API), но работает на одном токене."""

    def __init__(self, gateway, symbol: str, interval: float = 0.5) -> None:
        self.gateway = gateway
        self.symbol = symbol
        self.interval = interval

    async def messages(self):
        while True:
            t = time.monotonic()
            data = await self.gateway.get_order_book(self.symbol)
            yield ROWS_SNAPSHOT, data.get("orderbook", {}).get("rows", [])
            await asyncio.sleep(max(0.0, self.interval - (time.monotonic() - t)))


class GrpcBookSource:
    """Стрим MarketDataService.SubscribeOrderBook (Finam Trade API, gRPC).

    request_cls/response_cls — классы SubscribeOrderBookRequest/Response,
    сгенерированные из proto Finam (grpcio-tools). Канал один на сессию,
    keepalive держит HTTP/2-соединение тёплым между всплесками.
    """

    PATH = "/grpc.tradeapi.v1.marketdata.MarketDataService/SubscribeOrderBook"

    def __init__(self, symbol: str, jwt_provider, request_cls, response_cls,
                 target: str = "api.finam.ru:443", mode: str = "delta") -> None:
        self.symbol = symbol
        self.jwt_provider = jwt_provider
        self.request_cls = request_cls
        self.response_cls = response_cls
        self.target = target
        self.kind = ROWS_SNAPSHOT if mode == "snapshot" else ROWS_DELTA

    async def messages(self):
        import grpc

        options = [
            ("grpc.keepalive_time_ms", 10_000),
            ("grpc.keepalive_timeout_ms", 5_000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        async with grpc.aio.secure_channel(self.target, grpc.ssl_channel_credentials(), options=options) as ch:
            call = ch.unary_stream(
                self.PATH,
                request_serializer=self.request_cls.SerializeToString,
                response_deserializer=self.response_cls.FromString,
            )
            stream = call(self.request_cls(symbol=self.symbol), metadata=(("authorization", self.jwt_provider()),))
            kind = self.kind
            async for resp in stream:
                for ob in resp.order_book:
                    yield kind, ob.rows


class IteratorBookSource:
    """Обёртка над любым асинхронным итератором SDK (например, ваш текущий
    `client.orders_stream.subscribe_order_book(...)`). Формат сообщений
    определяется по первому сообщению."""

    def __init__(self, factory, mode: str = "delta") -> None:
        self.factory = factory
        self.rows_kind = ROWS_SNAPSHOT if mode == "snapshot" else ROWS_DELTA

    async def messages(self):
        shape = None
        async for msg in self.factory():
            if shape is None:
                shape = "levels" if hasattr(msg, "bids") else "multi" if hasattr(msg, "order_book") else "rows"
            if shape == "levels":
                yield LEVELS, msg
            elif shape == "multi":
                for ob in msg.order_book:
                    yield self.rows_kind, ob.rows
            else:
                yield self.rows_kind, msg.rows


class _Level:
    __slots__ = ("price", "quantity")

    def __init__(self, price: float, quantity: float) -> None:
        self.price = price
        self.quantity = quantity


class _Book:
    __slots__ = ("bids", "asks")

    def __init__(self, depth: int) -> None:
        self.bids = [_Level(0.0, 0.0) for _ in range(depth)]
        self.asks = [_Level(0.0, 0.0) for _ in range(depth)]


class SyntheticBookSource:
    """Синтетический стакан для DRY-RUN и тестов без сети.

    Скрытое «давление» p (AR(1)) одновременно перекашивает объёмы и сдвигает
    цену — у дисбаланса есть реальная предсказательная сила, поэтому на этих
    данных можно проверить и сигнал, и ML-переобучение. Есть пачки (burst без
    sleep) и шоки спреда.
    """

    def __init__(self, tick: float = 0.001, start: float = 11.5, rate_hz: float = 200.0,
                 depth: int = 10, seed: int = 7, burst_prob: float = 0.002,
                 shock_prob: float = 0.001, max_messages: int | None = None) -> None:
        self.tick = tick
        self.start = start
        self.rate_hz = rate_hz
        self.depth = depth
        self.seed = seed
        self.burst_prob = burst_prob
        self.shock_prob = shock_prob
        self.max_messages = max_messages

    async def messages(self):
        rng = random.Random(self.seed)
        tick = self.tick
        depth = self.depth
        mid_t = round(self.start / tick)
        pressure = 0.0
        shock_left = 0
        burst_left = 0
        book = _Book(depth)  # один объект, как у SDK с переиспользуемыми сообщениями
        sent = 0
        while self.max_messages is None or sent < self.max_messages:
            pressure = 0.97 * pressure + rng.gauss(0.0, 0.25)
            if rng.random() < 0.03 * min(1.0, abs(pressure)):
                mid_t += 1 if pressure > 0 else -1
            if shock_left == 0 and rng.random() < self.shock_prob:
                shock_left = rng.randint(20, 80)
            spread_t = 1 + (rng.randint(4, 8) if shock_left else 0)
            shock_left = max(0, shock_left - 1)
            bid_t = mid_t
            ask_t = mid_t + spread_t
            skew = math.tanh(pressure)
            for i in range(depth):
                b = book.bids[i]
                a = book.asks[i]
                b.price = (bid_t - i) * tick
                a.price = (ask_t + i) * tick
                base = 50.0 + rng.random() * 30.0
                b.quantity = float(max(1, int(base * (1.0 + 0.8 * skew))))
                a.quantity = float(max(1, int(base * (1.0 - 0.8 * skew))))
            yield LEVELS, book
            sent += 1
            if burst_left == 0 and rng.random() < self.burst_prob:
                burst_left = rng.randint(50, 300)
            if burst_left:
                burst_left -= 1
                if burst_left % 64 == 0:
                    await asyncio.sleep(0)
            else:
                await asyncio.sleep(1.0 / self.rate_hz)
