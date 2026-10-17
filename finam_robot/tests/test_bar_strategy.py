import asyncio
import textwrap
from dataclasses import replace
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from finam_robot import kernels as K
from finam_robot.bar_strategy import BarStrategyRunner, finam_bars_to_rows, prepare_bars
from finam_robot.config import TZ_MOSCOW, RiskParams, Settings
from finam_robot.gateway import AssetInfo, PaperBroker, PaperGateway
from finam_robot.notify import TelegramNotifier
from finam_robot.robot import InstitutionalContestRobot
from finam_robot.sources import SyntheticBookSource


def mom_positions(bars, look=20):
    """Каузальная тестовая стратегия: знак изменения цены за look минут внутри дня."""
    prev = bars.groupby("day")["close"].shift(look)
    sig = np.sign(bars["close"] - prev).fillna(0.0).to_numpy(copy=True)
    tod = bars["tod"].to_numpy()
    sig[(tod < 600) | (tod >= 13 * 60 + 30)] = 0.0
    return sig


def history_rows(days=("2026-10-12", "2026-10-13", "2026-10-14")):
    rows = []
    for d, day in enumerate(days):
        for k, t in enumerate(pd.date_range(f"{day} 09:50", f"{day} 13:59", freq="1min")):
            p = 12.6 + 0.01 * np.sin(k / 17.0 + d) + 0.0002 * k
            rows.append([t.strftime("%Y-%m-%d %H:%M:%S"), p, p + 0.001, p - 0.001, p, 10.0, "CRZ6"])
    return rows


def test_live_runner_matches_backtest_positions_bar_by_bar():
    rows = history_rows()
    full = prepare_bars(pd.DataFrame(rows, columns=["begin", "open", "high", "low", "close", "volume", "secid"]))
    expected = np.sign(mom_positions(full)).astype(int)
    clock = {"now": None}
    seen = []
    runner = BarStrategyRunner(None, mom_positions, {}, lambda s, ch, b: seen.append((b, s)),
                               history_days=2, clock=lambda: clock["now"])
    for i, r in enumerate(rows):
        clock["now"] = datetime.fromisoformat(r[0]).replace(tzinfo=TZ_MOSCOW) + timedelta(seconds=63)
        runner.step(rows[max(0, i - 5): i + 1])  # как живой опрос: последние несколько свечей
    got = dict(seen)
    assert len(got) == len(rows)
    assert all(got[r[0]] == expected[i] for i, r in enumerate(rows))


def test_runner_ignores_bar_that_is_not_closed_yet():
    rows = history_rows(days=("2026-10-12",))[:30]
    now = datetime.fromisoformat(rows[-1][0]).replace(tzinfo=TZ_MOSCOW) + timedelta(seconds=30)
    runner = BarStrategyRunner(None, mom_positions, {}, lambda *a: None, clock=lambda: now)
    runner.step(rows)
    assert runner.state.last_begin == rows[-2][0]


def test_finam_bars_payload_is_converted_to_moscow_time():
    payload = {"bars": [{"timestamp": "2026-10-16T07:00:00Z", "open": {"value": "12.6"}, "high": {"value": "12.61"},
                         "low": {"value": "12.59"}, "close": {"value": "12.605"}, "volume": {"value": "150"}}]}
    assert finam_bars_to_rows(payload, "CRZ6") == [["2026-10-16 10:00:00", 12.6, 12.61, 12.59, 12.605, 150.0, "CRZ6"]]


class EmptyFeed:
    async def fetch(self, start, end):
        return []


def test_robot_bar_mode_follows_signal_and_respects_disaster_stop(tmp_path):
    strat = tmp_path / "strategy.py"
    strat.write_text(textwrap.dedent("""
        import numpy as np
        BEST_PARAMS = {}
        def positions(bars, **p):
            return np.zeros(len(bars))
    """))
    settings = replace(Settings(), symbol="CRZ6@RTSX", book_source="synthetic", dry_run=True,
                       data_dir=str(tmp_path), log_file=str(tmp_path / "r.log"), no_entry_windows=(),
                       md_stale_after=5.0, strategy="bars", bar_strategy=str(strat), bar_disaster_ticks=500.0,
                       risk=RiskParams(warmup_n=20.0, tau_vol=1.0))
    broker = PaperBroker(latency=0.0)

    async def account_factory():
        return PaperGateway(broker, AssetInfo(tick=0.001, decimals=3, lot_size=1.0))

    async def scenario():
        robot = InstitutionalContestRobot(
            settings, account_factory=account_factory,
            source_factory=lambda: SyntheticBookSource(rate_hz=500, seed=11),
            notifier=TelegramNotifier("", ""), bar_feed_factory=EmptyFeed)
        broker.quote_fn = robot.quote
        await robot.start_trading_session()
        await asyncio.sleep(0.3)                       # котировки и гейты
        assert robot.entries_allowed
        robot._on_bar_signal(1, True, "b1")            # стратегия хочет лонг
        await asyncio.sleep(0.2)
        long_lots = broker.position
        robot._on_bar_signal(0, True, "b2")            # стратегия вне рынка
        await asyncio.sleep(0.2)
        flat_after_signal = broker.position
        robot.P[K.P_HARD_MIN] = robot.P[K.P_HARD_MAX] = 2.0  # узкий аварийный стоп сработает на шуме
        robot._on_bar_signal(-1, True, "b3")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if robot._bar_blocked and broker.position == 0:
                break
        blocked = robot._bar_blocked
        robot._on_bar_signal(-1, False, "b4")          # тот же сигнал: повторного входа нет
        await asyncio.sleep(0.2)
        pos_after_block = broker.position
        await robot.stop_trading_session()
        return long_lots, flat_after_signal, blocked, pos_after_block, robot

    long_lots, flat_after_signal, blocked, pos_after_block, robot = asyncio.run(scenario())
    assert long_lots == robot.lots_long > 0
    assert flat_after_signal == 0
    assert blocked and pos_after_block == 0
    assert robot.P[K.P_ACT_MULT] >= 1e9  # трейлинг ядра выключен в режиме свечей
    assert broker.position == 0
