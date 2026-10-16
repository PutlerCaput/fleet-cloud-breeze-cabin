"""Вычислительное ядро на Numba: признаки стакана, сигнал, динамический риск-движок.

Один вызов `on_tick` на одно обновление стакана. Ядро читает снимок прямо из
кольцевого буфера (без копий и без создания view), обновляет состояние в
преаллоцированном массиве `S` и возвращает код действия (малый int — CPython
отдаёт его из кэша, то есть граница Python/Numba тоже без аллокаций).

Все цены внутри — в рублях, все расстояния — в тиках.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit

# fastmath без nnan/ninf: векторизация и FMA остаются, а проверки на NaN
# (битые котировки) не вырезаются оптимизатором.
JIT = dict(
    nogil=True,
    cache=True,
    error_model="numpy",
    boundscheck=False,
    fastmath={"nsz", "arcp", "contract", "afn", "reassoc"},
)

# --- раскладка снимка стакана: books[slot, ROW, level] ---
BID_PX, BID_QTY, ASK_PX, ASK_QTY = 0, 1, 2, 3

# --- параметры (массив P), порядок совпадает с config.RiskParams ---
P_TICK = 0
P_LEVELS = 1
P_P_BUY = 2
P_P_SELL = 3
P_TAU_VOL = 4
P_TAU_FAST = 5
P_HORIZON = 6
P_K_VOL = 7
P_K_SPREAD = 8
P_K_SPREAD_VOL = 9
P_K_OBI_VAR = 10
P_K_OBI_DIR = 11
P_TRAIL_MIN = 12
P_TRAIL_MAX = 13
P_ACT_MULT = 14
P_FEE_TICKS = 15
P_HARD_K = 16
P_HARD_MIN = 17
P_HARD_MAX = 18
P_SHOCK_MULT = 19
P_MAX_DEFER = 20
P_CAT_TICKS = 21
P_MAX_ENTRY_SPREAD = 22
P_WARMUP_N = 23
P_EXIT_SLIP = 24
P_ENTRY_SLIP = 25
P_COOLDOWN = 26
P_EXIT_ON_REVERSAL = 27
P_REC_INTERVAL = 28
P_COUNT = 29

PARAM_INDEX = {
    "tick": P_TICK,
    "levels": P_LEVELS,
    "p_buy": P_P_BUY,
    "p_sell": P_P_SELL,
    "tau_vol": P_TAU_VOL,
    "tau_fast": P_TAU_FAST,
    "horizon": P_HORIZON,
    "k_vol": P_K_VOL,
    "k_spread": P_K_SPREAD,
    "k_spread_vol": P_K_SPREAD_VOL,
    "k_obi_var": P_K_OBI_VAR,
    "k_obi_dir": P_K_OBI_DIR,
    "trail_min": P_TRAIL_MIN,
    "trail_max": P_TRAIL_MAX,
    "act_mult": P_ACT_MULT,
    "fee_ticks": P_FEE_TICKS,
    "hard_k": P_HARD_K,
    "hard_min": P_HARD_MIN,
    "hard_max": P_HARD_MAX,
    "shock_mult": P_SHOCK_MULT,
    "max_defer": P_MAX_DEFER,
    "cat_ticks": P_CAT_TICKS,
    "max_entry_spread": P_MAX_ENTRY_SPREAD,
    "warmup_n": P_WARMUP_N,
    "exit_slip": P_EXIT_SLIP,
    "entry_slip": P_ENTRY_SLIP,
    "cooldown": P_COOLDOWN,
    "exit_on_reversal": P_EXIT_ON_REVERSAL,
    "rec_interval": P_REC_INTERVAL,
}

# --- состояние (массив S) ---
S_N = 0              # число принятых обновлений
S_FIRST_TS = 1
S_LAST_TS = 2
S_LAST_MID = 3
S_RV = 4             # экспоненциально затухающая сумма dmid^2, тики^2
S_SPR_M = 5          # EWMA спреда, тики
S_SPR_M2 = 6         # EWMA спреда^2
S_OBI_M = 7          # EWMA дисбаланса
S_OBI_M2 = 8
S_SIDE = 9           # +1 лонг, -1 шорт, 0 вне рынка
S_ENTRY = 10
S_EXTREME = 11       # лучший bid (лонг) / лучший ask (шорт) с момента входа
S_STOP = 12          # текущий стоп; только подтягивается, никогда не ослабляется
S_TRAIL_ON = 13
S_HARD_STOP = 14
S_DEFER_TS = 15      # начало ожидания выхода при шоке спреда
S_PENDING = 16       # 1 — заявка в пути, новые действия запрещены
S_LAST_EXIT_TS = 17
S_DIST = 18          # последняя дистанция трейлинга, тики
S_SIGMA = 19         # sigma(horizon), тики
S_PROB = 20          # P(up) модели
S_IMB = 21
S_MICRO = 22
S_MID = 23
S_SPREAD = 24        # тики
S_BIAS = 25          # (micro - mid) / tick
S_SHOCK = 26
S_BID = 27
S_ASK = 28
S_REC_N = 29
S_REC_LAST_TS = 30
S_COUNT = 31

# --- выход ядра (массив out) ---
O_PRICE = 0          # лимитная цена заявки
O_REASON = 1
O_COUNT = 2

# --- коды действий ---
ACT_NONE = 0
ACT_BUY = 1
ACT_SELL = -1
ACT_EXIT = 2

# --- причины ---
R_NONE = 0
R_SIGNAL = 1
R_HARD = 2
R_TRAIL = 3
R_REVERSAL = 4
R_CATASTROPHE = 5
R_DEFER_TIMEOUT = 6
REASON_NAMES = {
    R_NONE: "none",
    R_SIGNAL: "signal",
    R_HARD: "hard_stop",
    R_TRAIL: "trailing",
    R_REVERSAL: "reversal",
    R_CATASTROPHE: "catastrophe",
    R_DEFER_TIMEOUT: "spread_shock_timeout",
}

# --- запись признаков для ML: rec[i] = ts, imb, bias, mid, spread ---
REC_TS, REC_IMB, REC_BIAS, REC_MID, REC_SPREAD = 0, 1, 2, 3, 4
REC_COLS = 5


@njit(**JIT)
def update_features(books, idx, nb, na, ts, P, S):
    """Признаки снимка + волатильностное состояние. False — снимок непригоден."""
    if nb <= 0 or na <= 0:
        return False
    bid = books[idx, BID_PX, 0]
    ask = books[idx, ASK_PX, 0]
    # Пересечённый/пустой стакан или NaN: сравнения с NaN дают False.
    if not (bid > 0.0 and ask > bid):
        return False

    tick = P[P_TICK]
    levels = int(P[P_LEVELS])
    if levels > nb:
        levels = nb
    if levels > na:
        levels = na
    bv = 0.0
    av = 0.0
    for k in range(levels):
        bv += books[idx, BID_QTY, k]
        av += books[idx, ASK_QTY, k]
    tot = bv + av
    if not (tot > 0.0):
        return False

    imb = (bv - av) / tot
    mid = 0.5 * (bid + ask)                 # было (bid * ask) / 2 — см. README
    micro = (bid * av + ask * bv) / tot
    spread_t = (ask - bid) / tick
    bias_t = (micro - mid) / tick

    S[S_IMB] = imb
    S[S_MICRO] = micro
    S[S_MID] = mid
    S[S_SPREAD] = spread_t
    S[S_BIAS] = bias_t
    S[S_BID] = bid
    S[S_ASK] = ask

    if S[S_N] == 0.0:
        S[S_FIRST_TS] = ts
        S[S_LAST_TS] = ts
        S[S_LAST_MID] = mid
        S[S_RV] = 0.0
        S[S_SPR_M] = spread_t
        S[S_SPR_M2] = spread_t * spread_t
        S[S_OBI_M] = imb
        S[S_OBI_M2] = imb * imb
        S[S_SHOCK] = 0.0
    else:
        spr_prev = S[S_SPR_M]
        # Шок спреда меряем против EWMA ДО обновления, иначе он сам себя гасит.
        if spread_t >= P[P_SHOCK_MULT] * spr_prev and spread_t - spr_prev >= 2.0:
            S[S_SHOCK] = 1.0
        else:
            S[S_SHOCK] = 0.0

        dt = ts - S[S_LAST_TS]
        if dt < 0.0:
            dt = 0.0
        # Временное (а не потиковое) затухание: пачка из 1000 апдейтов за 1 мс
        # не «съедает» историю, а редкие апдейты не завышают вес одного тика.
        dm = (mid - S[S_LAST_MID]) / tick
        S[S_RV] = S[S_RV] * math.exp(-dt / P[P_TAU_VOL]) + dm * dm
        a = 1.0 - math.exp(-dt / P[P_TAU_FAST])
        S[S_SPR_M] += a * (spread_t - S[S_SPR_M])
        S[S_SPR_M2] += a * (spread_t * spread_t - S[S_SPR_M2])
        S[S_OBI_M] += a * (imb - S[S_OBI_M])
        S[S_OBI_M2] += a * (imb * imb - S[S_OBI_M2])
        S[S_LAST_TS] = ts
        S[S_LAST_MID] = mid
    S[S_N] += 1.0
    return True


@njit(**JIT)
def trail_distance(P, S, side):
    """Дистанция трейлинга в тиках из волатильности mid, спреда и дисперсии OBI.

    dist = (k_vol*sigma_H + k_spread*E[spread] + k_spread_vol*std(spread))
           * (1 + k_obi_var*std(OBI))
           * clamp(1 + k_obi_dir*side*E[OBI], 0.5, 1.5)

    sigma_H = sqrt(RV/tau_vol * horizon): RV — затухающая сумма квадратов
    приращений mid, RV/tau — дисперсия в секунду.
    """
    var_rate = S[S_RV] / P[P_TAU_VOL]
    if var_rate < 0.0:
        var_rate = 0.0
    sigma_h = math.sqrt(var_rate * P[P_HORIZON])
    spr_var = S[S_SPR_M2] - S[S_SPR_M] * S[S_SPR_M]
    obi_var = S[S_OBI_M2] - S[S_OBI_M] * S[S_OBI_M]
    spr_std = math.sqrt(spr_var) if spr_var > 0.0 else 0.0
    obi_std = math.sqrt(obi_var) if obi_var > 0.0 else 0.0

    d = P[P_K_VOL] * sigma_h + P[P_K_SPREAD] * S[S_SPR_M] + P[P_K_SPREAD_VOL] * spr_std
    d *= 1.0 + P[P_K_OBI_VAR] * obi_std
    if side != 0:
        # Стакан за позицию — даём «бегуну» больше места; против — подтягиваем.
        m = 1.0 + P[P_K_OBI_DIR] * side * S[S_OBI_M]
        if m < 0.5:
            m = 0.5
        elif m > 1.5:
            m = 1.5
        d *= m
    d = math.ceil(d)
    if d < P[P_TRAIL_MIN]:
        d = P[P_TRAIL_MIN]
    elif d > P[P_TRAIL_MAX]:
        d = P[P_TRAIL_MAX]
    S[S_SIGMA] = sigma_h
    return d


@njit(**JIT)
def on_tick(books, nbs, nas, tss, idx, P, S, W, allow_entry, out, rec):
    """Полный цикл на одно обновление: признаки -> модель -> риск -> действие.

    W = [w_imbalance, w_bias, intercept]. Возвращает ACT_*; цена и причина — в out.
    """
    out[O_PRICE] = 0.0
    out[O_REASON] = 0.0
    ts = tss[idx]
    if not update_features(books, idx, nbs[idx], nas[idx], ts, P, S):
        return ACT_NONE

    if ts - S[S_REC_LAST_TS] >= P[P_REC_INTERVAL]:
        r = int(S[S_REC_N]) % rec.shape[0]
        rec[r, REC_TS] = ts
        rec[r, REC_IMB] = S[S_IMB]
        rec[r, REC_BIAS] = S[S_BIAS]
        rec[r, REC_MID] = S[S_MID]
        rec[r, REC_SPREAD] = S[S_SPREAD]
        S[S_REC_N] += 1.0
        S[S_REC_LAST_TS] = ts

    logit = S[S_IMB] * W[0] + S[S_BIAS] * W[1] + W[2]
    p_up = 1.0 / (1.0 + math.exp(-logit))
    S[S_PROB] = p_up

    side = int(S[S_SIDE])
    dist = trail_distance(P, S, side)
    S[S_DIST] = dist
    tick = P[P_TICK]
    bid = S[S_BID]
    ask = S[S_ASK]

    if side != 0:
        if S[S_PENDING] != 0.0:
            return ACT_NONE
        # Стоп оцениваем по цене, по которой реально выйдем (bid для лонга,
        # ask для шорта), а не по mid/last — иначе стоп «врёт» на полспреда.
        if side > 0:
            px = bid
            if bid > S[S_EXTREME]:
                S[S_EXTREME] = bid
        else:
            px = ask
            if ask < S[S_EXTREME]:
                S[S_EXTREME] = ask

        profit_t = side * (px - S[S_ENTRY]) / tick
        if S[S_TRAIL_ON] == 0.0 and profit_t >= P[P_FEE_TICKS] + P[P_ACT_MULT] * dist:
            S[S_TRAIL_ON] = 1.0
        if S[S_TRAIL_ON] != 0.0:
            cand = S[S_EXTREME] - side * dist * tick
            if side > 0:
                if cand > S[S_STOP]:
                    S[S_STOP] = cand
            elif cand < S[S_STOP]:
                S[S_STOP] = cand

        stop = S[S_STOP]
        if side > 0:
            hit = bid <= stop
        else:
            hit = ask >= stop
        reason = R_NONE
        if hit:
            reason = R_TRAIL if S[S_TRAIL_ON] != 0.0 else R_HARD
            if S[S_SHOCK] != 0.0:
                # Спред взорвался: рыночный выход сейчас = худшее проскальзывание.
                # Ждём до max_defer, если цена не ушла за «катастрофический» уровень.
                if side > 0:
                    cat = bid <= stop - P[P_CAT_TICKS] * tick
                else:
                    cat = ask >= stop + P[P_CAT_TICKS] * tick
                if cat:
                    reason = R_CATASTROPHE
                else:
                    if S[S_DEFER_TS] == 0.0:
                        S[S_DEFER_TS] = ts
                        return ACT_NONE
                    if ts - S[S_DEFER_TS] < P[P_MAX_DEFER]:
                        return ACT_NONE
                    reason = R_DEFER_TIMEOUT
        else:
            S[S_DEFER_TS] = 0.0
            if P[P_EXIT_ON_REVERSAL] != 0.0:
                if (side > 0 and p_up < P[P_P_SELL]) or (side < 0 and p_up > P[P_P_BUY]):
                    reason = R_REVERSAL

        if reason != R_NONE:
            S[S_DEFER_TS] = 0.0
            S[S_PENDING] = 1.0
            out[O_PRICE] = px - side * P[P_EXIT_SLIP] * tick
            out[O_REASON] = reason
            return ACT_EXIT
        return ACT_NONE

    # --- вне позиции: вход ---
    if S[S_PENDING] != 0.0 or allow_entry == 0:
        return ACT_NONE
    if S[S_N] < P[P_WARMUP_N] or ts - S[S_FIRST_TS] < P[P_TAU_VOL]:
        return ACT_NONE
    if S[S_SHOCK] != 0.0 or S[S_SPREAD] > P[P_MAX_ENTRY_SPREAD]:
        return ACT_NONE
    if ts - S[S_LAST_EXIT_TS] < P[P_COOLDOWN]:
        return ACT_NONE
    if p_up > P[P_P_BUY]:
        S[S_PENDING] = 1.0
        out[O_PRICE] = ask + P[P_ENTRY_SLIP] * tick
        out[O_REASON] = R_SIGNAL
        return ACT_BUY
    if p_up < P[P_P_SELL]:
        S[S_PENDING] = 1.0
        out[O_PRICE] = bid - P[P_ENTRY_SLIP] * tick
        out[O_REASON] = R_SIGNAL
        return ACT_SELL
    return ACT_NONE


@njit(**JIT)
def on_fill(P, S, side, price, ts):
    """Позиция изменилась: side = новый знак позиции, price = средняя цена входа."""
    S[S_PENDING] = 0.0
    S[S_DEFER_TS] = 0.0
    S[S_TRAIL_ON] = 0.0
    if side == 0:
        S[S_SIDE] = 0.0
        S[S_LAST_EXIT_TS] = ts
        return
    tick = P[P_TICK]
    dist = S[S_DIST] if S[S_DIST] > 0.0 else P[P_TRAIL_MIN]
    hard = P[P_HARD_K] * dist
    if hard < P[P_HARD_MIN]:
        hard = P[P_HARD_MIN]
    elif hard > P[P_HARD_MAX]:
        hard = P[P_HARD_MAX]
    S[S_SIDE] = float(side)
    S[S_ENTRY] = price
    S[S_EXTREME] = price
    # Жёсткий стоп фиксируется на входе по волатильности того момента.
    S[S_HARD_STOP] = price - side * hard * tick
    S[S_STOP] = S[S_HARD_STOP]


@njit(**JIT)
def on_price_fix(S, price):
    """Уточнение цены входа по фактическим сделкам (пока трейлинг не включён)."""
    if S[S_SIDE] == 0.0 or S[S_TRAIL_ON] != 0.0:
        return
    shift = price - S[S_ENTRY]
    S[S_ENTRY] = price
    S[S_HARD_STOP] += shift
    S[S_STOP] = S[S_HARD_STOP]
    if S[S_SIDE] > 0.0:
        if price > S[S_EXTREME]:
            S[S_EXTREME] = price
    elif price < S[S_EXTREME]:
        S[S_EXTREME] = price


@njit(**JIT)
def on_reject(S):
    S[S_PENDING] = 0.0


@njit(**JIT)
def calculate_hft_signals(bid_prices, bid_volumes, ask_prices, ask_volumes, ml_weights, ml_intercept):
    """Совместимость со старой сигнатурой (исправлен mid). Возвращает кортеж —
    это аллокация на каждом вызове; в боевом цикле используйте on_tick."""
    total_bid_vol = bid_volumes[0] + bid_volumes[1] + bid_volumes[2]
    total_ask_vol = ask_volumes[0] + ask_volumes[1] + ask_volumes[2]
    tot = total_bid_vol + total_ask_vol + 1e-8
    imbalance = (total_bid_vol - total_ask_vol) / tot
    micro_price = (bid_prices[0] * total_ask_vol + ask_prices[0] * total_bid_vol) / tot
    mid_price = (bid_prices[0] + ask_prices[0]) / 2.0
    price_spread_bias = micro_price - mid_price
    logit_score = imbalance * ml_weights[0, 0] + price_spread_bias * ml_weights[0, 1] + ml_intercept
    probability_up = 1.0 / (1.0 + math.exp(-logit_score))
    signal = 0
    if probability_up > 0.65:
        signal = 1
    elif probability_up < 0.35:
        signal = -1
    return signal, imbalance, micro_price


def new_state() -> np.ndarray:
    return np.zeros(S_COUNT, dtype=np.float64)


def new_out() -> np.ndarray:
    return np.zeros(O_COUNT, dtype=np.float64)


def weights_array(ml_weights: dict | None) -> np.ndarray:
    """{"weights": [[w_imb, w_bias]], "intercept": b} -> W = [w_imb, w_bias, b]."""
    if not ml_weights:
        ml_weights = {"weights": [[2.5, 1.2]], "intercept": 0.0}
    w = np.asarray(ml_weights["weights"], dtype=np.float64).reshape(-1)
    return np.array([w[0], w[1], float(ml_weights["intercept"])], dtype=np.float64)


_WARMED = False


def warmup(depth: int = 10, capacity: int = 4) -> None:
    """Компиляция (или загрузка из кэша) всех ядер ДО открытия сессии и запрет
    перекомпиляции: случайный новый тип аргумента не устроит 1-секундную паузу
    посреди торгов, а сразу упадёт с TypeError в тестах."""
    global _WARMED
    if _WARMED:
        return
    books = np.zeros((capacity, 4, depth), dtype=np.float64)
    nbs = np.zeros(capacity, dtype=np.int64)
    nas = np.zeros(capacity, dtype=np.int64)
    tss = np.zeros(capacity, dtype=np.float64)
    P = np.zeros(P_COUNT, dtype=np.float64)
    P[P_TICK] = 1.0
    P[P_LEVELS] = 1.0
    P[P_TAU_VOL] = 1.0
    P[P_TAU_FAST] = 1.0
    S = new_state()
    W = np.zeros(3, dtype=np.float64)
    out = new_out()
    rec = np.zeros((4, REC_COLS), dtype=np.float64)
    on_tick(books, nbs, nas, tss, 0, P, S, W, 0, out, rec)
    on_fill(P, S, 0, 0.0, 0.0)
    on_fill(P, S, 1, 1.0, 0.0)
    on_price_fix(S, 1.0)
    on_reject(S)
    calculate_hft_signals(
        np.ones(3), np.ones(3), np.ones(3), np.ones(3), np.zeros((1, 2)), 0.0
    )
    for fn in (on_tick, on_fill, on_price_fix, on_reject, calculate_hft_signals):
        fn.disable_compile()
    _WARMED = True
