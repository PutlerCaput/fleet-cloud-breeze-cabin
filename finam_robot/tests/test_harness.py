import numpy as np
import pandas as pd
import pytest

from finam_robot.research.harness import Costs, deflated_sharpe, lookahead_check, simulate


def make_bars(days=("2025-03-03", "2025-03-04"), start="09:50", end="23:49", step=0.001, base=12.0):
    rows = []
    for d in days:
        for k, t in enumerate(pd.date_range(f"{d} {start}", f"{d} {end}", freq="1min")):
            p = base + k * step
            rows.append((t, p, p + step, p - step, p + step))
    df = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close"])
    df["volume"] = 10.0
    df["day"] = df["dt"].dt.strftime("%Y-%m-%d")
    df["tod"] = df["dt"].dt.hour * 60 + df["dt"].dt.minute
    return df


def test_long_all_day_pnl_costs_and_forced_flat():
    bars = make_bars(days=("2025-03-03",))
    target = np.where(bars["tod"] >= 10 * 60, 1.0, 0.0)
    res = simulate(bars, target, Costs(fee_rub=1.0, slip_ticks=1.0))
    i_dec = int(np.flatnonzero(bars["tod"] == 600)[0])
    entry = bars["open"].iloc[i_dec + 1] + 0.001
    i_last = int(np.flatnonzero(bars["tod"] < 23 * 60 + 40)[-1])
    exit_ = bars["close"].iloc[i_last] - 0.001
    expected = (exit_ - entry) * 1000 - 2.0
    assert res.daily[0] == pytest.approx(expected)
    assert len(res.trades) == 1 and res.trades["net_ticks"].iloc[0] == pytest.approx(expected)


def test_no_entry_before_window_or_in_clearing_but_exit_allowed():
    bars = make_bars(days=("2025-03-03",))
    tod = bars["tod"].to_numpy()
    target = np.where((tod >= 13 * 60 + 59) & (tod < 14 * 60 + 3), 1.0, 0.0)  # только в клиринг
    assert simulate(bars, target).trades.empty
    target = np.where(tod < 9 * 60 + 55, -1.0, 0.0)                            # только до 09:55
    assert simulate(bars, target).trades.empty
    target = np.where((tod >= 13 * 60) & (tod < 14 * 60 + 2), 1.0, 0.0)        # вошли до, вышли в клиринг
    res = simulate(bars, target)
    assert len(res.trades) == 1


def test_nothing_held_overnight():
    bars = make_bars()
    res = simulate(bars, np.ones(len(bars)))
    assert len(res.trades) == 2 and len(res.daily) == 2


def test_lookahead_check_catches_future_peeking():
    bars = make_bars(days=("2025-03-03", "2025-03-04", "2025-03-05"))

    def honest(b):
        return np.sign(b["close"].diff().fillna(0)).to_numpy()

    def cheat(b):
        return np.sign(b["close"].shift(-1).fillna(b["close"]) - b["close"]).to_numpy()

    assert lookahead_check(honest, bars, {})[0]
    assert not lookahead_check(cheat, bars, {})[0]


def test_deflated_sharpe_penalizes_many_trials():
    rng = np.random.default_rng(0)
    daily = rng.normal(5, 20, 250)
    assert deflated_sharpe(daily, 1) > deflated_sharpe(daily, 1000)
