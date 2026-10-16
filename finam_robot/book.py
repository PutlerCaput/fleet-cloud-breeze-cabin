"""Сборка стакана из сообщений API прямо в слот кольцевого буфера.

Два формата входа:
  * LevelsAssembler — снимок со списками `bids` / `asks` (уровни с price и
    quantity/volume/size), как в исходном `ob.bids` / `ob.asks`;
  * RowBook — строки Finam Trade API v1 (`rows`: price, buy_size | sell_size,
    action ADD/UPDATE/REMOVE). Это дельты: каждое сообщение обязано быть
    применено, иначе книга разъедется с биржей.

Форма объектов (float, Decimal{value}, {num, scale}, dict из REST JSON,
protobuf) определяется ОДИН раз по первому сообщению, дальше работает
специализированная функция без hasattr/isinstance на каждом тике.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from operator import attrgetter

_POW10 = tuple(10.0**k for k in range(19))


def _dec_plain(x) -> float:
    return float(x)


def _dec_value(x) -> float:
    return float(x.value)


def _dec_num_scale(x) -> float:
    return x.num / _POW10[x.scale]


def _dec_dict(x) -> float:
    return float(x["value"])


def make_decoder(sample):
    """Выбор декодера числа по образцу: float | "11.5" | {value} | Decimal(value) | {num, scale}."""
    if isinstance(sample, (int, float, str)):
        return _dec_plain
    if isinstance(sample, dict):
        return _dec_dict
    if hasattr(sample, "num") and hasattr(sample, "scale"):
        return _dec_num_scale
    if hasattr(sample, "value"):
        return _dec_value
    raise TypeError(f"Неизвестный формат числа: {type(sample)!r}")


class LevelsAssembler:
    """Снимок `msg.bids` / `msg.asks` -> слот кольца. Без промежуточных списков."""

    QTY_NAMES = ("quantity", "volume", "size", "qty", "lots")

    def __init__(self) -> None:
        self._ready = False
        self._px = _dec_plain
        self._qd = _dec_plain
        self._get_qty = attrgetter("quantity")
        self._bids_rev = False
        self._asks_rev = False
        # Результат write(): атрибуты, а не кортеж — малые int берутся из кэша
        # CPython, и на тике не создаётся ни одного GC-отслеживаемого объекта.
        self.nb = 0
        self.na = 0

    def _probe(self, msg) -> None:
        sample = msg.bids[0] if msg.bids else msg.asks[0]
        qty_name = next((n for n in self.QTY_NAMES if hasattr(sample, n)), None)
        if qty_name is None:
            raise TypeError(f"У уровня стакана нет поля объёма: {dir(sample)}")
        self._get_qty = attrgetter(qty_name)
        self._px = make_decoder(sample.price)
        self._qd = make_decoder(self._get_qty(sample))
        # Лучший уровень должен быть первым; если API отдаёт наоборот — читаем с конца.
        if len(msg.bids) > 1:
            self._bids_rev = self._px(msg.bids[0].price) < self._px(msg.bids[1].price)
        if len(msg.asks) > 1:
            self._asks_rev = self._px(msg.asks[0].price) > self._px(msg.asks[1].price)
        self._ready = True

    def write(self, msg, mv, base: int, depth: int) -> None:
        bids = msg.bids
        asks = msg.asks
        if not bids or not asks:
            self.nb = self.na = 0
            return
        if not self._ready:
            self._probe(msg)
        px = self._px
        qd = self._qd
        qty = self._get_qty
        nb = len(bids)
        na = len(asks)
        if nb > depth:
            nb = depth
        if na > depth:
            na = depth
        o_bq = base + depth
        o_ap = base + 2 * depth
        o_aq = base + 3 * depth
        last_b = len(bids) - 1
        last_a = len(asks) - 1
        for i in range(nb):
            lvl = bids[last_b - i] if self._bids_rev else bids[i]
            mv[base + i] = px(lvl.price)
            mv[o_bq + i] = qd(qty(lvl))
        for i in range(na):
            lvl = asks[last_a - i] if self._asks_rev else asks[i]
            mv[o_ap + i] = px(lvl.price)
            mv[o_aq + i] = qd(qty(lvl))
        self.nb = nb
        self.na = na


class RowBook:
    """Локальная книга из строк Finam (дельты или полный снимок).

    bids/asks: dict price -> size; bid_px/ask_px — отсортированные по
    возрастанию цены (лучший bid = последний, лучший ask = первый).
    Вставка bisect'ом O(log n + memmove) на книге в десятки уровней дешевле,
    чем сортировка dict на каждом сообщении.
    """

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.bid_px: list[float] = []
        self.ask_px: list[float] = []
        self._apply_row = None
        self.nb = 0               # результат write()
        self.na = 0
        self.messages = 0
        self.crossed = 0          # сколько раз книга оказалась пересечённой
        self.crossed_streak = 0   # подряд — повод переподписаться

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.bid_px.clear()
        self.ask_px.clear()
        self.crossed_streak = 0

    # --- запись уровней ---
    @staticmethod
    def _set(side: dict, prices: list, price: float, size: float) -> None:
        if size > 0.0:
            if price not in side:
                insort(prices, price)
            side[price] = size
        elif price in side:
            del side[price]
            del prices[bisect_left(prices, price)]

    # --- кодеки строк (выбираются один раз) ---
    def _probe(self, row) -> None:
        if isinstance(row, dict):
            camel = "buySize" in row or "sellSize" in row
            buy_key = "buySize" if camel else "buy_size"
            sell_key = "sellSize" if camel else "sell_size"
            dec = make_decoder(row["price"])
            set_ = self._set
            bids, asks, bpx, apx = self.bids, self.asks, self.bid_px, self.ask_px

            def apply_dict(r) -> None:
                price = dec(r["price"])
                remove = r.get("action") == "ACTION_REMOVE"
                b = r.get(buy_key)
                if b is not None:
                    set_(bids, bpx, price, 0.0 if remove else _dec_dict(b))
                    return
                s = r.get(sell_key)
                if s is not None:
                    set_(asks, apx, price, 0.0 if remove else _dec_dict(s))

            self._apply_row = apply_dict
            return

        dec = make_decoder(row.price)
        set_ = self._set
        bids, asks, bpx, apx = self.bids, self.asks, self.bid_px, self.ask_px

        if hasattr(row, "HasField"):  # protobuf: неустановленное поле != None
            try:
                remove_code = (
                    type(row).DESCRIPTOR.fields_by_name["action"]
                    .enum_type.values_by_name["ACTION_REMOVE"].number
                )
            except Exception:
                remove_code = 1

            def apply_proto(r) -> None:
                price = dec(r.price)
                remove = r.action == remove_code
                if r.HasField("buy_size"):
                    set_(bids, bpx, price, 0.0 if remove else float(r.buy_size.value))
                elif r.HasField("sell_size"):
                    set_(asks, apx, price, 0.0 if remove else float(r.sell_size.value))

            self._apply_row = apply_proto
            return

        def apply_obj(r) -> None:  # pydantic / dataclass: неустановленное = None
            price = dec(r.price)
            remove = r.action == "ACTION_REMOVE"
            b = r.buy_size
            if b is not None:
                set_(bids, bpx, price, 0.0 if remove else dec(b))
                return
            s = r.sell_size
            if s is not None:
                set_(asks, apx, price, 0.0 if remove else dec(s))

        self._apply_row = apply_obj

    def apply(self, rows, snapshot: bool = False) -> None:
        if snapshot:
            self.reset()
        if not rows:
            return
        if self._apply_row is None:
            self._probe(rows[0])
        apply_row = self._apply_row
        for r in rows:
            apply_row(r)
        self.messages += 1

    def write(self, mv, base: int, depth: int) -> None:
        bp = self.bid_px
        ap = self.ask_px
        bids = self.bids
        asks = self.asks
        n_b = len(bp)
        n_a = len(ap)
        nb = n_b if n_b < depth else depth
        na = n_a if n_a < depth else depth
        o_bq = base + depth
        o_ap = base + 2 * depth
        o_aq = base + 3 * depth
        for i in range(nb):
            p = bp[n_b - 1 - i]
            mv[base + i] = p
            mv[o_bq + i] = bids[p]
        for i in range(na):
            p = ap[i]
            mv[o_ap + i] = p
            mv[o_aq + i] = asks[p]
        if nb and na and bp[-1] >= ap[0]:
            self.crossed += 1
            self.crossed_streak += 1
        else:
            self.crossed_streak = 0
        self.nb = nb
        self.na = na
