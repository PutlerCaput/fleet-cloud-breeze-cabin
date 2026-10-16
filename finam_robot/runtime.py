"""Логирование вне event loop, настройка GC/GIL, мониторинг пауз сборщика."""

from __future__ import annotations

import gc
import logging
import logging.handlers
import os
import queue
import sys
import time

logger = logging.getLogger("ContestRobot")


def setup_logging(log_file: str, level: int = logging.INFO) -> logging.handlers.QueueListener:
    """Запись в файл/консоль — в отдельном потоке через QueueHandler.

    RotatingFileHandler в исходнике писал на диск прямо из торгового loop:
    любой медленный write/ротация = пауза обработки стакана.
    """
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handlers: list[logging.Handler] = []
    try:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
        fh.setFormatter(fmt)
        handlers.append(fh)
    except OSError as e:
        print(f"Лог-файл недоступен ({e}), пишу только в консоль", file=sys.stderr)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    handlers.append(ch)

    q: queue.SimpleQueue = queue.SimpleQueue()
    listener = logging.handlers.QueueListener(q, *handlers, respect_handler_level=True)
    listener.start()
    logger.handlers[:] = [logging.handlers.QueueHandler(q)]
    logger.setLevel(level)
    logger.propagate = False
    return listener


class GcMonitor:
    """Считает паузы циклического GC (через gc.callbacks)."""

    def __init__(self) -> None:
        self.count = 0
        self.total_ms = 0.0
        self.max_ms = 0.0
        self._t0 = 0.0
        gc.callbacks.append(self._cb)

    def _cb(self, phase: str, info: dict) -> None:
        if phase == "start":
            self._t0 = time.perf_counter()
        else:
            ms = (time.perf_counter() - self._t0) * 1e3
            self.count += 1
            self.total_ms += ms
            if ms > self.max_ms:
                self.max_ms = ms

    def reset(self) -> None:
        self.count = 0
        self.total_ms = 0.0
        self.max_ms = 0.0

    def summary(self) -> str:
        return f"GC: {self.count} сборок, макс {self.max_ms:.2f} мс, всего {self.total_ms:.1f} мс"


def tune_runtime(gil_switch_interval: float, freeze: bool) -> None:
    """Вызывать ПОСЛЕ импортов и warmup ядер.

    * sys.setswitchinterval: поток исполнения ждёт GIL не дольше ~0.5 мс
      вместо 5 мс по умолчанию, когда поток стакана крутит Python-код;
    * gc.freeze(): всё созданное при старте (numpy, numba, sklearn, httpx —
      сотни тысяч объектов) уходит в permanent generation, и старшие сборки
      больше не обходят его в торговое время;
    * порог gen0 выше: реже короткие сборки на всплеске сообщений.
    """
    if gil_switch_interval > 0:
        sys.setswitchinterval(gil_switch_interval)
    gc.collect()
    if freeze:
        gc.freeze()
    g0, g1, g2 = gc.get_threshold()
    gc.set_threshold(max(g0, 10_000), g1, g2)
