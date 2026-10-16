import asyncio
from types import SimpleNamespace as NS

import pytest

from finam_robot import kernels as K
from finam_robot.book import LevelsAssembler, RowBook, make_decoder
from finam_robot.pipeline import ConflatingConsumer, Doorbell, SnapshotRing


def slot(ring, idx):
    return ring.books[idx]


def test_decoders():
    assert make_decoder(1.5)(1.5) == 1.5
    assert make_decoder("11.523")("11.523") == pytest.approx(11.523)
    assert make_decoder({"value": "2"})({"value": "2"}) == 2.0
    d = NS(num=11523, scale=3)
    assert make_decoder(d)(d) == pytest.approx(11.523)
    v = NS(value="7.25")
    assert make_decoder(v)(v) == 7.25


def test_levels_assembler_num_scale_and_reversed_order():
    ring = SnapshotRing(4, 5)
    bids = [NS(price=NS(num=11498 + i, scale=3), quantity=10 + i) for i in range(3)]  # худший первым
    asks = [NS(price=NS(num=11501 + i, scale=3), quantity=20 + i) for i in range(7)]
    asm = LevelsAssembler()
    idx = ring.next_slot()
    asm.write(NS(bids=bids, asks=asks), ring.mv, idx * ring.stride, ring.depth)
    assert (asm.nb, asm.na) == (3, 5)
    b = slot(ring, idx)
    assert list(b[K.BID_PX, :3]) == pytest.approx([11.500, 11.499, 11.498])
    assert list(b[K.BID_QTY, :3]) == [12, 11, 10]
    assert b[K.ASK_PX, 0] == pytest.approx(11.501)
    assert b[K.ASK_QTY, 4] == 24


def test_row_book_dict_deltas_snake_and_camel():
    for buy, sell in (("buy_size", "sell_size"), ("buySize", "sellSize")):
        ring = SnapshotRing(4, 3)
        book = RowBook()
        book.apply([
            {"price": {"value": "11.500"}, buy: {"value": "10"}, "action": "ACTION_ADD"},
            {"price": {"value": "11.499"}, buy: {"value": "5"}, "action": "ACTION_ADD"},
            {"price": {"value": "11.502"}, sell: {"value": "7"}, "action": "ACTION_ADD"},
            {"price": {"value": "11.501"}, sell: {"value": "3"}, "action": "ACTION_ADD"},
        ])
        book.apply([
            {"price": {"value": "11.501"}, sell: {"value": "0"}, "action": "ACTION_REMOVE"},
            {"price": {"value": "11.500"}, buy: {"value": "12"}, "action": "ACTION_UPDATE"},
        ])
        book.write(ring.mv, 0, ring.depth)
        b = slot(ring, 0)
        assert (book.nb, book.na) == (2, 1)
        assert b[K.BID_PX, 0] == pytest.approx(11.500) and b[K.BID_QTY, 0] == 12
        assert b[K.ASK_PX, 0] == pytest.approx(11.502) and b[K.ASK_QTY, 0] == 7


def test_row_book_snapshot_resets_and_crossing_counter():
    book = RowBook()
    ring = SnapshotRing(4, 3)
    book.apply([{"price": {"value": "1.0"}, "buy_size": {"value": "1"}, "action": "ACTION_ADD"}])
    book.apply([{"price": {"value": "2.0"}, "sell_size": {"value": "1"}, "action": "ACTION_ADD"}], snapshot=True)
    assert book.bids == {} and book.asks == {2.0: 1.0}
    book.apply([{"price": {"value": "3.0"}, "buy_size": {"value": "1"}, "action": "ACTION_ADD"}])
    book.write(ring.mv, 0, 3)
    assert book.crossed_streak == 1


def test_row_book_objects_and_protobuf_like():
    obj_rows = [NS(price=NS(value="11.5"), buy_size=NS(value="4"), sell_size=None, action="ACTION_ADD"),
                NS(price=NS(value="11.6"), buy_size=None, sell_size=NS(value="6"), action="ACTION_ADD")]
    book = RowBook()
    book.apply(obj_rows)
    assert book.bids == {11.5: 4.0} and book.asks == {11.6: 6.0}

    class Row:  # имитация protobuf: HasField, неустановленный oneof — пустой Decimal
        def __init__(self, price, side, size, action):
            self.price = NS(value=price)
            self.buy_size = NS(value=size if side == "b" else "")
            self.sell_size = NS(value=size if side == "s" else "")
            self.action = action
            self._side = side

        def HasField(self, name):
            return (name == "buy_size") == (self._side == "b")

    pbook = RowBook()
    pbook.apply([Row("11.5", "b", "4", 2), Row("11.6", "s", "6", 2)])
    pbook.apply([Row("11.6", "s", "6", 1)])  # 1 == ACTION_REMOVE (fallback)
    assert pbook.bids == {11.5: 4.0} and pbook.asks == {}


def test_ring_requires_power_of_two_and_wraps():
    with pytest.raises(ValueError):
        SnapshotRing(6, 3)
    ring = SnapshotRing(4, 3)
    for i in range(6):
        ring.publish(ring.next_slot(), 1, 1, float(i))
    assert ring.seq == 6 and ring.latest() == 1 and ring.tss[ring.latest()] == 5.0


def test_doorbell_conflates_and_consumer_takes_latest():
    async def scenario():
        ring = SnapshotRing(8, 3)
        seen = []
        cons = ConflatingConsumer(ring, seen.append)
        task = asyncio.create_task(cons.run())
        await asyncio.sleep(0)
        for i in range(100):  # пачка без await между публикациями
            idx = ring.next_slot()
            ring.publish(idx, 1, 1, float(i))
            cons.doorbell.ring()
        await asyncio.sleep(0.01)
        cons.active = False
        task.cancel()
        return ring, seen, cons

    ring, seen, cons = asyncio.run(scenario())
    assert len(seen) == 1 and ring.tss[seen[0]] == 99.0
    assert cons.conflated == 99


def test_doorbell_wait_blocks_until_ring():
    async def scenario():
        bell = Doorbell()
        bell.ring()
        await asyncio.wait_for(bell.wait(), 0.1)  # уже звонили — сразу
        waiter = asyncio.create_task(bell.wait())
        await asyncio.sleep(0.01)
        assert not waiter.done()
        bell.ring()
        await asyncio.wait_for(waiter, 0.1)

    asyncio.run(scenario())
