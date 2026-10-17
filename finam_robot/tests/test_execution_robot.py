import asyncio
import time
from dataclasses import replace
from datetime import datetime, time as dtime

import numpy as np
import pytest

from finam_robot import kernels as K
from finam_robot.config import TZ_MOSCOW, RiskParams, Settings
from finam_robot.execution import ExecutionEngine, OrderIntent
from finam_robot.gateway import AssetInfo, PaperBroker, PaperGateway
from finam_robot.ml import MarketMlOptimizer, save_session
from finam_robot.notify import TelegramNotifier
from finam_robot.robot import InstitutionalContestRobot
from finam_robot.scheduler import Job, in_session
from finam_robot.sources import SyntheticBookSource

QUOTE = (11.500, 11.501)


def intent(target, urgent=False, limit=0.0):
    return OrderIntent(target, limit, 0.0, "test", urgent, time.perf_counter_ns())


async def run_engine(scenario, *, threaded, latency=0.03, fail_next=0):
    broker = PaperBroker(latency=latency, quote_fn=lambda: QUOTE)
    gw = PaperGateway(broker)
    gw.fail_next = fail_next
    reports = []
    loop = asyncio.get_running_loop()

    async def factory():
        return gw

    eng = ExecutionEngine(factory, "CRZ6@RTSX", reports.append, loop, threaded=threaded,
                          max_orders_per_sec=100, order_timeout=0.5, exit_retries=3,
                          quote_fn=lambda: QUOTE)
    await eng.start()
    try:
        await scenario(eng)
    finally:
        await eng.stop()
    await asyncio.sleep(0.01)
    return broker, eng, reports


@pytest.mark.parametrize("threaded", [False, True])
def test_engine_coalesces_targets(threaded):
    async def scenario(eng):
        eng.submit(intent(5))
        await asyncio.sleep(0.005)          # первая заявка уже в пути
        eng.submit(intent(3))
        eng.submit(intent(-2))              # из трёх целей исполняется последняя
        await asyncio.sleep(0.3)

    broker, eng, reports = asyncio.run(run_engine(scenario, threaded=threaded))
    assert broker.position == -2 and eng.position == -2
    assert broker.orders == 2 and eng.coalesced == 1
    assert reports[0].kind == "sync" and reports[-1].kind == "done"
    assert reports[-1].position == -2


@pytest.mark.parametrize("threaded", [False, True])
def test_urgent_exit_reconciles_after_failure(threaded):
    async def scenario(eng):
        eng.submit(intent(4))
        await asyncio.sleep(0.1)
        eng._gw.fail_next = 1               # сеть упала на выходе
        eng.submit(intent(0, urgent=True))
        await asyncio.sleep(0.5)

    broker, eng, reports = asyncio.run(run_engine(scenario, threaded=threaded))
    assert broker.position == 0             # закрылись, и без двойного закрытия
    done = [r for r in reports if r.kind == "done"]
    assert done[-1].position == 0 and "ConnectionError" in done[-1].error


def test_failed_requested_sync_does_not_kill_executor():
    async def scenario(eng):
        real = eng._gw.get_position

        async def boom(symbol):
            raise ConnectionError("sync down")

        eng._gw.get_position = boom
        eng.request_sync()
        await asyncio.sleep(0.05)
        eng._gw.get_position = real
        assert eng.alive()
        eng.submit(intent(2))
        await asyncio.sleep(0.2)

    broker, eng, reports = asyncio.run(run_engine(scenario, threaded=False))
    assert broker.position == 2


def test_ioc_limit_that_misses_reports_zero_fill():
    async def scenario(eng):
        eng.submit(intent(2, limit=11.490))  # ниже ask -> IOC не исполняется
        await asyncio.sleep(0.2)

    broker, eng, reports = asyncio.run(run_engine(scenario, threaded=False))
    done = [r for r in reports if r.kind == "done"][-1]
    assert done.filled == 0 and done.orders == 1 and broker.position == 0


def robot_settings(tmp_path, **kw):
    risk = RiskParams(warmup_n=50.0, tau_vol=2.0, cooldown=0.5)
    base = Settings(symbol="CRZ6@RTSX", book_source="synthetic", dry_run=True, data_dir=str(tmp_path),
                    log_file=str(tmp_path / "robot.log"), no_entry_windows=(), md_stale_after=5.0,
                    risk=risk)
    return replace(base, **kw)


@pytest.mark.parametrize("threaded,inline", [(True, True), (False, False)])
def test_robot_session_round_trip(tmp_path, threaded, inline):
    settings = robot_settings(tmp_path, exec_threaded=threaded, inline_consumer=inline)
    broker = PaperBroker(latency=0.005)
    asset = AssetInfo(tick=0.001, decimals=3, lot_size=1.0)

    async def account_factory():
        return PaperGateway(broker, asset)

    async def scenario():
        robot = InstitutionalContestRobot(
            settings, account_factory=account_factory,
            source_factory=lambda: SyntheticBookSource(rate_hz=1000, seed=3, burst_prob=0.01),
            notifier=TelegramNotifier("", ""))
        broker.quote_fn = robot.quote
        await robot.start_trading_session()
        assert robot.is_active and robot.lots_long == 19
        await asyncio.sleep(4.0)
        ticks_mid_session = robot.ticks
        await robot.stop_trading_session()
        return robot, ticks_mid_session

    robot, ticks = asyncio.run(scenario())
    assert ticks > 500
    assert robot.position == 0 and broker.position == 0
    assert robot.S[K.S_PENDING] == 0.0 and robot.S[K.S_SIDE] == 0.0
    assert broker.orders >= 1
    assert list((tmp_path / "features").glob("*.npz"))


def test_robot_adopts_exchange_position_on_start(tmp_path):
    broker = PaperBroker(latency=0.0)
    broker.position, broker.avg_price = 3, 11.500  # робот перезапущен с открытой позицией
    settings = robot_settings(tmp_path, exec_threaded=False)

    async def account_factory():
        return PaperGateway(broker)

    async def scenario():
        robot = InstitutionalContestRobot(
            settings, account_factory=account_factory,
            source_factory=lambda: SyntheticBookSource(rate_hz=500), notifier=TelegramNotifier("", ""))
        broker.quote_fn = robot.quote
        await robot.start_trading_session()
        await asyncio.sleep(0.2)
        side, stop = robot.S[K.S_SIDE], robot.S[K.S_STOP]
        await robot.stop_trading_session()
        return side, stop

    side, stop = asyncio.run(scenario())
    assert side == 1.0 and stop < 11.500
    assert broker.position == 0


def synthetic_rows(n, edge, seed=1):
    rng = np.random.default_rng(seed)
    imb = np.zeros(n)
    for i in range(1, n):
        imb[i] = 0.98 * imb[i - 1] + rng.normal(0, 0.1)
    imb = np.clip(imb, -1, 1)
    steps = np.where(rng.random(n) < 0.3, np.sign(edge * imb + rng.normal(0, 0.3, n)), 0.0) * 0.001
    mid = 11.5 + np.cumsum(steps)
    rows = np.zeros((n, K.REC_COLS))
    rows[:, K.REC_TS] = np.arange(n) * 0.25
    rows[:, K.REC_IMB] = imb
    rows[:, K.REC_BIAS] = imb * 0.3
    rows[:, K.REC_MID] = mid
    rows[:, K.REC_SPREAD] = 1.0
    return rows


def test_ml_approves_real_edge_and_rejects_noise(tmp_path):
    for name, edge, expect in (("edge", 1.0, "APPROVED"), ("noise", 0.0, "REJECTED")):
        d = tmp_path / name
        rows = synthetic_rows(20_000, edge)
        save_session(rows, len(rows), str(d), datetime(2026, 10, 12))
        res = MarketMlOptimizer(str(d), horizon=2.0).train_and_optimize()
        assert res["status"] == expect, res
        if expect == "APPROVED":
            assert res["weights"][0][0] > 0 and len(res["weights"][0]) == 2
            assert isinstance(res["intercept"], float)


def test_scheduler_skips_weekend_and_session_window():
    job = Job("start", dtime(9, 55), lambda: None, (0, 1, 2, 3, 4))
    fri_evening = datetime(2026, 10, 16, 23, 0, tzinfo=TZ_MOSCOW)
    assert job.compute_next(fri_evening) == datetime(2026, 10, 19, 9, 55, tzinfo=TZ_MOSCOW)
    mon_morning = datetime(2026, 10, 19, 9, 0, tzinfo=TZ_MOSCOW)
    assert job.compute_next(mon_morning).day == 19
    start, stop = dtime(9, 55), dtime(23, 40)
    assert in_session(datetime(2026, 10, 19, 12, 0, tzinfo=TZ_MOSCOW), start, stop, range(5))
    assert not in_session(datetime(2026, 10, 18, 12, 0, tzinfo=TZ_MOSCOW), start, stop, range(5))
    assert not in_session(datetime(2026, 10, 19, 23, 45, tzinfo=TZ_MOSCOW), start, stop, range(5))
