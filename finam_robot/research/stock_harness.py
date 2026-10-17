"""Стенд портфельных стратегий на акциях MOEX (TQBR), дневные данные.

Каждый торговый день t делится на два отрезка:
  * ночь  — от закрытия t-1 до открытия t (в доходность ночи входит дивиденд
    на экс-дату, за вычетом НДФЛ 13 %);
  * день  — от открытия t до закрытия t.

Стратегия — функция `weights(data, **params) -> (w_night, w_day)`: две
таблицы дата × бумага с долями капитала. Правила, которые проверяет стенд:
  * w_day[t] (держим от открытия до закрытия t) — по данным до закрытия t-1
    включительно и цене открытия t; high/low/close/оборот дня t нельзя;
  * w_night[t] (держим от закрытия t-1 до открытия t) выставляется в
    аукционе закрытия t-1, когда цена закрытия t-1 ещё не известна, поэтому —
    по данным до закрытия t-2 и цене открытия t-1; high/low/close/оборот
    дня t-1 и любые данные дня t нельзя. Обычное «решили вечером — держим
    несколько дней» выражается так: w_day[t] = W (вход на открытии t),
    w_night[t+1] = W и далее;
  * длинные позиции без плеча: сумма долей <= 1; отрицательные доли допустимы
    только в w_day (внутридневной шорт без переноса) при allow_intraday_short;
  * сделки: в аукционе закрытия t-1 переход w_day[t-1] -> w_night[t], на
    открытии t переход w_night[t] -> w_day[t]; издержки cost_side с каждого
    рубля оборота (комиссия + проскальзывание).

Цены корректируются на сплиты и консолидации (доходности), а фильтр «дешевле
300 ₽» использует фактическую цену торгов того дня (как видел бы трейдер).

Периоды: разработка — до 2023-12-31, валидация — 2024-01-01…2025-06-30,
отложенная выборка — с 2025-07-01 (её файл исследователям не выдаётся).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

DEV_END = "2023-12-31"
VAL_START, VAL_END = "2024-01-01", "2025-06-30"
HOLDOUT_START = "2025-07-01"
NDFL = 0.13


@dataclass
class Data:
    dates: pd.Index
    secids: pd.Index
    open: pd.DataFrame        # скорректированные на сплиты
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    raw_close: pd.DataFrame   # фактическая цена торгов (для фильтра по цене)
    value: pd.DataFrame       # оборот, ₽
    numtrades: pd.DataFrame
    div: pd.DataFrame         # дивиденд на акцию после НДФЛ, на экс-дату (в масштабе скорректированных цен)
    names: dict

    def slice(self, end_date: str) -> Data:
        """Данные строго до end_date (для проверки на заглядывание в будущее)."""
        m = self.dates < end_date
        f = lambda df: df.loc[m]
        return Data(self.dates[m], self.secids, f(self.open), f(self.high), f(self.low), f(self.close),
                    f(self.raw_close), f(self.value), f(self.numtrades), f(self.div), self.names)

    # --- доходности отрезков ---
    def night_ret(self) -> pd.DataFrame:
        prev = self.close.shift(1)
        return ((self.open + self.div) / prev - 1.0)

    def day_ret(self) -> pd.DataFrame:
        return self.close / self.open - 1.0


def load(panel_path: str, splits_path: str, dividends_path: str | None = None,
         end: str | None = None) -> Data:
    p = pd.read_csv(panel_path)
    if end:
        p = p[p["date"] <= end]
    for c in ("open", "high", "low", "close", "value", "numtrades"):
        p[c] = pd.to_numeric(p[c], errors="coerce")
    # дни без сделок — пропуски, а не нули
    bad = (p["value"].fillna(0) <= 0) | (p["close"].fillna(0) <= 0) | (p["open"].fillna(0) <= 0)
    p.loc[bad, ["open", "high", "low", "close"]] = np.nan
    names = p.drop_duplicates("secid", keep="last").set_index("secid")["shortname"].to_dict()
    piv = {c: p.pivot_table(index="date", columns="secid", values=c, aggfunc="last")
           for c in ("open", "high", "low", "close", "value", "numtrades")}
    dates = piv["close"].index
    secids = piv["close"].columns
    piv = {k: v.reindex(index=dates, columns=secids) for k, v in piv.items()}
    raw_close = piv["close"].copy()

    # сплиты: цены ДО даты сплита умножаем на before/after
    factor = pd.DataFrame(1.0, index=dates, columns=secids)
    sp = pd.read_csv(splits_path)
    for _, r in sp.iterrows():
        if r["secid"] in factor.columns:
            factor.loc[factor.index < r["tradedate"], r["secid"]] *= float(r["before"]) / float(r["after"])
    for c in ("open", "high", "low", "close"):
        piv[c] = piv[c] * factor

    div = pd.DataFrame(0.0, index=dates, columns=secids)
    if dividends_path:
        dv = pd.read_csv(dividends_path)
        dv = dv[(dv.get("currencyid", "RUB") == "RUB") & dv["ex_date"].notna()]
        for _, r in dv.iterrows():
            s, d = r["secid"], r["ex_date"]
            if s in div.columns and d in div.index:
                # дивиденд на акцию в масштабе скорректированных цен
                div.loc[d, s] += float(r["value"]) * (1 - NDFL) * factor.loc[d, s]
    return Data(dates, secids, piv["open"], piv["high"], piv["low"], piv["close"], raw_close,
                piv["value"], piv["numtrades"], div, names)


def universe(data: Data, max_price: float = 300.0, min_value: float = 30e6, top_liquid: int = 40,
             top_vol: int | None = None, lookback: int = 20, lag: int = 2) -> pd.DataFrame:
    """Маска допуска на дату t по данным до t-lag: цена < max_price, медианный
    оборот за lookback дней >= min_value, top_liquid по обороту, затем (если
    задано) top_vol самых волатильных из них. lag=2 годится и для ночных,
    и для дневных весов."""
    medv = data.value.rolling(lookback, min_periods=lookback // 2).median().shift(lag)
    price = data.raw_close.shift(lag)
    ok = (price < max_price) & (medv >= min_value) & data.close.shift(lag).notna()
    rank_liq = medv.where(ok).rank(axis=1, ascending=False)
    ok &= rank_liq <= top_liquid
    if top_vol:
        vol = data.close.pct_change(fill_method=None).rolling(lookback, min_periods=lookback // 2).std().shift(lag)
        rank_vol = vol.where(ok).rank(axis=1, ascending=False)
        ok &= rank_vol <= top_vol
    return ok.fillna(False)


@dataclass
class Result:
    daily: pd.Series          # дневная доходность портфеля (после издержек)
    turnover: pd.Series       # оборот за день, доли капитала
    exposure: pd.Series       # средняя доля в позициях

    def monthly(self) -> pd.Series:
        g = (1 + self.daily).groupby(self.daily.index.str.slice(0, 7)).prod() - 1
        return g

    def yearly(self) -> pd.Series:
        return (1 + self.daily).groupby(self.daily.index.str.slice(0, 4)).prod() - 1

    def metrics(self) -> dict:
        d = self.daily
        n = len(d)
        if n < 2:
            return {}
        eq = (1 + d).cumprod()
        dd = float((eq / eq.cummax() - 1).min())
        m = self.monthly()
        y = self.yearly()
        sd = float(d.std(ddof=1))
        return {
            "days": n,
            "total_return": float(eq.iloc[-1] - 1),
            "cagr": float(eq.iloc[-1] ** (252 / n) - 1),
            "vol_ann": sd * math.sqrt(252),
            "sharpe_ann": float(d.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0,
            "max_dd": dd,
            "months": len(m),
            "pct_months_up": float((m > 0).mean()),
            "worst_month": float(m.min()),
            "best_month": float(m.max()),
            "years_up": f"{int((y > 0).sum())}/{len(y)}",
            "worst_year": float(y.min()),
            "turnover_daily": float(self.turnover.mean()),
            "exposure": float(self.exposure.mean()),
        }


def simulate(data: Data, w_night: pd.DataFrame, w_day: pd.DataFrame, cost_side: float = 0.001,
             allow_intraday_short: bool = False, start: str | None = None, end: str | None = None) -> Result:
    idx, cols = data.dates, data.secids
    wn = w_night.reindex(index=idx, columns=cols).fillna(0.0)
    wd = w_day.reindex(index=idx, columns=cols).fillna(0.0)
    if (wn < -1e-12).any().any():
        raise ValueError("overnight shorts are not allowed (w_night < 0)")
    if not allow_intraday_short and (wd < -1e-12).any().any():
        raise ValueError("intraday shorts disabled (w_day < 0)")
    for name, w in (("w_night", wn), ("w_day", wd)):
        if (w.clip(lower=0).sum(axis=1) > 1 + 1e-9).any():
            raise ValueError(f"{name}: long exposure above 100% (no leverage)")
    rn = data.night_ret()
    rd = data.day_ret()
    # нельзя держать бумагу, по которой нет цен на отрезке
    wn = wn.where(rn.notna(), 0.0)
    wd = wd.where(rd.notna(), 0.0)
    rn, rd = rn.fillna(0.0), rd.fillna(0.0)
    prev_day = wd.shift(1).fillna(0.0)
    t_close = (wn - prev_day).abs().sum(axis=1)     # аукцион закрытия t-1
    t_open = (wd - wn).abs().sum(axis=1)            # открытие t
    r_night = (wn * rn).sum(axis=1) - cost_side * t_close
    r_day = (wd * rd).sum(axis=1) - cost_side * t_open
    daily = (1 + r_night) * (1 + r_day) - 1
    turnover = t_close + t_open
    exposure = (wn.abs().sum(axis=1) + wd.abs().sum(axis=1)) / 2
    m = pd.Series(True, index=idx)
    if start:
        m &= idx >= start
    if end:
        m &= idx <= end
    return Result(daily[m], turnover[m], exposure[m])


def evaluate(data: Data, fn, params: dict, start: str, end: str, cost_side: float = 0.001,
             allow_intraday_short: bool = False) -> dict:
    wn, wd = fn(data, **params)
    return simulate(data, wn, wd, cost_side, allow_intraday_short, start, end).metrics()


def _perturb_row(data: Data, date: str, fields: tuple[str, ...], seed: int) -> Data:
    rng = np.random.default_rng(seed)
    out = {}
    for f in ("open", "high", "low", "close", "raw_close", "value", "numtrades"):
        df = getattr(data, f).astype(float)
        if f in fields or (f == "raw_close" and "close" in fields):
            row = df.loc[date]
            df.loc[date] = row * (1 + rng.uniform(0.05, 0.2, len(row)) * rng.choice([-1, 1], len(row)))
        out[f] = df
    return Data(data.dates, data.secids, out["open"], out["high"], out["low"], out["close"], out["raw_close"],
                out["value"], out["numtrades"], data.div, data.names)


def _diff_at(a: pd.DataFrame, b: pd.DataFrame, rows, cols) -> pd.Series:
    a = a.reindex(index=rows, columns=cols).fillna(0.0)
    b = b.reindex(index=rows, columns=cols).fillna(0.0)
    d = (a - b).abs().max(axis=1)
    return d[d > 1e-9]


def lookahead_check(fn, data: Data, params: dict, cuts: int = 4) -> tuple[bool, str]:
    """Две проверки:
    1) усечение: веса на данных до даты X совпадают с весами на полных данных;
    2) возмущение: high/low/close/оборот дня t не влияют ни на w_night[t],
       ни на w_day[t]; open дня t не влияет на w_night[t]; high/low/close/
       оборот дня t-1 не влияют на w_night[t] (её ставят в аукционе закрытия t-1)."""
    wn_f, wd_f = fn(data, **params)
    n = len(data.dates)
    for j, k in enumerate(np.linspace(n * 0.3, n * 0.95, cuts).astype(int)):
        cut = data.dates[k]
        part = data.slice(cut)
        wn_p, wd_p = fn(part, **params)
        for name, a, b in (("w_night", wn_f, wn_p), ("w_day", wd_f, wd_p)):
            bad = _diff_at(a, b, part.dates, data.secids)
            if len(bad):
                return False, f"{name} at {bad.index[0]} uses data from {cut} or later"
        # день t = последняя строка среза до (k+1)
        upto = data.slice(data.dates[min(k + 1, n - 1)]) if k + 1 < n else data
        t = upto.dates[-1]
        wn0, wd0 = fn(upto, **params)
        wn1, wd1 = fn(_perturb_row(upto, t, ("high", "low", "close", "value", "numtrades"), j), **params)
        for name, a, b in (("w_night", wn0, wn1), ("w_day", wd0, wd1)):
            if len(_diff_at(a, b, [t], data.secids)):
                return False, f"{name}[{t}] depends on high/low/close/value of the same day"
        wn2, _ = fn(_perturb_row(upto, t, ("open",), j + 100), **params)
        if len(_diff_at(wn0, wn2, [t], data.secids)):
            return False, f"w_night[{t}] depends on open[{t}] (night position is set at the previous close)"
        t_prev = upto.dates[-2]
        wn3, _ = fn(_perturb_row(upto, t_prev, ("high", "low", "close", "value", "numtrades"), j + 200), **params)
        if len(_diff_at(wn0, wn3, [t], data.secids)):
            return False, (f"w_night[{t}] depends on close/high/low/value of {t_prev}: that close is "
                           "not known yet when the closing-auction order is placed")
    return True, ""
