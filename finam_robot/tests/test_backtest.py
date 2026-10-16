import asyncio
from dataclasses import replace
from datetime import datetime, time

import numpy as np
import pytest

from finam_robot import kernels as K
from finam_robot.backtest import (BtConfig, STATIC_RISK, Tape, block_permute_flow, bootstrap_trades,
                                  run_backtest, tape_from_books_npz, tape_from_trades)
from finam_robot.config import TZ_MOSCOW, RiskParams
from finam_robot.pipeline import SnapshotRing
from finam_robot.recorder import BookRecorder

TICK = 0.001
T0 = datetime(2026, 10, 16, 10, 0, tzinfo=TZ_MOSCOW).timestamp()
RISK = RiskParams(warmup_n=10.0, tau_vol=1.0, cooldown=0.0)
CFG = BtConfig(fee_per_lot=1.0, latency=0.05, no_entry_windows=())


def make_tape(path, dt=0.1, bq=300.0, aq=50.0, depth_known=False, levels=3, qty=1000.0):
    """path: список (bid, ask) по событиям; объёмы — сильный перекос в bid."""
    n = len(path)
    books = np.zeros((n, 4, levels))
    for i, (b, a) in enumerate(path):
        for k in range(levels):
            books[i, K.BID_PX, k] = b - k * TICK
            books[i, K.ASK_PX, k] = a + k * TICK
            books[i, K.BID_QTY, k] = bq if not depth_known else qty
            books[i, K.ASK_QTY, k] = aq if not depth_known else qty
    ts = T0 + np.arange(n) * dt
    return Tape(ts, books, np.full(n, levels), np.full(n, levels), TICK, "test", depth_known)


def test_entry_after_latency_and_hard_stop_pnl():
    path = [(12.600, 12.601)] * 40 + [(12.590, 12.591)] * 20
    tape = make_tape(path)
    res = run_backtest(tape, CFG, RISK)
    assert len(res.trades) >= 1
    tr = res.trades[0]
    lots = int(30_000 * 0.95 // CFG.margin_per_lot)
    assert tr.side == 1 and tr.qty == lots
    assert tr.entry_px == pytest.approx(12.601)          # ask на момент прихода заявки
    assert tr.reason == "hard_stop" and tr.exit_px == pytest.approx(12.590)
    expected = (12.590 - 12.601) * lots * CFG.point_value - 2 * lots * CFG.fee_per_lot
    assert tr.pnl == pytest.approx(expected)
    # первая сделка не раньше прогрева + latency
    assert tr.entry_ts - tape.ts[0] >= 1.0


def test_session_end_flattens_and_equity_matches_trades():
    path = [(12.600, 12.601)] * 60
    tape = make_tape(path)
    cfg = replace(CFG, session_stop=time(10, 0, 4))
    res = run_backtest(tape, cfg, RISK)
    assert res.trades and res.trades[-1].reason == "session_end"
    assert res.end_equity - res.start_equity == pytest.approx(sum(t.pnl for t in res.trades))


def test_ioc_miss_with_known_depth_and_level_walk():
    path = [(12.600, 12.601)] * 40
    tape = make_tape(path, depth_known=True, qty=5.0)  # по 5 лотов на уровне
    # заставить ядро хотеть вход: перекос объёмов
    tape.books[:, K.BID_QTY, :] = 300.0
    tape.books[:, K.ASK_QTY, :] = 5.0
    risk = replace(RISK, entry_slip=1.0)                  # лимит ask+1: хватит 2 уровней = 10 лотов
    res = run_backtest(tape, replace(CFG, session_stop=time(10, 0, 3)), risk)
    tr = res.trades[0]
    assert tr.qty == 10                                   # частичное IOC-исполнение по двум уровням
    assert tr.entry_px == pytest.approx((5 * 12.601 + 5 * 12.602) / 10)


def test_daily_loss_limit_stops_trading():
    path = [(12.600, 12.601)] * 40 + [(12.500 - i * TICK, 12.501 - i * TICK) for i in range(200)]
    tape = make_tape(path)
    res = run_backtest(tape, replace(CFG, max_daily_loss_pct=1.0), replace(RISK, hard_min=200.0, hard_max=200.0))
    assert res.trades[-1].reason == "daily_loss"
    assert len(res.trades) == 1


def test_static_profile_matches_original_thresholds():
    P = RiskParams(**STATIC_RISK)
    assert P.trail_min == P.trail_max == 5.0
    assert P.fee_ticks + P.act_mult * P.trail_min == 15.0
    assert P.hard_min == P.hard_max == 10.0


def test_tape_from_trades_quotes_and_flow():
    ts = T0 + np.array([0.0, 0.1, 0.2, 0.3])
    price = np.array([12.600, 12.601, 12.605, 12.603])
    side = np.array([-1.0, 1.0, 1.0, -1.0])
    tape = tape_from_trades(ts, price, np.array([5.0, 3.0, 2.0, 1.0]), side, TICK)
    b = tape.books
    assert b[1, K.BID_PX, 0] == pytest.approx(12.600) and b[1, K.ASK_PX, 0] == pytest.approx(12.601)
    assert b[2, K.ASK_PX, 0] == pytest.approx(12.605) and b[2, K.BID_PX, 0] == pytest.approx(12.604)  # спред <= 2
    assert b[3, K.BID_PX, 0] == pytest.approx(12.603) and b[3, K.ASK_PX, 0] == pytest.approx(12.605)
    assert b[2, K.BID_QTY, 0] > b[2, K.ASK_QTY, 0]  # после покупок поток в сторону bid


def test_null_permutation_keeps_prices_and_bootstrap():
    tape = make_tape([(12.600 + (i % 7) * TICK, 12.601 + (i % 7) * TICK) for i in range(3000)])
    tape.books[:, K.BID_QTY, :] = np.arange(3000)[:, None]
    perm = block_permute_flow(tape, 10.0, np.random.default_rng(0))
    assert np.array_equal(perm.books[:, K.BID_PX, :], tape.books[:, K.BID_PX, :])
    assert sorted(perm.books[:, K.BID_QTY, 0]) == sorted(tape.books[:, K.BID_QTY, 0])
    assert not np.array_equal(perm.books[:, K.BID_QTY, 0], tape.books[:, K.BID_QTY, 0])
    res = run_backtest(make_tape([(12.600, 12.601)] * 40 + [(12.590, 12.591)] * 20), CFG, RISK)
    bs = bootstrap_trades(res.trades, CFG.equity, n=200)
    assert bs["prob_loss"] == 1.0


def test_book_recorder_roundtrip(tmp_path):
    async def scenario():
        ring = SnapshotRing(8, 3)
        rec = BookRecorder(str(tmp_path), 3, capacity=4)
        for i in range(10):  # 2.5 буфера: проверка ротации и записи хвоста
            idx = ring.next_slot()
            ring.books[idx, K.BID_PX, 0] = 12.0 + i * TICK
            ring.books[idx, K.ASK_PX, 0] = 12.001 + i * TICK
            ring.publish(idx, 3, 3, T0 + i)
            rec.push(ring, idx)
            await asyncio.sleep(0.01)
        await rec.close()

    asyncio.run(scenario())
    tape = tape_from_books_npz(str(tmp_path / "books_*.npz"), TICK)
    assert len(tape) == 10 and tape.depth_known
    assert np.allclose(np.diff(tape.ts), 1.0)
    assert tape.books[9, K.BID_PX, 0] == pytest.approx(12.009)
