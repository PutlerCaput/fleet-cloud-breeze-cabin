"""Запись настоящего стакана для бэктеста (RECORD_BOOKS=1).

Каждый опубликованный снимок кольца копируется Numba-функцией в
преаллоцированный буфер. Два буфера по очереди: заполненный уходит на диск
в фоновом потоке (np.savez), запись продолжается во второй — горячий путь
не ждёт диск. Файлы: {dir}/books_{дата}_{n}.npz, читаются backtest --books.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
from datetime import datetime

import numpy as np
from numba import njit

from .config import TZ_MOSCOW

logger = logging.getLogger("ContestRobot")


@njit(cache=True, nogil=True)
def record_slot(books, nbs, nas, tss, idx, out_books, out_nbs, out_nas, out_ts, n):
    out_books[n] = books[idx]
    out_nbs[n] = nbs[idx]
    out_nas[n] = nas[idx]
    out_ts[n] = tss[idx]
    return n + 1


class _Buf:
    def __init__(self, cap: int, depth: int) -> None:
        self.books = np.zeros((cap, 4, depth))
        self.nbs = np.zeros(cap, dtype=np.int64)
        self.nas = np.zeros(cap, dtype=np.int64)
        self.ts = np.zeros(cap)
        self.n = 0


class BookRecorder:
    def __init__(self, out_dir: str, depth: int, capacity: int = 1 << 19) -> None:
        self.out_dir = out_dir
        self.capacity = capacity
        self._bufs = [_Buf(capacity, depth), _Buf(capacity, depth)]
        self._cur = 0
        self._saving: asyncio.Future | None = None
        self.parts = 0
        self.dropped = 0
        # компиляция/загрузка из кэша до первого тика сессии
        tmp = _Buf(1, depth)
        record_slot(tmp.books, tmp.nbs, tmp.nas, tmp.ts, 0, tmp.books, tmp.nbs, tmp.nas, tmp.ts, 0)

    def push(self, ring, idx: int) -> None:
        b = self._bufs[self._cur]
        if b.n == self.capacity:
            if self._saving is not None and not self._saving.done():
                self.dropped += 1  # оба буфера заняты — диск не успевает
                return
            self._flush(b)
            self._cur ^= 1
            b = self._bufs[self._cur]
        b.n = record_slot(ring.books, ring.nbs, ring.nas, ring.tss, idx, b.books, b.nbs, b.nas, b.ts, b.n)

    def _path(self) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        day = datetime.now(TZ_MOSCOW).strftime("%Y-%m-%d")
        self.parts += 1
        return os.path.join(self.out_dir, f"books_{day}_{self.parts:03d}.npz")

    def _flush(self, b: _Buf) -> None:
        n, path = b.n, self._path()

        def save() -> None:
            np.savez(path, ts=b.ts[:n], books=b.books[:n], nbs=b.nbs[:n], nas=b.nas[:n])
            b.n = 0

        self._saving = asyncio.get_running_loop().run_in_executor(None, save)

    async def close(self) -> None:
        if self._saving is not None:
            await self._saving
        b = self._bufs[self._cur]
        if b.n:
            self._flush(b)
            await self._saving
        if self.dropped:
            logger.warning("[REC] пропущено снимков: %d", self.dropped)


def load_book_parts(pattern: str) -> dict[str, np.ndarray]:
    """Склеить части books_*.npz (по имени файла = по времени)."""
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(pattern)
    parts = [np.load(f) for f in files]
    return {k: np.concatenate([p[k] for p in parts]) for k in ("ts", "books", "nbs", "nas")}
