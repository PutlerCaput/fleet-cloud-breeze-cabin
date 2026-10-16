"""Событийный бэктест на ТОМ ЖЕ ядре, что и в бою (kernels.on_tick).

Лента — массивы ts/books/nbs/nas; ядро читает снимок по индексу прямо из
ленты, как из кольца в бою. Модель исполнения повторяет ExecutionEngine:
  * заявка доходит до биржи через `latency` и исполняется по стакану того
    момента (IOC: не дотянулись лимитом — 0 лотов);
  * при известной глубине крупная заявка проходит уровни (проскальзывание);
  * выход (urgent) повторяется по свежей цене, пока позиция не закрыта;
  * комиссия за лот с каждой сделки, размер позиции — как у робота
    (эквити * margin_utilization // ГО), дневной стоп по эквити,
    окна без входов, принудительное закрытие в конце сессии.

Запуск: `python -m finam_robot.backtest --fetch CRZ6` (лента MOEX ISS за
сегодня) или `--books path.npz` (стакан, записанный роботом, RECORD_BOOKS=1).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time
from typing import Iterable

import numpy as np
from numba import njit

from . import kernels as K
from .config import TZ_MOSCOW, RiskParams, risk_to_array
from .ml import MarketMlOptimizer, save_session

MSK_OFFSET = 3 * 3600  # Москва — UTC+3 круглый год


@dataclass
class Tape:
    ts: np.ndarray
    books: np.ndarray        # (N, 4, depth)
    nbs: np.ndarray
    nas: np.ndarray
    tick: float
    source: str
    depth_known: bool        # False — объёмы уровней не настоящие (прокси из ленты сделок)

    def __len__(self) -> int:
        return len(self.ts)

    def slice(self, a: int, b: int) -> "Tape":
        return replace(self, ts=self.ts[a:b], books=self.books[a:b], nbs=self.nbs[a:b], nas=self.nas[a:b])


@njit(cache=True)
def _trade_tape(ts, price, qty, side, tick, tau, max_spread, base_qty, books, nbs, nas):
    n = len(ts)
    levels = books.shape[2]
    bid = price[0] - tick if side[0] > 0 else price[0]
    ask = bid + tick
    b_flow = 0.0
    s_flow = 0.0
    last = ts[0]
    for i in range(n):
        dt = ts[i] - last
        last = ts[i]
        if dt > 0.0:
            d = math.exp(-dt / tau)
            b_flow *= d
            s_flow *= d
        p = price[i]
        if side[i] > 0:          # покупатель-агрессор бьёт в ask
            b_flow += qty[i]
            ask = p
            if bid >= ask or ask - bid > max_spread * tick + 1e-9:
                bid = ask - tick
        else:                    # продавец-агрессор бьёт в bid
            s_flow += qty[i]
            bid = p
            if ask <= bid or ask - bid > max_spread * tick + 1e-9:
                ask = bid + tick
        for k in range(levels):
            books[i, K.BID_PX, k] = bid - k * tick
            books[i, K.BID_QTY, k] = b_flow + base_qty
            books[i, K.ASK_PX, k] = ask + k * tick
            books[i, K.ASK_QTY, k] = s_flow + base_qty
        nbs[i] = levels
        nas[i] = levels


def tape_from_trades(ts, price, qty, side, tick: float, *, tau: float = 2.0, max_spread_ticks: float = 2.0,
                     base_qty: float = 1.0, levels: int = 3, source: str = "trades") -> Tape:
    """Прокси-стакан из ленты сделок: bid/ask — последние цены агрессоров
    продажи/покупки, «объёмы» — затухающий поток агрессивных покупок/продаж
    (tau секунд). Дисбаланс тогда = дисбаланс потока сделок (TFI), а не
    стакана: другой, но близкий по смыслу сигнал."""
    n = len(ts)
    books = np.zeros((n, 4, levels))
    nbs = np.zeros(n, dtype=np.int64)
    nas = np.zeros(n, dtype=np.int64)
    _trade_tape(np.ascontiguousarray(ts, dtype=np.float64), np.ascontiguousarray(price, dtype=np.float64),
                np.ascontiguousarray(qty, dtype=np.float64), np.ascontiguousarray(side, dtype=np.float64),
                tick, tau, max_spread_ticks, base_qty, books, nbs, nas)
    return Tape(np.asarray(ts, dtype=np.float64), books, nbs, nas, tick, source, depth_known=False)


def tape_from_books_npz(pattern: str, tick: float) -> Tape:
    """Стакан, записанный роботом (RECORD_BOOKS=1): файл или glob частей."""
    from .recorder import load_book_parts

    d = load_book_parts(pattern)
    return Tape(d["ts"], d["books"], d["nbs"].astype(np.int64), d["nas"].astype(np.int64), tick,
                os.path.basename(pattern), depth_known=True)


@dataclass(frozen=True)
class BtConfig:
    equity: float = 30_000.0
    margin_per_lot: float = 1_232.71     # ГО CRZ6 по ISS на 16.10.2026
    point_value: float = 1_000.0         # руб. за 1.0 цены на лот (шаг 0.001 = 1 руб.)
    fee_per_lot: float = 1.0             # руб. за лот за сделку: биржа + брокер (допущение)
    latency: float = 0.03                # с от решения ядра до биржи
    margin_utilization: float = 0.95
    max_lots: int = 1000
    max_daily_loss_pct: float = 10.0
    session_start: time = time(9, 55)
    session_stop: time = time(23, 40)
    no_entry_windows: tuple = ((time(13, 58), time(14, 6)), (time(18, 43), time(19, 6)), (time(23, 30), time(23, 59)))


@dataclass
class TradeRec:
    side: int
    qty: int
    entry_ts: float
    entry_px: float
    exit_ts: float = 0.0
    exit_px: float = 0.0
    reason: str = ""
    fees: float = 0.0
    pnl: float = 0.0          # руб., после комиссий
    cash0: float = 0.0        # деньги до входа: pnl = деньги после выхода - cash0
    exit_notional: float = 0.0
    exit_qty: int = 0


@dataclass
class BtResult:
    trades: list[TradeRec]
    equity_ts: np.ndarray
    equity: np.ndarray
    mid: np.ndarray
    start_equity: float
    end_equity: float
    fees: float
    orders: int
    ioc_misses: int
    rec_rows: np.ndarray | None = None
    params: dict = field(default_factory=dict)

    def metrics(self, tick: float) -> dict:
        pnl = np.array([t.pnl for t in self.trades])
        wins = pnl[pnl > 0]
        losses = pnl[pnl <= 0]
        ticks = np.array([t.side * (t.exit_px - t.entry_px) / tick for t in self.trades])
        eq = self.equity
        peak = np.maximum.accumulate(eq) if len(eq) else eq
        dd = ((eq - peak) / peak).min() * 100 if len(eq) else 0.0
        reasons: dict[str, int] = {}
        for t in self.trades:
            reasons[t.reason] = reasons.get(t.reason, 0) + 1
        hold = np.array([t.exit_ts - t.entry_ts for t in self.trades]) if self.trades else np.array([0.0])
        gross = float((pnl + np.array([t.fees for t in self.trades])).sum()) if self.trades else 0.0
        return {
            "net_pnl": self.end_equity - self.start_equity,
            "return_pct": (self.end_equity / self.start_equity - 1) * 100,
            "gross_pnl": gross,
            "fees": self.fees,
            "trades": len(self.trades),
            "win_rate": float(len(wins) / len(pnl)) if len(pnl) else 0.0,
            "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "profit_factor": (float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0
                              else float("inf") if len(wins) else 0.0),
            "avg_ticks": float(ticks.mean()) if len(ticks) else 0.0,
            "max_dd_pct": float(dd),
            "avg_hold_s": float(hold.mean()),
            "orders": self.orders,
            "ioc_misses": self.ioc_misses,
            "exit_reasons": reasons,
        }


def _seconds_of_day(ts: np.ndarray) -> np.ndarray:
    return (ts + MSK_OFFSET) % 86400.0


def _sod(t: time) -> float:
    return t.hour * 3600 + t.minute * 60 + t.second


def _fill(book, side: int, qty: int, limit: float, depth_known: bool) -> tuple[int, float]:
    """IOC по снимку: (исполнено лотов, средняя цена)."""
    px_row, qty_row = (K.ASK_PX, K.ASK_QTY) if side > 0 else (K.BID_PX, K.BID_QTY)
    touch = book[px_row, 0]
    if limit > 0 and ((side > 0 and limit < touch - 1e-9) or (side < 0 and limit > touch + 1e-9)):
        return 0, 0.0
    if not depth_known:
        return qty, float(touch)
    left = qty
    notional = 0.0
    for k in range(book.shape[1]):
        p = book[px_row, k]
        if p <= 0 or (limit > 0 and ((side > 0 and p > limit + 1e-9) or (side < 0 and p < limit - 1e-9))):
            break
        take = min(left, int(book[qty_row, k]))
        notional += take * p
        left -= take
        if left == 0:
            break
    done = qty - left
    return done, (notional / done if done else 0.0)


def run_backtest(tape: Tape, cfg: BtConfig, risk: RiskParams, weights: dict | None = None,
                 *, record: bool = False, sample_every: float = 10.0) -> BtResult:
    P = risk_to_array(risk)
    P[K.P_TICK] = tape.tick
    S = K.new_state()
    out = K.new_out()
    W = K.weights_array(weights)
    rec = np.zeros((1 << 19, K.REC_COLS)) if record else np.zeros((4, K.REC_COLS))
    tick = tape.tick
    slip = P[K.P_EXIT_SLIP] * tick

    sod = _seconds_of_day(tape.ts)
    start_s, stop_s = _sod(cfg.session_start), _sod(cfg.session_stop)
    in_session = (sod >= start_s) & (sod < stop_s)
    no_entry = np.zeros(len(tape), dtype=bool)
    for a, b in cfg.no_entry_windows:
        no_entry |= (sod >= _sod(a)) & (sod < _sod(b))
    allow_arr = (in_session & ~no_entry).astype(np.int64)

    cash = cfg.equity
    start_eq = cfg.equity
    pos = 0
    avg = 0.0
    fees = 0.0
    orders = misses = 0
    loss_hit = False
    order = None   # [arrival_ts, target, limit, urgent, reason]
    trades: list[TradeRec] = []
    cur: TradeRec | None = None
    eq_ts, eq_v, eq_mid = [], [], []
    next_sample = tape.ts[0] if len(tape) else 0.0
    next_risk_check = next_sample

    def lots(eq: float) -> int:
        return min(cfg.max_lots, int(eq * cfg.margin_utilization // cfg.margin_per_lot))

    books, nbs, nas, tss = tape.books, tape.nbs, tape.nas, tape.ts
    n = len(tape)
    for i in range(n):
        t = tss[i]
        book = books[i]
        bid = book[K.BID_PX, 0]
        ask = book[K.ASK_PX, 0]

        # 1. заявка дошла до биржи
        if order is not None and t >= order[0]:
            target, limit, urgent, reason = order[1], order[2], order[3], order[4]
            delta = target - pos
            filled, px = (0, 0.0)
            if delta != 0:
                orders += 1
                side = 1 if delta > 0 else -1
                filled, px = _fill(book, side, abs(delta), limit, tape.depth_known)
                if filled:
                    signed = side * filled
                    fee = cfg.fee_per_lot * filled
                    if pos == 0:
                        cur = TradeRec(side=side, qty=filled, entry_ts=t, entry_px=px, cash0=cash)
                    elif cur is not None and (pos > 0) != (signed > 0):
                        cur.exit_notional += px * filled
                        cur.exit_qty += filled
                    fees += fee
                    cash -= fee
                    if pos != 0 and (pos > 0) != (signed > 0):
                        closed = min(abs(pos), filled)
                        cash += closed * (px - avg) * cfg.point_value * (1 if pos > 0 else -1)
                    new = pos + signed
                    if new == 0:
                        avg = 0.0
                    elif pos == 0:
                        avg = px
                    elif abs(new) > abs(pos):
                        avg = (avg * abs(pos) + px * filled) / abs(new)
                    if cur is not None:
                        cur.fees += fee
                        if abs(new) > abs(pos):
                            cur.qty, cur.entry_px = abs(new), avg
                    pos = new
                else:
                    misses += 1
            if pos == target:
                if pos == 0 and cur is not None:
                    cur.exit_ts, cur.reason = t, reason
                    cur.exit_px = cur.exit_notional / cur.exit_qty if cur.exit_qty else px
                    cur.pnl = cash - cur.cash0
                    trades.append(cur)
                    cur = None
                    K.on_fill(P, S, 0, float(px), float(t))
                elif pos != 0:
                    K.on_fill(P, S, 1 if pos > 0 else -1, float(avg), float(t))
                else:
                    K.on_reject(S)
                order = None
            elif urgent:
                # как ExecutionEngine: повтор по свежей котировке
                order = [t + cfg.latency, target, (bid - slip) if pos > 0 else (ask + slip), True, reason]
            else:
                if pos != 0 and int(S[K.S_SIDE]) == 0:
                    K.on_fill(P, S, 1 if pos > 0 else -1, float(avg), float(t))
                else:
                    K.on_reject(S)
                order = None

        # 2. эквити, дневной стоп
        if t >= next_risk_check or i == n - 1:
            mark = bid if pos > 0 else ask
            eq = cash + (pos * (mark - avg) * cfg.point_value if pos else 0.0)
            if t >= next_sample or i == n - 1:
                eq_ts.append(t)
                eq_v.append(eq)
                eq_mid.append(0.5 * (bid + ask))
                next_sample = t + sample_every
            if not loss_hit and (eq / start_eq - 1) * 100 <= -cfg.max_daily_loss_pct:
                loss_hit = True
            next_risk_check = t + 1.0

        # 3. принудительное закрытие: конец сессии / конец ленты / дневной стоп
        must_flat = (not in_session[i] and sod[i] >= stop_s) or loss_hit or i >= n - 2
        if must_flat and pos != 0 and order is None:
            S[K.S_PENDING] = 1.0
            limit = (bid - slip) if pos > 0 else (ask + slip)
            reason = "daily_loss" if loss_hit else "session_end"
            if i >= n - 2:  # ленты больше нет — закрываем по текущему стакану
                order = [t, 0, limit, True, reason]
            else:
                order = [t + cfg.latency, 0, limit, True, reason]

        # 4. ядро
        allow = 0 if (loss_hit or must_flat) else int(allow_arr[i])
        act = K.on_tick(books, nbs, nas, tss, i, P, S, W, allow, out, rec)
        if act and order is None:
            r = K.REASON_NAMES.get(int(out[K.O_REASON]), "?")
            if act == K.ACT_EXIT:
                order = [t + cfg.latency, 0, float(out[K.O_PRICE]), True, r]
            else:
                eq_now = cash
                q = lots(eq_now)
                if q == 0:
                    K.on_reject(S)
                else:
                    order = [t + cfg.latency, q if act == K.ACT_BUY else -q, float(out[K.O_PRICE]), False, r]

    # хвост: если лента кончилась с открытой позицией — закрыть по последнему стакану
    if pos != 0 and n:
        book = books[n - 1]
        px = book[K.BID_PX, 0] if pos > 0 else book[K.ASK_PX, 0]
        fee = cfg.fee_per_lot * abs(pos)
        fees += fee
        cash += pos * (px - avg) * cfg.point_value - fee
        if cur is not None:
            cur.fees += fee
            cur.exit_notional += px * abs(pos)
            cur.exit_qty += abs(pos)
            cur.exit_ts, cur.reason = tss[n - 1], "end_of_data"
            cur.exit_px = cur.exit_notional / cur.exit_qty
            cur.pnl = cash - cur.cash0
            trades.append(cur)
        pos = 0
    if eq_v:
        eq_v[-1] = cash

    rows = None
    if record:
        nrec = int(S[K.S_REC_N])
        rows = rec[: min(nrec, rec.shape[0])].copy()
    return BtResult(trades, np.array(eq_ts), np.array(eq_v), np.array(eq_mid), start_eq, cash, fees,
                    orders, misses, rows, {"latency": cfg.latency, "fee_per_lot": cfg.fee_per_lot})


STATIC_RISK = dict(  # исходные trailing_distance=5, trailing_activation=15, hard_stop_loss=10 (тиков)
    k_vol=0.0, k_spread=0.0, k_spread_vol=0.0, k_obi_var=0.0, k_obi_dir=0.0,
    trail_min=5.0, trail_max=5.0, fee_ticks=0.0, act_mult=3.0,
    hard_k=0.0, hard_min=10.0, hard_max=10.0, shock_mult=1e9,
)


def block_permute_flow(tape: Tape, block_s: float, rng: np.random.Generator) -> Tape:
    """Нулевая гипотеза: та же цена, но поток объёмов перемешан блоками —
    у дисбаланса не остаётся связи с будущей ценой."""
    bucket = np.floor((tape.ts - tape.ts[0]) / block_s).astype(np.int64)
    edges = np.flatnonzero(np.diff(bucket)) + 1
    blocks = np.split(np.arange(len(tape)), edges)
    order = rng.permutation(len(blocks))
    idx = np.concatenate([blocks[k] for k in order])
    books = tape.books.copy()
    books[:, K.BID_QTY, :] = tape.books[idx, K.BID_QTY, :]
    books[:, K.ASK_QTY, :] = tape.books[idx, K.ASK_QTY, :]
    return replace(tape, books=books)


def bootstrap_trades(trades: list[TradeRec], equity: float, n: int = 5000, seed: int = 1) -> dict:
    if not trades:
        return {}
    pnl = np.array([t.pnl for t in trades])
    rng = np.random.default_rng(seed)
    samples = rng.choice(pnl, size=(n, len(pnl)), replace=True)
    curves = equity + np.cumsum(samples, axis=1)
    finals = (curves[:, -1] / equity - 1) * 100
    peaks = np.maximum.accumulate(np.concatenate([np.full((n, 1), equity), curves], axis=1), axis=1)[:, 1:]
    dds = ((curves - peaks) / peaks).min(axis=1) * 100
    return {
        "p05_return": float(np.percentile(finals, 5)),
        "p50_return": float(np.percentile(finals, 50)),
        "p95_return": float(np.percentile(finals, 95)),
        "prob_loss": float((finals < 0).mean()),
        "p05_max_dd": float(np.percentile(dds, 5)),
        "p50_max_dd": float(np.percentile(dds, 50)),
    }


def _msk(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ_MOSCOW).strftime("%H:%M:%S")


def run_report(tape: Tape, cfg: BtConfig, *, out_dir: str, null_runs: int = 40, seed: int = 7,
               info: dict | None = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    K.warmup()
    dyn_risk = RiskParams()
    static_risk = RiskParams(**STATIC_RISK)

    dyn = run_backtest(tape, cfg, dyn_risk, record=True)
    sta = run_backtest(tape, cfg, static_risk)
    m_dyn, m_sta = dyn.metrics(tape.tick), sta.metrics(tape.tick)

    sens = []
    for lat in (0.005, 0.03, 0.1, 0.3):
        for fee in (0.0, 0.5, 1.0, 2.0):
            r = run_backtest(tape, replace(cfg, latency=lat, fee_per_lot=fee), dyn_risk)
            m = r.metrics(tape.tick)
            sens.append({"latency_ms": lat * 1000, "fee": fee, "return_pct": m["return_pct"],
                         "trades": m["trades"], "win_rate": m["win_rate"]})
    thr = []
    for pb in (0.65, 0.75, 0.85, 0.95):
        r = run_backtest(tape, cfg, replace(dyn_risk, p_buy=pb, p_sell=1 - pb))
        m = r.metrics(tape.tick)
        thr.append({"p_buy": pb, "return_pct": m["return_pct"], "trades": m["trades"],
                    "win_rate": m["win_rate"], "gross_pnl": m["gross_pnl"], "fees": m["fees"]})

    rng = np.random.default_rng(seed)
    null = []
    for _ in range(null_runs):
        r = run_backtest(block_permute_flow(tape, 60.0, rng), cfg, dyn_risk)
        null.append(r.metrics(tape.tick)["return_pct"])
    null_arr = np.array(null)
    p_value = float((null_arr >= m_dyn["return_pct"]).mean()) if null_runs else float("nan")

    # ML walk-forward: учим на первой половине сессии, проверяем на второй
    ml = {}
    sod = _seconds_of_day(tape.ts)
    split_t = None
    sess = np.flatnonzero((sod >= _sod(cfg.session_start)) & (sod < _sod(cfg.session_stop)))
    if len(sess) > 1000 and dyn.rec_rows is not None:
        split_t = tape.ts[sess[0]] + (tape.ts[sess[-1]] - tape.ts[sess[0]]) / 2
        rows = dyn.rec_rows[dyn.rec_rows[:, K.REC_TS] < split_t]
        with tempfile.TemporaryDirectory() as d:
            save_session(rows, len(rows), d, datetime.fromtimestamp(split_t, TZ_MOSCOW))
            res = MarketMlOptimizer(d, rec_interval=dyn_risk.rec_interval).train_and_optimize()
        split_i = int(np.searchsorted(tape.ts, split_t))
        warm = max(0, split_i - 20_000)
        second = tape.slice(warm, len(tape))
        cfg2 = replace(cfg, session_start=datetime.fromtimestamp(split_t, TZ_MOSCOW).time())
        base2 = run_backtest(second, cfg2, dyn_risk).metrics(tape.tick)
        ml = {"split": _msk(split_t), "train": {k: v for k, v in res.items() if k != "trained_at"},
              "oos_default_weights": base2}
        if res.get("status") == "APPROVED":
            ml["oos_trained_weights"] = run_backtest(second, cfg2, dyn_risk, weights=res).metrics(tape.tick)

    report = {
        "info": info or {},
        "tape": {"source": tape.source, "events": len(tape), "from": _msk(tape.ts[0]), "to": _msk(tape.ts[-1]),
                 "tick": tape.tick, "depth_known": tape.depth_known},
        "config": {k: (str(v) if isinstance(v, (time, tuple)) else v) for k, v in asdict(cfg).items()},
        "dynamic": m_dyn,
        "static": m_sta,
        "bootstrap_dynamic": bootstrap_trades(dyn.trades, cfg.equity),
        "sensitivity": sens,
        "thresholds": thr,
        "null": {"runs": null_runs, "block_s": 60, "returns": null, "p_value": p_value,
                 "mean": float(null_arr.mean()) if null_runs else None},
        "ml_walk_forward": ml,
        "equity": {
            "ts": [_msk(t) for t in dyn.equity_ts],
            "dynamic": dyn.equity.round(2).tolist(),
            "static": (np.interp(dyn.equity_ts, sta.equity_ts, sta.equity).round(2).tolist()
                       if len(sta.equity_ts) else []),
            "mid": dyn.mid.round(4).tolist(),
        },
    }
    with open(os.path.join(out_dir, "backtest_report.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=float)
    for name, res in (("dynamic", dyn), ("static", sta)):
        with open(os.path.join(out_dir, f"trades_{name}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["side", "qty", "entry_time", "entry_px", "exit_time", "exit_px", "ticks", "reason",
                        "fees_rub", "pnl_rub"])
            for t in res.trades:
                w.writerow([t.side, t.qty, _msk(t.entry_ts), t.entry_px, _msk(t.exit_ts), t.exit_px,
                            round(t.side * (t.exit_px - t.entry_px) / tape.tick, 1), t.reason,
                            round(t.fees, 2), round(t.pnl, 2)])
    return report


def _print(report: dict) -> None:
    for name in ("dynamic", "static"):
        m = report[name]
        print(f"{name:8s} доход {m['return_pct']:+7.2f}%  сделок {m['trades']:4d}  win {m['win_rate']:.2f}  "
              f"PF {m['profit_factor']:.2f}  DD {m['max_dd_pct']:.2f}%  комиссии {m['fees']:.0f}  "
              f"gross {m['gross_pnl']:.0f}")
    nl = report["null"]
    print(f"нулевая гипотеза: средний доход {nl['mean']:+.2f}%, p-value {nl['p_value']:.3f}")


def main(argv: Iterable[str] | None = None) -> None:
    from .moex_iss import fetch_security, fetch_trades, load_trades

    ap = argparse.ArgumentParser(prog="finam_robot.backtest")
    ap.add_argument("--fetch", metavar="SECID", help="скачать ленту текущей сессии с MOEX ISS")
    ap.add_argument("--trades", help="готовый CSV.gz ленты ISS")
    ap.add_argument("--books", help="npz стакана, записанный роботом (RECORD_BOOKS=1)")
    ap.add_argument("--tick", type=float, default=0.001)
    ap.add_argument("--out", default="backtest_out")
    ap.add_argument("--null-runs", type=int, default=40)
    args = ap.parse_args(argv)

    info = {}
    cfg = BtConfig()
    if args.fetch:
        sec = fetch_security(args.fetch)
        info["security"] = sec
        cfg = replace(cfg, margin_per_lot=float(sec["INITIALMARGIN"]),
                      point_value=float(sec["STEPPRICE"]) / float(sec["MINSTEP"]))
        args.tick = float(sec["MINSTEP"])
        args.trades = fetch_trades(args.fetch, os.path.join(args.out, "data"))
    if args.books:
        tape = tape_from_books_npz(args.books, args.tick)
    elif args.trades:
        ts, price, qty, side, _ = load_trades(args.trades)
        tape = tape_from_trades(ts, price, qty, side, args.tick, source=os.path.basename(args.trades))
        info["trades_in_tape"] = int(len(ts))
    else:
        ap.error("нужен --fetch, --trades или --books")
    report = run_report(tape, cfg, out_dir=args.out, null_runs=args.null_runs, info=info)
    _print(report)


if __name__ == "__main__":
    main()
