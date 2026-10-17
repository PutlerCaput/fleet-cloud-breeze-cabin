"""Единый стенд проверки внутридневных стратегий на минутных свечах CR.

Договор для любой стратегии: функция `positions(bars, **params) -> np.ndarray`
длины len(bars), где значение i — желаемая позиция в лотах ПОСЛЕ закрытия
свечи i, посчитанная только по данным свечей 0..i. Стенд сам:

  * исполняет изменение позиции по open свечи i+1 (того же дня) с
    проскальзыванием `slip_ticks` тиков на лот и комиссией за лот;
  * не даёт наращивать позицию вне окна входов (до 09:55, в клиринг,
    после 23:30) — сокращать можно всегда;
  * принудительно закрывает позицию на close последней свечи до 23:40,
    через ночь ничего не держит;
  * считает дневной P&L в рублях на 1 лот (шаг 0.001 = 1 ₽ на лот).

Разбиение по датам фиксировано: разработка — до 2025-12-31, валидация —
2026-01-01…2026-06-30, финальная отложенная выборка (holdout) — с 2026-07-01.
Holdout лежит в отдельном файле и открывается только на финальной проверке.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numba import njit

TICK = 0.001
RUB_PER_TICK = 1.0                      # CR: 1000 юаней, шаг 0.001 = 1 ₽ на лот
DEV_END = "2025-12-31"
VAL_START, VAL_END = "2026-01-01", "2026-06-30"
HOLDOUT_START = "2026-07-01"


@dataclass(frozen=True)
class Costs:
    fee_rub: float = 1.0                # биржа + брокер, ₽ за лот за сделку
    slip_ticks: float = 1.0             # проскальзывание, тиков за лот за сделку


@dataclass(frozen=True)
class Session:
    entry_start: int = 9 * 60 + 55      # минуты от полуночи МСК (время начала свечи)
    entry_end: int = 23 * 60 + 30
    flat_at: int = 23 * 60 + 40
    no_entry: tuple = ((13 * 60 + 58, 14 * 60 + 6), (18 * 60 + 43, 19 * 60 + 6))


def load_bars(path: str) -> pd.DataFrame:
    """CSV ISS (begin, open, high, low, close, volume, secid) -> свечи будних дней."""
    df = pd.read_csv(path)
    df["dt"] = pd.to_datetime(df["begin"])
    df = df[df["dt"].dt.weekday < 5].sort_values("dt").reset_index(drop=True)
    df["day"] = df["dt"].dt.strftime("%Y-%m-%d")
    df["tod"] = df["dt"].dt.hour * 60 + df["dt"].dt.minute
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(np.float64)
    return df


def split_dev_val(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dev = df[df["day"] <= DEV_END].reset_index(drop=True)
    val = df[(df["day"] >= VAL_START) & (df["day"] <= VAL_END)].reset_index(drop=True)
    return dev, val


@njit(cache=True)
def _simulate(open_, close, day_id, tod, target, entry_ok, flat_at, cost_per_lot, slip_px, rub_per_px):
    n = len(close)
    pos = 0.0
    entry_px = 0.0
    day_pnl = np.zeros(day_id[-1] + 1 if n else 0)
    # сделки: начало, конец (индексы свечей исполнения), сторона, чистые тики на лот
    tr_start = np.empty(n, np.int64)
    tr_end = np.empty(n, np.int64)
    tr_side = np.empty(n, np.float64)
    tr_net = np.empty(n, np.float64)
    nt = 0
    cur_start = 0
    lots_traded = 0.0
    exposure = 0
    for i in range(n):
        last_today = i == n - 1 or day_id[i + 1] != day_id[i] or tod[i + 1] >= flat_at
        if tod[i] >= flat_at:
            continue
        want = target[i]
        if last_today:
            want = 0.0
        elif abs(want) > abs(pos) or (want != 0.0 and pos != 0.0 and (want > 0) != (pos > 0)):
            if not entry_ok[i]:
                # вне окна входа: можно только сокращать/закрывать
                if pos == 0.0 or (want != 0.0 and (want > 0) != (pos > 0)):
                    want = 0.0
                else:
                    want = pos
        if pos != 0.0:
            exposure += 1
        if want == pos:
            continue
        px = close[i] if last_today else open_[i + 1]
        k = i if last_today else i + 1
        delta = want - pos
        fill = px + slip_px if delta > 0 else px - slip_px
        d = day_id[i]
        day_pnl[d] -= abs(delta) * cost_per_lot
        lots_traded += abs(delta)
        # закрытие/сокращение
        if pos != 0.0 and (delta > 0) != (pos > 0):
            closed = min(abs(delta), abs(pos))
            side = 1.0 if pos > 0 else -1.0
            day_pnl[d] += closed * side * (fill - entry_px) * rub_per_px
            if closed == abs(pos):
                tr_start[nt] = cur_start
                tr_end[nt] = k
                tr_side[nt] = side
                tr_net[nt] = side * (fill - entry_px) * rub_per_px - 2.0 * cost_per_lot
                nt += 1
        new = pos + delta
        if new != 0.0 and (pos == 0.0 or (new > 0) != (pos > 0)):
            entry_px = fill
            cur_start = k
        elif new != 0.0 and abs(new) > abs(pos):
            entry_px = (entry_px * abs(pos) + fill * (abs(new) - abs(pos))) / abs(new)
        pos = new
    return day_pnl, tr_start[:nt], tr_end[:nt], tr_side[:nt], tr_net[:nt], lots_traded, exposure


@dataclass
class Result:
    days: list[str]
    daily: np.ndarray               # ₽ на 1 лот по дням (включая дни без сделок)
    trades: pd.DataFrame            # entry, exit, side, net_ticks, minutes
    lots_traded: float
    exposure_share: float

    def metrics(self) -> dict:
        d = self.daily
        n = len(d)
        mu = float(d.mean()) if n else 0.0
        sd = float(d.std(ddof=1)) if n > 1 else 0.0
        eq = np.cumsum(d)
        dd = float((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min()) if n else 0.0
        t = self.trades
        net = t["net_ticks"].to_numpy() if len(t) else np.zeros(0)
        wins = net[net > 0]
        losses = net[net <= 0]
        traded_days = d[d != 0]
        return {
            "days": n,
            "trades": int(len(t)),
            "total_rub": float(d.sum()),
            "avg_trade_ticks": float(net.mean()) if len(net) else 0.0,
            "win_rate": float(len(wins) / len(net)) if len(net) else 0.0,
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("nan"),
            "sharpe_ann": float(mu / sd * math.sqrt(252)) if sd > 0 else 0.0,
            "t_stat": float(mu / sd * math.sqrt(n)) if sd > 0 else 0.0,
            "max_dd_rub": dd,
            "pct_days_up": float((traded_days > 0).mean()) if len(traded_days) else 0.0,
            "avg_minutes": float(t["minutes"].mean()) if len(t) else 0.0,
            "exposure": self.exposure_share,
            "skew": float(pd.Series(d).skew()) if n > 2 else 0.0,
            "kurt": float(pd.Series(d).kurt() + 3.0) if n > 3 else 3.0,
        }


def simulate(bars: pd.DataFrame, target, costs: Costs = Costs(), session: Session = Session()) -> Result:
    target = np.asarray(target, dtype=np.float64)
    if len(target) != len(bars):
        raise ValueError("target must have one value per bar")
    if not np.all(np.isfinite(target)):
        raise ValueError("target contains NaN/inf")
    tod = bars["tod"].to_numpy(np.int64)
    entry_ok = (tod >= session.entry_start) & (tod < session.entry_end)
    for a, b in session.no_entry:
        entry_ok &= ~((tod >= a) & (tod < b))
    days, day_id = np.unique(bars["day"].to_numpy(), return_inverse=True)
    cost = costs.fee_rub
    pnl, ts, te, side, net, lots, expo = _simulate(
        bars["open"].to_numpy(), bars["close"].to_numpy(), day_id.astype(np.int64), tod, target,
        entry_ok, session.flat_at, cost, costs.slip_ticks * TICK, RUB_PER_TICK / TICK)
    dt = bars["dt"].to_numpy()
    trades = pd.DataFrame({
        "entry": dt[ts], "exit": dt[te], "side": side, "net_ticks": net / RUB_PER_TICK,
        "minutes": (dt[te] - dt[ts]).astype("timedelta64[m]").astype(np.int64),
    })
    return Result(list(days), pnl, trades, float(lots), float(expo) / max(1, len(bars)))


def evaluate(bars: pd.DataFrame, fn, params: dict, costs: Costs = Costs()) -> dict:
    return simulate(bars, fn(bars, **params), costs).metrics()


def lookahead_check(fn, bars: pd.DataFrame, params: dict, cuts: int = 6) -> tuple[bool, int]:
    """Позиции на усечённых данных должны совпадать с позициями на полных."""
    full = np.asarray(fn(bars, **params), dtype=np.float64)
    n = len(bars)
    for k in np.linspace(n * 0.2, n * 0.95, cuts).astype(int):
        part = np.asarray(fn(bars.iloc[:k].reset_index(drop=True), **params), dtype=np.float64)
        bad = np.flatnonzero(part != full[:k])
        if len(bad):
            return False, int(bad[0])
    return True, -1


def _norm_ppf(p: float) -> float:
    from statistics import NormalDist

    return NormalDist().inv_cdf(p)


def deflated_sharpe(daily: np.ndarray, n_trials: int, trial_sharpes_var: float | None = None) -> float:
    """Вероятность, что истинный дневной Sharpe > 0 с поправкой на перебор
    n_trials конфигураций (Bailey & López de Prado, 2014)."""
    from statistics import NormalDist

    d = np.asarray(daily, dtype=np.float64)
    T = len(d)
    if T < 10 or d.std(ddof=1) == 0:
        return 0.0
    sr = d.mean() / d.std(ddof=1)
    s = pd.Series(d)
    skew, kurt = float(s.skew()), float(s.kurt() + 3.0)
    var_sr = trial_sharpes_var if trial_sharpes_var is not None else 1.0 / T
    g = 0.5772156649
    n = max(2, int(n_trials))
    sr0 = math.sqrt(var_sr) * ((1 - g) * _norm_ppf(1 - 1 / n) + g * _norm_ppf(1 - 1 / (n * math.e)))
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    return float(NormalDist().cdf((sr - sr0) * math.sqrt(T - 1) / denom))
