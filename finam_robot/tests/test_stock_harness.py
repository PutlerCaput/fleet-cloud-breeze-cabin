import numpy as np
import pandas as pd
import pytest

from finam_robot.research import stock_harness as H


@pytest.fixture()
def files(tmp_path):
    dates = pd.bdate_range("2024-01-01", periods=80).strftime("%Y-%m-%d")
    rng = np.random.default_rng(0)
    rows = []
    for s, base, val in (("AAA", 100.0, 5e8), ("BBB", 250.0, 2e8), ("CCC", 900.0, 9e8), ("ILL", 50.0, 1e5)):
        p = base
        for i, d in enumerate(dates):
            o = p * (1 + rng.normal(0, 0.005))
            c = o * (1 + rng.normal(0, 0.01))
            if s == "BBB" and d >= dates[40]:
                o, c = o / 10, c / 10            # сплит 1:10 с dates[40]
            rows.append((d, s, s, o, min(o, c) * 0.99, max(o, c) * 1.01, c, 1000, val, 100, c))
            p = c * 10 if (s == "BBB" and d >= dates[40]) else c
    panel = pd.DataFrame(rows, columns=["date", "secid", "shortname", "open", "low", "high", "close",
                                        "volume", "value", "numtrades", "waprice"])
    pp = tmp_path / "panel.csv.gz"
    panel.to_csv(pp, index=False, compression="gzip")
    sp = tmp_path / "splits.csv"
    pd.DataFrame([[dates[40], "BBB", 1, 10]], columns=["tradedate", "secid", "before", "after"]).to_csv(sp, index=False)
    dv = tmp_path / "div.csv"
    pd.DataFrame([["AAA", "2024-02-01", 5.0, "RUB", dates[30]]],
                 columns=["secid", "registryclosedate", "value", "currencyid", "ex_date"]).to_csv(dv, index=False)
    return str(pp), str(sp), str(dv), list(dates)


def test_split_is_adjusted_and_raw_price_kept(files):
    pp, sp, dv, dates = files
    d = H.load(pp, sp, dv)
    r = d.close["BBB"].pct_change()
    assert abs(r.loc[dates[40]]) < 0.1                    # без поправки было бы −90 %
    assert d.raw_close.loc[dates[39], "BBB"] > 200 and d.raw_close.loc[dates[41], "BBB"] < 40


def test_dividend_after_tax_lands_in_night_return(files):
    pp, sp, dv, dates = files
    d = H.load(pp, sp, dv)
    t = dates[30]
    expected = (d.open.loc[t, "AAA"] + 5.0 * 0.87) / d.close.shift(1).loc[t, "AAA"] - 1
    assert d.night_ret().loc[t, "AAA"] == pytest.approx(expected)


def test_universe_filters_price_and_liquidity(files):
    pp, sp, dv, _ = files
    d = H.load(pp, sp, dv)
    u = H.universe(d, max_price=300, min_value=1e6, top_liquid=10)
    assert not u["CCC"].any()          # дороже 300 ₽
    assert not u["ILL"].any()          # неликвид
    assert u["AAA"].iloc[25:].all()


def test_buy_and_hold_equals_close_to_close_minus_entry_cost(files):
    pp, sp, dv, dates = files
    d = H.load(pp, sp, dv, end=dates[25])
    w = pd.DataFrame(0.0, index=d.dates, columns=d.secids)
    w["AAA"] = 1.0
    w.iloc[0] = 0.0
    res = H.simulate(d, w, w, cost_side=0.001)
    cc = d.close["AAA"].pct_change()
    assert res.daily.iloc[1] == pytest.approx((1 + d.night_ret()["AAA"].iloc[1] - 0.001) * (1 + d.day_ret()["AAA"].iloc[1]) - 1)
    assert res.daily.iloc[5] == pytest.approx(cc.iloc[5])
    assert res.metrics()["months"] >= 1


def test_no_leverage_and_no_overnight_short(files):
    pp, sp, dv, _ = files
    d = H.load(pp, sp, dv)
    w = pd.DataFrame(0.6, index=d.dates, columns=d.secids)
    with pytest.raises(ValueError):
        H.simulate(d, w, w)
    w = pd.DataFrame(0.0, index=d.dates, columns=d.secids)
    w["AAA"] = -0.5
    with pytest.raises(ValueError):
        H.simulate(d, w, w * 0)


def test_lookahead_check_catches_three_kinds_of_peeking(files):
    pp, sp, dv, _ = files
    d = H.load(pp, sp, dv)

    def honest(data):
        mom = data.close.pct_change(5, fill_method=None).shift(1)
        w = (mom > 0).astype(float) / len(data.secids)
        return w.shift(1).fillna(0.0), w   # вход на открытии t, удержание через ночь t+1

    def same_day_close(data):
        w = (data.close > data.open).astype(float) / len(data.secids)
        return w * 0, w

    def night_uses_open(data):
        w = (data.open > data.close.shift(1)).astype(float) / len(data.secids)
        return w, w * 0

    def future(data):
        w = (data.close.shift(-1) > data.close).astype(float) / len(data.secids)
        return w, w

    assert H.lookahead_check(honest, d, {})[0]
    def night_uses_prev_close(data):
        w = (data.close.pct_change(fill_method=None).shift(1) < 0).astype(float) / len(data.secids)
        return w, w * 0   # купить в аукционе закрытия по сигналу от этого же закрытия

    for cheat in (same_day_close, night_uses_open, future, night_uses_prev_close):
        ok, _ = H.lookahead_check(cheat, d, {})
        assert not ok, cheat.__name__


def test_halted_stock_keeps_position_and_gap_is_paid(files):
    pp, sp, dv, dates = files
    p = pd.read_csv(pp)
    halt = p["secid"].eq("AAA") & p["date"].isin(dates[50:53])
    p.loc[halt, "value"] = 0                       # три дня без торгов
    after = p["secid"].eq("AAA") & p["date"].eq(dates[53])
    last = float(p.loc[p["secid"].eq("AAA") & p["date"].eq(dates[49]), "close"].iloc[0])
    p.loc[after, ["open", "close"]] = last * 0.7   # после паузы −30 %
    p.loc[after, "low"] = last * 0.69
    p.loc[after, "high"] = last * 0.71
    p.to_csv(pp, index=False, compression="gzip")
    d = H.load(pp, sp, dv)
    w = pd.DataFrame(0.0, index=d.dates, columns=d.secids)
    w.loc[dates[45]:dates[49], "AAA"] = 1.0
    wn = w.copy()
    wn.loc[dates[50], "AAA"] = 1.0                 # держим ночь, хотим выйти на открытии dates[50]
    res = H.simulate(d, wn, w, cost_side=0.0)
    assert res.daily.loc[dates[53]] == pytest.approx(-0.3)   # выйти в паузу нельзя — разрыв оплачен


def test_night_may_use_pre_close_of_previous_day_but_day_may_not_use_its_own(files):
    pp, sp, dv, _ = files
    d = H.load(pp, sp, dv)
    d.pre = d.close * 1.001                         # «цена в 18:00»
    n = len(d.secids)

    def auction_reversal(data):
        sig = (data.pre / data.close.shift(1) - 1).shift(1) < 0   # падение к 18:00 дня t-1
        return sig.astype(float) / n, sig.astype(float) * 0

    def day_uses_own_pre(data):
        return data.close * 0, (data.pre > data.open).astype(float) / n

    assert H.lookahead_check(auction_reversal, d, {})[0]
    assert not H.lookahead_check(day_uses_own_pre, d, {})[0]


def test_cash_yield_only_on_free_capital(files, tmp_path):
    pp, sp, dv, dates = files
    ix = tmp_path / "ix.csv"
    pd.DataFrame([[x, "RUSFAR", 18.5, 18.5] for x in dates], columns=["date", "secid", "open", "close"]).to_csv(ix, index=False)
    d = H.load(pp, sp, dv, indices_path=str(ix))
    zero = pd.DataFrame(0.0, index=d.dates, columns=d.secids)
    res = H.simulate(d, zero, zero, cash_yield=True)
    # будни подряд: 1 календарный день, понедельник — 3
    assert res.daily.loc[dates[2]] == pytest.approx((0.185 - H.CASH_FEE) / 365)
    assert res.metrics()["total_return"] > 0
    full = zero.copy()
    full["AAA"] = 1.0
    assert H.simulate(d, full, full, cash_yield=True).daily.iloc[5] == pytest.approx(
        H.simulate(d, full, full).daily.iloc[5])


def test_lot_check_reports_rounding(files, tmp_path):
    pp, sp, dv, _ = files
    d = H.load(pp, sp, dv)
    lots = tmp_path / "lots.csv"
    pd.DataFrame([["AAA", "A", 100, 0.01], ["BBB", "B", 1, 0.01]], columns=["secid", "shortname", "lotsize", "minstep"]).to_csv(lots, index=False)
    w = pd.DataFrame(0.0, index=d.dates, columns=d.secids)
    w["AAA"] = 0.2                    # 6000 ₽ < лота AAA (~10 000 ₽)
    w["BBB"] = 0.5
    r = H.lot_check(d, w, str(lots), capital=30_000)
    assert r["share_positions_below_one_lot"] == pytest.approx(0.5, abs=0.02)
    assert 0.15 < r["mean_weight_shortfall"] < 0.3
