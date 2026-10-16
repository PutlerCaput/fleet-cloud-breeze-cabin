import numpy as np
import pytest

from finam_robot import kernels as K
from finam_robot.config import RiskParams, risk_to_array
from finam_robot.pipeline import SnapshotRing

TICK = 0.001


class Env:
    """Мини-стенд: кольцо + состояние + вызов on_tick одним снимком."""

    def __init__(self, **risk):
        defaults = dict(tick=TICK, warmup_n=0.0, tau_vol=1.0, cooldown=0.0, rec_interval=0.0)
        defaults.update(risk)
        self.P = risk_to_array(RiskParams(**defaults))
        self.ring = SnapshotRing(8, 10)
        self.S = K.new_state()
        self.out = K.new_out()
        self.W = K.weights_array(None)
        self.rec = np.zeros((1024, K.REC_COLS))
        self.ts = 1_000.0

    def tick(self, bid, ask, bq=100.0, aq=100.0, dt=0.1, allow=1, levels=3):
        self.ts += dt
        r = self.ring
        idx = r.next_slot()
        b = r.books[idx]
        for i in range(levels):
            b[K.BID_PX, i] = bid - i * TICK
            b[K.BID_QTY, i] = bq
            b[K.ASK_PX, i] = ask + i * TICK
            b[K.ASK_QTY, i] = aq
        r.publish(idx, levels, levels, self.ts)
        return K.on_tick(r.books, r.nbs, r.nas, r.tss, idx, self.P, self.S, self.W, allow, self.out, self.rec)

    def warm(self, n=50, bid=11.500, spread=1):
        for _ in range(n):
            self.tick(bid, bid + spread * TICK, allow=0)

    def open_long(self, price, dist=5.0):
        self.S[K.S_DIST] = dist
        K.on_fill(self.P, self.S, 1, price, self.ts)

    def open_short(self, price, dist=5.0):
        self.S[K.S_DIST] = dist
        K.on_fill(self.P, self.S, -1, price, self.ts)

    @property
    def reason(self):
        return int(self.out[K.O_REASON])


def test_mid_price_is_arithmetic_mean():
    env = Env()
    env.tick(11.500, 11.502)
    assert env.S[K.S_MID] == pytest.approx(11.501)
    assert env.S[K.S_SPREAD] == pytest.approx(2.0)
    # Совместимая функция: старый баг mid = bid*ask/2 давал bias ~ -55 руб. и вечный SELL.
    sig, imb, micro = K.calculate_hft_signals(
        np.array([11.500, 11.499, 11.498]), np.full(3, 100.0),
        np.array([11.502, 11.503, 11.504]), np.full(3, 100.0),
        np.array([[2.5, 1.2]]), 0.0,
    )
    assert sig == 0 and imb == pytest.approx(0.0) and micro == pytest.approx(11.501)


def test_crossed_book_is_ignored():
    env = Env()
    assert env.tick(11.500, 11.500) == K.ACT_NONE
    assert env.tick(11.502, 11.500) == K.ACT_NONE
    assert env.S[K.S_N] == 0.0


def test_long_entry_on_bid_imbalance():
    env = Env()
    env.warm()
    act = env.tick(11.500, 11.501, bq=300.0, aq=50.0)
    assert act == K.ACT_BUY
    assert env.S[K.S_PROB] > 0.65
    assert env.out[K.O_PRICE] == pytest.approx(11.501 + TICK)  # ask + entry_slip
    assert env.S[K.S_PENDING] == 1.0
    # Пока заявка в пути — никаких дублей.
    assert env.tick(11.500, 11.501, bq=300.0, aq=50.0) == K.ACT_NONE
    K.on_reject(env.S)
    assert env.tick(11.500, 11.501, bq=300.0, aq=50.0) == K.ACT_BUY


def test_short_entry_on_ask_imbalance():
    env = Env()
    env.warm()
    assert env.tick(11.500, 11.501, bq=50.0, aq=300.0) == K.ACT_SELL
    assert env.out[K.O_PRICE] == pytest.approx(11.500 - TICK)


def test_entry_gates():
    env = Env(warmup_n=100.0)
    env.warm(n=10)
    assert env.tick(11.500, 11.501, bq=300.0, aq=50.0) == K.ACT_NONE  # прогрев
    env = Env()
    env.warm()
    assert env.tick(11.500, 11.501, bq=300.0, aq=50.0, allow=0) == K.ACT_NONE  # гейт робота
    env = Env(max_entry_spread=3.0)
    env.warm(spread=4)
    assert env.tick(11.500, 11.504, bq=300.0, aq=50.0) == K.ACT_NONE  # широкий спред


def test_hard_stop_long():
    env = Env(hard_k=2.0, hard_min=6.0)
    env.warm()
    env.open_long(11.500, dist=5.0)
    assert env.S[K.S_STOP] == pytest.approx(11.490)  # 2*5 = 10 тиков
    assert env.tick(11.491, 11.492) == K.ACT_NONE
    assert env.tick(11.490, 11.491) == K.ACT_EXIT
    assert env.reason == K.R_HARD
    assert env.out[K.O_PRICE] == pytest.approx(11.490 - 5 * TICK)  # bid - exit_slip


def test_hard_stop_short_uses_ask():
    env = Env()
    env.warm()
    env.open_short(11.500, dist=5.0)
    assert env.S[K.S_STOP] == pytest.approx(11.510)
    assert env.tick(11.508, 11.509) == K.ACT_NONE
    assert env.tick(11.509, 11.510) == K.ACT_EXIT


def test_trailing_activates_ratchets_and_exits():
    env = Env(act_mult=2.0, fee_ticks=2.0, k_vol=0.0, k_spread=0.0, k_spread_vol=0.0,
              k_obi_var=0.0, k_obi_dir=0.0, trail_min=5.0)
    env.warm()
    env.open_long(11.500, dist=5.0)
    stops = []
    for k in range(1, 30):  # рост на 29 тиков
        assert env.tick(11.500 + k * TICK, 11.501 + k * TICK) == K.ACT_NONE
        stops.append(env.S[K.S_STOP])
    assert env.S[K.S_TRAIL_ON] == 1.0
    assert all(b >= a for a, b in zip(stops, stops[1:], strict=False))
    assert env.S[K.S_STOP] == pytest.approx(11.529 - 5 * TICK)
    # Откат в пределах дистанции — держим.
    assert env.tick(11.525, 11.526) == K.ACT_NONE
    assert env.tick(11.524, 11.525) == K.ACT_EXIT
    assert env.reason == K.R_TRAIL


def test_stop_never_loosens_when_volatility_widens_distance():
    env = Env(act_mult=1.0, fee_ticks=0.0, trail_min=2.0, trail_max=40.0)
    env.warm()
    env.open_long(11.500, dist=2.0)
    for k in range(1, 15):
        env.tick(11.500 + k * TICK, 11.501 + k * TICK, dt=1.0)
    assert env.S[K.S_TRAIL_ON] == 1.0
    stop_before = env.S[K.S_STOP]
    # Резкая пила: дистанция растёт, стоп — нет.
    for j in range(20):
        hi = 11.514 + (0.004 if j % 2 else 0.0)
        env.tick(hi, hi + TICK, dt=0.05)
        if env.S[K.S_PENDING]:
            break
        assert env.S[K.S_STOP] >= stop_before - 1e-12
        stop_before = env.S[K.S_STOP]


def test_spread_shock_defers_then_exits_on_timeout():
    env = Env(max_defer=0.3, cat_ticks=5.0)
    env.warm()
    env.open_long(11.500, dist=5.0)  # стоп 11.490
    # bid на стопе, ask улетел на 8 тиков: шок спреда -> ждём.
    assert env.tick(11.490, 11.498, dt=0.1) == K.ACT_NONE
    assert env.S[K.S_SHOCK] == 1.0 and env.S[K.S_DEFER_TS] > 0
    assert env.tick(11.490, 11.498, dt=0.1) == K.ACT_NONE
    act = K.ACT_NONE
    for _ in range(5):
        act = env.tick(11.490, 11.498, dt=0.1)
        if act:
            break
    assert act == K.ACT_EXIT
    assert env.reason in (K.R_DEFER_TIMEOUT, K.R_HARD)


def test_spread_shock_recovery_avoids_exit():
    env = Env(max_defer=1.0)
    env.warm()
    env.open_long(11.500, dist=5.0)
    assert env.tick(11.490, 11.498, dt=0.05) == K.ACT_NONE  # отложили
    assert env.tick(11.493, 11.494, dt=0.05) == K.ACT_NONE  # спред и цена вернулись
    assert env.S[K.S_DEFER_TS] == 0.0 and env.S[K.S_SIDE] == 1.0


def test_catastrophe_exits_immediately_during_shock():
    env = Env(cat_ticks=5.0)
    env.warm()
    env.open_long(11.500, dist=5.0)  # стоп 11.490, катастрофа <= 11.485
    assert env.tick(11.484, 11.494, dt=0.05) == K.ACT_EXIT
    assert env.reason == K.R_CATASTROPHE


def test_distance_scales_with_volatility():
    calm, wild = Env(tau_vol=5.0), Env(tau_vol=5.0)
    for i in range(200):
        calm.tick(11.500, 11.501, dt=0.05)
        j = (i % 6) - 3
        wild.tick(11.500 + j * 2 * TICK, 11.501 + j * 2 * TICK, dt=0.05)
    d_calm = K.trail_distance(calm.P, calm.S, 0)
    d_wild = K.trail_distance(wild.P, wild.S, 0)
    assert d_wild > d_calm
    assert calm.P[K.P_TRAIL_MIN] <= d_calm <= calm.P[K.P_TRAIL_MAX]


def test_obi_direction_widens_for_supportive_book():
    env = Env(k_obi_dir=0.5, k_spread=4.0, trail_min=1.0)
    for _ in range(100):
        env.tick(11.500, 11.501, bq=300.0, aq=50.0, dt=0.05)
    assert env.S[K.S_OBI_M] > 0.5
    d_long = K.trail_distance(env.P, env.S, 1)
    d_short = K.trail_distance(env.P, env.S, -1)
    assert d_long > d_short


def test_recorder_and_price_fix():
    env = Env()
    for _ in range(10):
        env.tick(11.500, 11.501)
    assert int(env.S[K.S_REC_N]) == 10
    assert env.rec[9, K.REC_MID] == pytest.approx(11.5005)
    env.open_long(11.501, dist=5.0)
    stop = env.S[K.S_STOP]
    K.on_price_fix(env.S, 11.502)
    assert env.S[K.S_ENTRY] == pytest.approx(11.502)
    assert env.S[K.S_STOP] == pytest.approx(stop + TICK)


def test_on_tick_does_not_allocate():
    env = Env()
    env.warm()
    import sys

    before = sys.getallocatedblocks()
    for _ in range(20_000):
        env.tick(11.500, 11.501, allow=0)
    assert sys.getallocatedblocks() - before < 100
