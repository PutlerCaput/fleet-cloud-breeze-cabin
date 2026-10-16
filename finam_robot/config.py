"""Настройки робота: окружение, риск-параметры, расписание.

Все числовые параметры риск-движка упакованы в плоский float64-массив
(см. kernels.P_*), чтобы Numba-ядро читало их без словарей и атрибутов.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from datetime import time
from zoneinfo import ZoneInfo

import numpy as np

TZ_MOSCOW = ZoneInfo("Europe/Moscow")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _parse_windows(value: str) -> tuple[tuple[time, time], ...]:
    """"13:58-14:06,18:43-19:06" -> ((13:58, 14:06), (18:43, 19:06))"""
    out = []
    for chunk in filter(None, (c.strip() for c in value.split(","))):
        a, b = chunk.split("-")
        out.append((_parse_hhmm(a), _parse_hhmm(b)))
    return tuple(out)


@dataclass(frozen=True)
class RiskParams:
    """Параметры сигнала и волатильностного риск-движка.

    Все расстояния — в ШАГАХ ЦЕНЫ (тиках), а не в рублях: так одни и те же
    коэффициенты работают на любом фьючерсе. Значения по умолчанию в спокойном
    рынке дают примерно старые 5 / 15 / 10 пунктов (trailing / activation / hard).
    """

    tick: float = 0.001             # шаг цены, перезаписывается из /assets при старте
    levels: int = 3                 # уровней стакана для дисбаланса (как в исходнике)
    p_buy: float = 0.65
    p_sell: float = 0.35
    tau_vol: float = 30.0           # с, окно реализованной дисперсии mid
    tau_fast: float = 5.0           # с, EWMA спреда и дисбаланса
    horizon: float = 5.0            # с, горизонт риска для трейлинга
    k_vol: float = 1.0              # множитель sigma(horizon)
    k_spread: float = 1.0           # множитель среднего спреда
    k_spread_vol: float = 1.0       # множитель волатильности спреда
    k_obi_var: float = 1.0          # расширение на нестабильном стакане: *(1 + k*std(OBI))
    k_obi_dir: float = 0.5          # OBI за позицию — шире, против — уже
    trail_min: float = 3.0          # тиков
    trail_max: float = 40.0         # тиков
    act_mult: float = 2.0           # трейлинг включается при прибыли >= fee + act_mult*dist
    fee_ticks: float = 2.0          # издержки круга (комиссии) в тиках
    hard_k: float = 2.0             # жёсткий стоп = hard_k * dist на входе
    hard_min: float = 6.0
    hard_max: float = 30.0
    shock_mult: float = 3.0         # шок спреда: spread > shock_mult * EWMA(spread)
    max_defer: float = 0.3          # с, сколько ждать нормализации спреда перед выходом
    cat_ticks: float = 5.0          # «катастрофа»: за стопом дальше этого — выход без ожидания
    max_entry_spread: float = 3.0   # тиков, вход только при узком спреде
    warmup_n: float = 200.0         # обновлений стакана до первой сделки
    exit_slip: float = 5.0          # тиков: агрессия выходной лимитки
    entry_slip: float = 1.0         # тиков: агрессия входной лимитки
    cooldown: float = 3.0           # с, пауза после выхода
    exit_on_reversal: float = 0.0   # 1 — выходить по противоположному сигналу модели
    rec_interval: float = 0.25      # с, шаг записи признаков для ML

    @classmethod
    def from_env(cls, **overrides) -> "RiskParams":
        values = {}
        for f in fields(cls):
            raw = os.getenv("RISK_" + f.name.upper())
            if raw is not None:
                values[f.name] = type(f.default)(raw)
        values.update(overrides)
        return cls(**values)


@dataclass(frozen=True)
class Settings:
    finam_secret: str = ""
    account_id: str = ""
    tg_token: str = ""
    tg_chat_id: str = ""

    symbol: str = "CRZ6@RTSX"                 # TICKER@MIC в Finam Trade API
    book_source: str = "rest"                 # rest | grpc | sdk
    book_stream_mode: str = "delta"           # delta | snapshot (только для grpc/sdk)
    rest_poll_interval: float = 0.5           # с, опрос стакана по REST (запасной режим)
    book_depth: int = 3                       # уровней в буфере (= levels сигнала; +0.4 мкс/уровень)
    ring_capacity: int = 256                  # снимков в кольцевом буфере (степень двойки)
    inline_consumer: bool = True              # считать сигнал прямо в продюсере
    record_books: bool = False                # писать стакан для бэктеста (data_dir/books)

    dry_run: bool = True                      # бумажный исполнитель вместо боевого
    exec_threaded: bool = False               # True — отдельный поток (см. README: GIL!)
    max_orders_per_sec: float = 5.0
    order_timeout: float = 2.0                # с, ожидание исполнения одной заявки
    exit_retries: int = 5
    margin_utilization: float = 0.95          # доля свободных средств под ГО (как в исходнике)
    max_lots: int = 1000
    max_daily_loss_pct: float = 10.0          # дневной стоп по эквити, % от старта
    stop_after_target: bool = False           # прекратить входы после +3%
    daily_target_pct: float = 3.0
    pending_timeout: float = 10.0             # с, зависшая заявка -> сверка с биржей

    session_start: time = time(9, 55)
    session_stop: time = time(23, 40)
    trading_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    no_entry_windows: tuple[tuple[time, time], ...] = (
        (time(13, 58), time(14, 6)),   # дневной промклиринг
        (time(18, 43), time(19, 6)),   # вечерний клиринг
        (time(23, 30), time(23, 59)),  # не входить перед принудительным закрытием
    )
    md_stale_after: float = 15.0              # с без стакана -> входы блокируются

    data_dir: str = "/home/contest_robot"
    log_file: str = "/home/contest_robot/robot_trading.log"
    gc_freeze: bool = True
    gil_switch_interval: float = 0.0005       # с, вместо 5 мс по умолчанию

    risk: RiskParams = field(default_factory=RiskParams)

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = _env("ROBOT_DATA_DIR", "/home/contest_robot")
        return cls(
            finam_secret=_env("FINAM_API_TOKEN", ""),
            account_id=_env("FINAM_ACCOUNT_ID", ""),
            tg_token=_env("TELEGRAM_BOT_TOKEN", ""),
            tg_chat_id=_env("TELEGRAM_CHAT_ID", ""),
            symbol=_env("FINAM_SYMBOL", "CRZ6@RTSX"),
            book_source=_env("BOOK_SOURCE", "rest"),
            book_stream_mode=_env("BOOK_STREAM_MODE", "delta"),
            rest_poll_interval=_env_float("REST_POLL_INTERVAL", 0.5),
            book_depth=_env_int("BOOK_DEPTH", 3),
            ring_capacity=_env_int("RING_CAPACITY", 256),
            inline_consumer=_env_bool("INLINE_CONSUMER", True),
            record_books=_env_bool("RECORD_BOOKS", False),
            dry_run=_env_bool("DRY_RUN", True),
            exec_threaded=_env_bool("EXEC_THREADED", False),
            max_orders_per_sec=_env_float("MAX_ORDERS_PER_SEC", 5.0),
            order_timeout=_env_float("ORDER_TIMEOUT", 2.0),
            exit_retries=_env_int("EXIT_RETRIES", 5),
            margin_utilization=_env_float("MARGIN_UTILIZATION", 0.95),
            max_lots=_env_int("MAX_LOTS", 1000),
            max_daily_loss_pct=_env_float("MAX_DAILY_LOSS_PCT", 10.0),
            stop_after_target=_env_bool("STOP_AFTER_TARGET", False),
            daily_target_pct=_env_float("DAILY_TARGET_PCT", 3.0),
            pending_timeout=_env_float("PENDING_TIMEOUT", 10.0),
            session_start=_parse_hhmm(_env("SESSION_START", "09:55")),
            session_stop=_parse_hhmm(_env("SESSION_STOP", "23:40")),
            trading_weekdays=tuple(int(x) for x in _env("TRADING_WEEKDAYS", "0,1,2,3,4").split(",")),
            no_entry_windows=_parse_windows(
                _env("NO_ENTRY_WINDOWS", "13:58-14:06,18:43-19:06,23:30-23:59")
            ),
            md_stale_after=_env_float("MD_STALE_AFTER", 15.0),
            data_dir=data_dir,
            log_file=_env("ROBOT_LOG_FILE", os.path.join(data_dir, "robot_trading.log")),
            gc_freeze=_env_bool("GC_FREEZE", True),
            gil_switch_interval=_env_float("GIL_SWITCH_INTERVAL", 0.0005),
            risk=RiskParams.from_env(),
        )


def risk_to_array(risk: RiskParams) -> np.ndarray:
    """Упаковка RiskParams в массив в порядке индексов kernels.P_*."""
    from . import kernels

    arr = np.zeros(kernels.P_COUNT, dtype=np.float64)
    for name, idx in kernels.PARAM_INDEX.items():
        arr[idx] = float(getattr(risk, name))
    return arr
