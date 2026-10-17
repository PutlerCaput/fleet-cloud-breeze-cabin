"""Шлюзы к брокеру: боевой Finam REST (постоянное HTTP/2-соединение) и бумажный.

Почему не `finam_trade_api.Client` в горячем пути: SDK (4.x) на КАЖДЫЙ запрос
делает `async with httpx.AsyncClient(...)`, то есть новый TCP+TLS+HTTP/2
handshake — десятки–сотни миллисекунд на заявку. Здесь один клиент живёт всю
сессию, соединение прогревается до первой заявки, JWT обновляется в фоне.

Каждый экземпляр шлюза привязан к своему event loop (httpx-клиент нельзя
делить между loop'ами), поэтому поток исполнения создаёт свой экземпляр.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("ContestRobot")

FINAM_REST_URL = "https://api.finam.ru/v1"

TERMINAL_STATUSES = frozenset(
    {
        "ORDER_STATUS_FILLED",
        "ORDER_STATUS_EXECUTED",
        "ORDER_STATUS_CANCELED",
        "ORDER_STATUS_REJECTED",
        "ORDER_STATUS_REJECTED_BY_EXCHANGE",
        "ORDER_STATUS_DENIED_BY_BROKER",
        "ORDER_STATUS_EXPIRED",
        "ORDER_STATUS_FAILED",
        "ORDER_STATUS_DONE_FOR_DAY",
    }
)

BUY = "SIDE_BUY"
SELL = "SIDE_SELL"


def dec(x) -> float:
    """Finam Decimal ({"value": "1.5"} | "1.5" | None) -> float."""
    if x is None:
        return 0.0
    if isinstance(x, dict):
        return float(x.get("value") or 0.0)
    return float(x or 0.0)


def money(x) -> float:
    """google.type.Money ({"units": "1520", "nanos": 500000000}) -> float."""
    if not x:
        return 0.0
    return float(x.get("units") or 0) + float(x.get("nanos") or 0) / 1e9


def new_client_order_id() -> str:
    return uuid.uuid4().hex[:20]  # лимит API — 20 символов


@dataclass(slots=True)
class AssetInfo:
    tick: float
    decimals: int
    lot_size: float


@dataclass(slots=True)
class FillResult:
    order_id: str
    status: str
    filled: int
    price: float  # 0.0 — неизвестна; точная цена — через fill_price()


class FinamAuthError(RuntimeError):
    """API-токен не принят (истёк, отозван, скопирован не полностью)."""


def finam_error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        return f"{r.status_code} {body.get('message') or body}"
    except ValueError:
        return f"{r.status_code} {r.text[:200]}"


class FinamRestGateway:
    """Тонкий клиент Finam Trade API v1 REST поверх одного httpx.AsyncClient."""

    def __init__(self, secret: str, account_id: str, *, base_url: str = FINAM_REST_URL,
                 timeout: float = 3.0, jwt_refresh_every: float = 600.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._secret = secret
        self.account_id = account_id
        self._http = httpx.AsyncClient(
            base_url=base_url,
            http2=transport is None,
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8, keepalive_expiry=600),
        )
        self._jwt_refresh_every = jwt_refresh_every
        self._jwt_at = 0.0
        self._jwt_lock = asyncio.Lock()
        self._refresher: asyncio.Task | None = None
        self.decimals = 3

    # --- соединение и авторизация ---
    async def connect(self) -> None:
        await self._refresh_jwt(force=True)
        await self.server_time()  # прогрев TLS/HTTP2 до первой заявки
        self._refresher = asyncio.create_task(self._jwt_loop(), name="finam-jwt")

    async def close(self) -> None:
        if self._refresher:
            self._refresher.cancel()
        await self._http.aclose()

    async def _refresh_jwt(self, force: bool = False) -> None:
        async with self._jwt_lock:
            if not force and time.monotonic() - self._jwt_at < 5.0:
                return
            r = await self._http.post("/sessions", json={"secret": self._secret})
            if r.status_code != 200:
                raise FinamAuthError(f"Finam не принял API-токен: {finam_error_text(r)}")
            self._http.headers["Authorization"] = r.json()["token"]
            self._jwt_at = time.monotonic()

    async def _jwt_loop(self) -> None:
        while True:
            await asyncio.sleep(self._jwt_refresh_every)
            try:
                await self._refresh_jwt(force=True)
            except Exception as e:
                logger.error("[JWT] обновление не удалось: %s", e)

    @property
    def jwt(self) -> str:
        return self._http.headers.get("Authorization", "")

    async def _request(self, method: str, url: str, **kw) -> dict:
        r = await self._http.request(method, url, **kw)
        if r.status_code == 401:
            await self._refresh_jwt()
            r = await self._http.request(method, url, **kw)
        if r.status_code != 200:
            raise RuntimeError(f"Finam {method} {url}: {finam_error_text(r)}")
        return r.json()

    async def token_details(self) -> dict:
        """Счета, права на маркет-дату и срок действия JWT текущей сессии."""
        r = await self._http.post("/sessions/details", json={"token": self.jwt})
        if r.status_code != 200:
            raise RuntimeError(f"Finam /sessions/details: {finam_error_text(r)}")
        return r.json()

    # --- справочные данные ---
    async def server_time(self) -> str:
        return (await self._request("GET", "/assets/clock")).get("timestamp", "")

    async def get_asset_info(self, symbol: str) -> AssetInfo:
        a = await self._request("GET", f"/assets/{symbol}", params={"account_id": self.account_id})
        decimals = int(a.get("decimals", 0))
        step = str(a.get("min_step", "1"))
        # min_step приходит в минимальных единицах цены ("1" при decimals=3 -> 0.001);
        # если вдруг уже дробное значение — берём как есть.
        tick = float(step) if "." in step else int(step) / 10**decimals
        self.decimals = decimals
        return AssetInfo(tick=tick, decimals=decimals, lot_size=dec(a.get("lot_size")))

    async def get_order_book(self, symbol: str) -> dict:
        return await self._request("GET", f"/instruments/{symbol}/orderbook")

    # --- счёт ---
    async def get_account(self) -> dict:
        return await self._request("GET", f"/accounts/{self.account_id}")

    async def get_equity(self) -> float:
        return dec((await self.get_account()).get("equity"))

    async def get_position(self, symbol: str) -> tuple[int, float]:
        for p in (await self.get_account()).get("positions", []):
            if p.get("symbol") == symbol:
                return int(round(dec(p.get("quantity")))), dec(p.get("average_price"))
        return 0, 0.0

    async def get_asset_params(self, symbol: str) -> dict:
        return await self._request("GET", f"/assets/{symbol}/params", params={"account_id": self.account_id})

    async def get_margin_per_lot(self, symbol: str) -> tuple[float, float]:
        """ГО на лот (лонг, шорт) из /assets/{symbol}/params."""
        p = await self.get_asset_params(symbol)
        return money(p.get("long_collateral")), money(p.get("short_collateral"))

    # --- заявки ---
    async def place_order(self, symbol: str, side: str, qty: int, limit_price: float,
                          client_order_id: str, tif: str = "TIME_IN_FORCE_IOC") -> dict:
        body = {
            "account_id": self.account_id,
            "symbol": symbol,
            "quantity": {"value": str(qty)},
            "side": side,
            "client_order_id": client_order_id,
        }
        if limit_price > 0.0:
            body["type"] = "ORDER_TYPE_LIMIT"
            body["time_in_force"] = tif
            body["limit_price"] = {"value": f"{limit_price:.{self.decimals}f}"}
        else:
            body["type"] = "ORDER_TYPE_MARKET"
        return await self._request("POST", f"/accounts/{self.account_id}/orders", json=body)

    async def get_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/accounts/{self.account_id}/orders/{order_id}")

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request("DELETE", f"/accounts/{self.account_id}/orders/{order_id}")

    async def execute(self, symbol: str, side: str, qty: int, limit_price: float,
                      client_order_id: str, timeout: float, tif: str = "TIME_IN_FORCE_IOC") -> FillResult:
        """Выставить и дождаться конечного статуса; остаток по таймауту снимается."""
        state = await self.place_order(symbol, side, qty, limit_price, client_order_id, tif)
        order_id = state.get("order_id", "")
        deadline = time.monotonic() + timeout
        poll = 0.02
        while state.get("status") not in TERMINAL_STATUSES and time.monotonic() < deadline:
            await asyncio.sleep(poll)
            poll = min(poll * 1.5, 0.2)
            state = await self.get_order(order_id)
        if state.get("status") not in TERMINAL_STATUSES:
            try:
                await self.cancel_order(order_id)
            finally:
                state = await self.get_order(order_id)
        return FillResult(
            order_id=order_id,
            status=state.get("status", ""),
            filled=int(round(dec(state.get("executed_quantity")))),
            price=0.0,  # OrderState не содержит цену сделки -> оценка по котировке
        )

    async def fill_price(self, order_id: str, since: datetime) -> float:
        """VWAP сделок заявки (0.0 — не нашли)."""
        now = datetime.now(timezone.utc)
        data = await self._request(
            "GET",
            f"/accounts/{self.account_id}/trades",
            params={
                "interval.start_time": (since - timedelta(seconds=5)).isoformat(),
                "interval.end_time": (now + timedelta(seconds=5)).isoformat(),
                "limit": 100,
            },
        )
        qty = notional = 0.0
        for t in data.get("trades", []):
            if t.get("order_id") == order_id:
                q = dec(t.get("size"))
                qty += q
                notional += q * dec(t.get("price"))
        return notional / qty if qty else 0.0


class PaperBroker:
    """Общее (между потоками) состояние бумажного счёта. Исполнение — по
    текущей котировке из `quote_fn` с IOC-семантикой: лимит не дотягивает до
    противоположной стороны — 0 лотов."""

    def __init__(self, equity: float = 30_000.0, margin_per_lot: float = 1_500.0,
                 point_value: float = 1_000.0, fee_per_lot: float = 1.0, latency: float = 0.02,
                 quote_fn=None) -> None:
        self.cash = equity
        self.margin_per_lot = margin_per_lot
        self.point_value = point_value  # руб. за 1.0 цены на лот (CR: 1000 юаней)
        self.fee_per_lot = fee_per_lot
        self.latency = latency
        self.quote_fn = quote_fn
        self.position = 0
        self.avg_price = 0.0
        self.orders = 0
        self._lock = threading.Lock()

    def fill(self, side: str, qty: int, limit_price: float) -> tuple[int, float]:
        bid, ask = self.quote_fn() if self.quote_fn else (limit_price, limit_price)
        if side == BUY:
            if not ask > 0 or (limit_price > 0 and limit_price < ask):
                return 0, 0.0
            px, signed = ask, qty
        else:
            if not bid > 0 or (limit_price > 0 and limit_price > bid):
                return 0, 0.0
            px, signed = bid, -qty
        with self._lock:
            self.orders += 1
            pos = self.position
            new = pos + signed
            if pos != 0 and (pos > 0) != (signed > 0):  # закрытие (частичное/полное)
                closed = min(abs(pos), qty)
                self.cash += closed * (px - self.avg_price) * self.point_value * (1 if pos > 0 else -1)
            if new == 0:
                self.avg_price = 0.0
            elif pos == 0 or (pos > 0) != (new > 0):
                self.avg_price = px
            elif abs(new) > abs(pos):
                self.avg_price = (self.avg_price * abs(pos) + px * qty) / abs(new)
            self.position = new
            self.cash -= self.fee_per_lot * qty
        return qty, px

    def equity(self) -> float:
        with self._lock:
            pos, avg = self.position, self.avg_price
            cash = self.cash
        if pos and self.quote_fn:
            bid, ask = self.quote_fn()
            mark = bid if pos > 0 else ask
            if mark > 0:
                cash += pos * (mark - avg) * self.point_value
        return cash


class PaperGateway:
    """Тот же интерфейс, что у FinamRestGateway, поверх PaperBroker."""

    def __init__(self, broker: PaperBroker, asset: AssetInfo | None = None) -> None:
        self.broker = broker
        self.asset = asset or AssetInfo(tick=0.001, decimals=3, lot_size=1.0)
        self.decimals = self.asset.decimals
        self.fail_next = 0  # для тестов: сколько следующих заявок уронить исключением

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def server_time(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def get_asset_info(self, symbol: str) -> AssetInfo:
        return self.asset

    async def get_equity(self) -> float:
        return self.broker.equity()

    async def get_position(self, symbol: str) -> tuple[int, float]:
        return self.broker.position, self.broker.avg_price

    async def get_margin_per_lot(self, symbol: str) -> tuple[float, float]:
        return self.broker.margin_per_lot, self.broker.margin_per_lot

    async def execute(self, symbol: str, side: str, qty: int, limit_price: float,
                      client_order_id: str, timeout: float, tif: str = "TIME_IN_FORCE_IOC") -> FillResult:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError("paper: simulated network failure")
        if self.broker.latency:
            await asyncio.sleep(self.broker.latency)
        filled, px = self.broker.fill(side, qty, limit_price)
        status = "ORDER_STATUS_FILLED" if filled == qty else "ORDER_STATUS_CANCELED"
        return FillResult(order_id=client_order_id, status=status, filled=filled, price=px or limit_price)

    async def fill_price(self, order_id: str, since: datetime) -> float:
        return 0.0
