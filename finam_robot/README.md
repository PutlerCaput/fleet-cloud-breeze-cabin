# finam_robot: low-latency rewrite of the MOEX FORTS contest robot

This is a drop-in rewrite of `InstitutionalContestRobot` (Finam Trade API, CRZ6, 30,000 RUB intraday).
It covers the four requested items: an allocation-free hot path, event-driven backpressure, a
volatility-driven risk engine compiled with Numba, and a non-blocking execution path. It also fixes
several bugs in the original that were more serious than any of the latency issues (see below).

```
python -m finam_robot                       # production: Moscow-time schedule, systemd
python -m finam_robot --now --duration 60   # open a session now, stop after 60 s
python -m finam_robot --train               # run the weekly ML job once
python -m finam_robot.bench                 # hot-path benchmark
python -m finam_robot.backtest --fetch CRZ6 # backtest on today's MOEX tape
python -m pytest finam_robot/tests -q       # 42 tests
```

Safe default: `DRY_RUN=1` uses a paper broker. `BOOK_SOURCE=synthetic DRY_RUN=1` runs the whole
pipeline offline, with no token.

## Read this first: bugs in the original code

Ordered by impact. Fix the first three even if you keep nothing else from this rewrite.

1. **The robot never processed the order book during the session.**
   `schedule...do(run_async, robot.start_trading_session())` runs `run_until_complete()`, which
   returns as soon as `start_trading_session` has *created* the producer and consumer tasks. The main
   thread then sleeps in `time.sleep(1)` with the event loop stopped, so the producer, the consumer and
   the watchdog never run between 09:55 and 23:40. The coroutine object is also created only once, at
   registration, so the second day fails with `cannot reuse already awaited coroutine`.
   → `scheduler.py` keeps one event loop running for the life of the process and calls the coroutine
   function again on each fire. It uses Moscow time and skips weekends.
2. **`mid_price = (bid * ask) / 2`.** It should be `(bid + ask) / 2`. For CR around 11.5 this makes
   `price_spread_bias` about −55, `logit` about −65, and `P(up)` about 0. The robot would therefore
   **sell on every tick, sized at 95 % of margin**.
3. **The ML model trains and trades on different features.** Weights are fitted on the mean change
   in candle close price and volume, then applied to order-book imbalance and micro-price bias. The
   coefficients mean nothing. → The kernel now records its own features (`ts, imbalance, bias, mid,
   spread`) every 0.25 s into a preallocated array. Training uses those files with a forward-looking
   label, a purged walk-forward split (`TimeSeriesSplit(gap=horizon)`), and hit-rate measured only
   where the rule actually trades.
4. **The Finam SDK does not have the calls the code uses.** `finam-trade-api` 4.x is REST only. It has
   no `orders_stream.subscribe_order_book`, `candles.get_candles` or `portfolios.get_portfolio`, and
   `Client` takes a `TokenManager`, not `token=`. The SDK also opens a **new HTTPS/TLS connection for
   every request** (`async with httpx.AsyncClient()` inside `_exec_request`). → `gateway.py` keeps one
   HTTP/2 connection for the whole session, warms it up before the first order and refreshes the JWT
   in the background.
5. **The Finam gRPC order-book stream sends deltas.** Rows carry `ACTION_ADD/UPDATE/REMOVE`. Dropping
   an update with `get_nowait()` corrupts the local book for good. → Every update is applied (see 2.
   below).
6. **The watchdog and Telegram never worked.** `ping_target("1.1.1")` is not a valid URL, so the
   check always failed. `https://telegram.org{TOKEN}` is not the Bot API URL
   (`https://api.telegram.org/bot{TOKEN}/sendMessage`). The watchdog also never blocked anything.
   → It now closes the `infra` gate and blocks entries for real.
7. **P&L was measured on `money.available`.** Free cash drops by the margin as soon as a position is
   open. → Equity is used instead. **`calculate_lots` made 2 REST calls before every order**, adding
   100–400 ms. → Sizing is refreshed every 15 s in the background.
8. `def init` / `if name == "main"` in the paste are probably markdown eating the underscores. Check
   your file for real `__init__` / `__name__ == "__main__"`.

## The four requested items, with measurements

All numbers are from `python -m finam_robot.bench` (Python 3.13, NumPy 2.5, Numba 0.68, 4 vCPU).

### 1. Allocation-free feature preparation

* `SnapshotRing` preallocates `books[slot, 4, depth]` together with `nbs`, `nas` and `tss`. The
  assemblers in `book.py` write top-N levels straight into the ring through a flat `memoryview`.
  Writing a scalar through `memoryview` is about 1.6× faster than `ndarray.__setitem__`, and neither
  creates an object.
* The kernel reads the slot **by index**: `on_tick(books, nbs, nas, tss, idx, ...)`. It never creates
  a view or a copy, and it returns a small int. Fill counts come back as attributes, not tuples. The
  number format (Decimal `value` string, `{num, scale}`, float, REST dict, protobuf) is detected once
  per stream.
* `kernels.warmup()` compiles each kernel (or loads it from `cache=True`) and then calls
  `disable_compile()`. A new argument type can no longer trigger a 1-second recompile in the middle of
  a session.
* `fastmath` drops only `nnan` and `ninf`. Vectorization and FMA stay on, while NaN checks on broken
  quotes still work.

| per tick: book → features → signal | p50 | p99 |
|---|---|---|
| original (4 × `np.array`, top-3, signal only) | 3.90 µs | 13.5 µs |
| ring + `on_tick` (top-3, signal **plus the full risk engine**) | 3.76 µs | 10.4 µs |
| ring + `on_tick` (top-10) | 7.10 µs | 23.5 µs |

The new path costs about the same per tick as the old one. In that time it also runs the risk engine
and allocates no arrays. Where the time goes in Python:

- calling the Numba dispatcher: about 75 ns per argument;
- decoding SDK objects: about 0.4 µs per book level;
- the compiled maths itself: tens of nanoseconds.

The GC premise of the request does not hold for CPython. Per-tick arrays are freed by reference
counting and never trigger cyclic GC; the cost was allocation and copying. The useful GC lever is
`runtime.tune_runtime()`: `gc.freeze()` after startup, so old generations stop walking the
numpy/numba/sklearn object graph, plus a higher gen0 threshold. `GcMonitor` reports GC pauses in the
end-of-day message. `np.memmap` and shared memory are the wrong tools here: one is file-backed, the
other is for sharing between processes.

### 2. Backpressure: conflating ring instead of `Queue(maxsize=50)`

The producer applies **every** message to the local book, writes top-N into the next ring slot and
publishes `seq`. Then one of two things happens:

- **inline mode (default):** it calls the kernel directly, so there is no queue hop at all;
- otherwise it rings a `Doorbell`. Any number of rings before the consumer wakes count as one wake-up,
  and the consumer processes only the newest slot.

Single-threaded asyncio means a slot can never be read half-written. The ring keeps history for
multi-snapshot features such as OFI.

| 4 kHz feed, an order on every 400th snapshot | snapshot age p99 | max | decisions on data > 5 ms old |
|---|---|---|---|
| `Queue(50)` + drop-oldest + `await send_order` | 11.8 ms | 12.4 ms | 300 (1,902 messages dropped) |
| ring + doorbell + `submit()` | 0.02 ms | 0.35 ms | 0 |

### 3. Volatility-driven trailing stop (Numba, `kernels.trail_distance` / `on_tick`)

All distances are in ticks. `P` holds the parameters (`RISK_*` env vars) and `S` holds the state; both
are flat `float64` arrays:

```
sigma_H = sqrt(RV / tau_vol * horizon)              RV: time-decayed sum of (Δmid/tick)²
dist    = k_vol*sigma_H + k_spread*E[spread] + k_spread_vol*std(spread)
dist   *= 1 + k_obi_var * std(OBI)                  unstable book → more room
dist   *= clamp(1 + k_obi_dir * side * E[OBI], 0.5, 1.5)   book with you → let it run; against you → tighten
dist    = clamp(ceil(dist), trail_min, trail_max)
```

* All EWMAs and the RV decay use **time-based** weights. A burst of 1,000 updates in 1 ms does not
  wipe out the history, and a quiet period does not give one tick too much weight.
* Trailing turns on at `profit ≥ fee_ticks + act_mult*dist`, so a trailing exit always covers fees.
  The hard stop is `clamp(hard_k*dist_at_entry, hard_min, hard_max)` and is frozen at entry. The stop
  only ever ratchets in your favour: when volatility widens, only *future* candidates move.
* The stop is checked against the price you would actually get: **bid for a long, ask for a short**.
  Checking against mid or last overstates the cushion by half a spread.
* **Spread-shock deferral.** If the stop is hit while `spread ≥ shock_mult × E[spread]`, the exit
  waits up to `max_defer` (0.3 s) for the spread to normalize. Exceptions: past `cat_ticks` beyond the
  stop it exits at once (`catastrophe`), and when the timeout runs out it exits anyway. This removes
  the worst-slippage fills. Exits use marketable IOC limits at `±exit_slip` ticks, not bare market
  orders.
* Defaults are a starting point, not calibrated on real CR data. In a simulated calm book (1-tick
  spread, mid moving about 2 ticks/s) they give a 5-tick trail, activation at 12 ticks and a
  10-tick hard stop, close to the old static 5 / 15 / 10. In a simulated fast book (3-tick jumps,
  spread flicker) they give 25 / 52 / 30. Calibrate `k_vol` and `horizon` on the recorded features
  before going live.

### 4. Non-blocking execution (`execution.py`)

* The consumer only calls `submit(OrderIntent(target_lots, ...))`. It never awaits an order.
* The executor works on **target position, latest wins**. Intents that arrive while an order is in
  flight are merged, and only the difference to the newest target is sent afterwards. That rules out
  order storms and double entries.
* Orders pass a token bucket (`MAX_ORDERS_PER_SEC`). Exits retry with a fresh price.
* After any timeout or connection error, the executor **reads the real position from the exchange
  before retrying**. An order whose status is unknown may already have filled, so retrying blindly
  can close the position twice.
* Fills come back through `call_soon_threadsafe`. The entry price is corrected later from the actual
  trades (`price_fix`).

**On "a separate thread": measured, and off by default.**

| intent → order sent | p50 | p99 |
|---|---|---|
| same event loop, idle | 75 µs | 150 µs |
| separate thread, idle | 197 µs | 1.2 ms |
| same event loop, market-data load | 198 µs | 411 µs |
| separate thread, market-data load | **3.6 ms** | **61 ms** |

With the GIL, the execution thread waits behind the market-data thread. A smaller
`sys.setswitchinterval` does not fix that. What actually removes the delay is *not awaiting* the
order, plus a persistent HTTP/2 connection: a TLS handshake costs tens of ms, the handoff costs
0.1 ms. If you ever need isolation, use a separate **process**. `EXEC_THREADED=1` is kept for
free-threaded Python builds.

## Backtest (`backtest.py`)

The backtest replays a tape through the **same** `kernels.on_tick` the robot runs live. The tape
array is read by index, exactly like the ring buffer. The execution model mirrors the live path:

- orders reach the exchange after `latency` and fill against the book at that moment as IOC limits;
- when book depth is known, large orders walk the levels;
- exits retry at a fresh price until the position is flat;
- per-lot fees, sizing (`equity × 0.95 // margin`), clearing windows, the daily loss stop and the
  end-of-session flatten all match the robot.

```
python -m finam_robot.backtest --fetch CRZ6                    # today's MOEX ISS tape (free, current session only)
python -m finam_robot.backtest --books "/home/contest_robot/books/books_*.npz"   # real recorded book
```

Outputs `backtest_report.json`, `trades_dynamic.csv` and `trades_static.csv`. The report includes:

- the new risk engine against the old static 5 / 15 / 10 stops;
- a latency × fee grid and a signal-threshold sweep;
- a **null test**: the same prices with order flow shuffled in one-minute blocks;
- a bootstrap of trades;
- an ML walk-forward: train on the first half of the session, test on the second.

**Data limits.** Without a subscription, MOEX ISS gives tick trades for the current session only,
and no order book. A trades-only tape becomes a proxy book: bid and ask are the last sell- and
buy-aggressor prices, and the "volumes" are decayed aggressive buy and sell flow. The signal is
then trade-flow imbalance, which is close to book imbalance but not the same. For a real
multi-week backtest, run the robot with `RECORD_BOOKS=1` (dry run is fine). `recorder.py` writes
every book snapshot to `data_dir/books/*.npz` off the hot path, and `--books` replays those files.

**Result for 16.10.2026** (CRZ6, 246,837 trades; robot active 09:55–21:40 MSK; 30 ms latency;
1 RUB/lot/side fee assumed; margin 1,232.71 RUB per lot from ISS):

| | new risk engine | old 5/15/10 stops |
|---|---|---|
| return | −10.13 % (hit the daily stop at 18:33) | −6.55 % |
| trades / win rate | 60 / 42 % | 29 / 34 % |
| P&L before fees / fees | −373 / 2,666 RUB | −705 / 1,260 RUB |

- **No edge:** shuffled order flow did as well or better in 73 % of 60 runs (mean −8.7 %).
- **Even with zero fees** the day is −1.9 %.
- **Strict thresholds hurt:** raising the entry threshold makes P&L before fees *worse*.
- **The ML model predicts but doesn't pay:** it picks the 5-second direction 82 % of the time
  (walk-forward), yet loses out of sample, because the moves it predicts are smaller than the
  spread plus fees.
- **Break-even math:** each round trip costs about 2 ticks per lot in fees plus the spread. Hitting
  +3 %/day at this trade count needs about +2.6 ticks per lot per trade before fees; the day
  produced −0.27.

## Layout

| file | role |
|---|---|
| `kernels.py` | Numba: features, signal, risk engine, `on_fill` / `on_reject` / `on_price_fix`, feature recorder, warmup |
| `pipeline.py` | `SnapshotRing`, `Doorbell`, `ConflatingConsumer` |
| `book.py` | `LevelsAssembler` (`bids`/`asks` snapshots), `RowBook` (Finam delta rows, bisect-sorted) |
| `sources.py` | `RestPollingBookSource`, `GrpcBookSource`, `IteratorBookSource` (wraps your SDK iterator), `SyntheticBookSource` |
| `gateway.py` | `FinamRestGateway` (persistent HTTP/2, JWT refresh, IOC limits, reconcile), `PaperBroker` / `PaperGateway` |
| `execution.py` | `ExecutionEngine`: target-position coalescing, token bucket, reconcile before retry |
| `robot.py` | `InstitutionalContestRobot`: hot path, entry gates, sizing, daily limits, session start/stop |
| `backtest.py`, `moex_iss.py`, `recorder.py` | event-driven backtest on the live kernel, MOEX ISS tape loader, real order-book recorder |
| `watchdog.py`, `notify.py`, `ml.py`, `scheduler.py`, `runtime.py` | infrastructure |

## Configuration (env)

| var | default | meaning |
|---|---|---|
| `FINAM_API_TOKEN`, `FINAM_ACCOUNT_ID` | | Finam secret and account |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | | alerts (sent off the hot path) |
| `FINAM_SYMBOL` | `CRZ6@RTSX` | `TICKER@MIC`; check it against `/v1/assets` |
| `BOOK_SOURCE` | `rest` | `rest` (polling fallback) \| `grpc` \| `synthetic` |
| `FINAM_GRPC_PB2_MODULE` | | module with `SubscribeOrderBookRequest/Response` generated from Finam's protos |
| `BOOK_STREAM_MODE` | `delta` | how gRPC/SDK rows are applied |
| `DRY_RUN` | `1` | paper broker (`PAPER_EQUITY`, `PAPER_MARGIN_PER_LOT`, `PAPER_POINT_VALUE`, `PAPER_FEE_PER_LOT`) |
| `MARGIN_UTILIZATION` | `0.95` | same as the original; this is very aggressive |
| `MAX_DAILY_LOSS_PCT` | `10` | equity kill switch: flatten and block entries for the rest of the day |
| `STOP_AFTER_TARGET` | `0` | stop entering after +`DAILY_TARGET_PCT` |
| `NO_ENTRY_WINDOWS` | `13:58-14:06,18:43-19:06,23:30-23:59` | clearing sessions and the last minutes before the forced close |
| `EXEC_THREADED` | `0` | see item 4 |
| `RECORD_BOOKS` | `0` | record every order-book snapshot for `backtest --books` |
| `RISK_<NAME>` | see `config.RiskParams` | any risk-engine parameter, e.g. `RISK_K_VOL=2.0` |

To keep using your own SDK stream, pass
`source_factory=lambda: IteratorBookSource(lambda: client.orders_stream.subscribe_order_book(...))`.
The message shape (`bids`/`asks`, `rows`, or `order_book[]`) is detected automatically.

Deploy: `deploy/finam-robot.service` (systemd). `SIGTERM` flattens the position and closes the session
cleanly. A restart in the middle of the day adopts the exchange position and puts a hard stop on it.

## Not verified against the live API (no credentials in this environment)

* The gRPC path `/grpc.tradeapi.v1.marketdata.MarketDataService/SubscribeOrderBook` and the row fields
  come from Finam's published docs. You still have to generate the stub classes from their protos.
* `min_step` is read as minimal price units (`"1"` with `decimals=3` → 0.001). The tick is logged at
  session start; `RISK_TICK` overrides it.
* IOC limit orders on FORTS through Finam, and the `/trades` query parameters used for `price_fix`.
* Run a full day with `DRY_RUN=1 BOOK_SOURCE=rest` (or `grpc`) before going live.
