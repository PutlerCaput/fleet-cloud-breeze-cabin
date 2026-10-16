"""Асинхронный планировщик по московскому времени.

Исходник: `schedule.every().day.at("09:55").do(run_async, robot.start_trading_session())`
  * корутина создавалась ОДИН раз при регистрации — на второй день
    "cannot reuse already awaited coroutine";
  * `run_until_complete` возвращался сразу после старта, а продюсер/консьюмер
    и watchdog — задачи на ОСТАНОВЛЕННОМ loop: между 09:55 и 23:40 главный
    поток спал в `time.sleep(1)`, и стакан не обрабатывался вообще;
  * время — локальное время сервера, а не МСК; `every().day` — и в выходные.
Здесь один постоянно работающий loop; задача — функция-корутина, вызываемая
заново при каждом срабатывании.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Awaitable, Callable

from .config import TZ_MOSCOW

logger = logging.getLogger("ContestRobot")


@dataclass
class Job:
    name: str
    at: time
    fn: Callable[[], Awaitable]
    weekdays: tuple[int, ...] = tuple(range(7))
    next_fire: datetime | None = field(default=None, compare=False)

    def compute_next(self, after: datetime) -> datetime:
        for k in range(8):
            day = after.date() + timedelta(days=k)
            if day.weekday() not in self.weekdays:
                continue
            cand = datetime.combine(day, self.at, tzinfo=TZ_MOSCOW)
            if cand > after:
                return cand
        raise ValueError(f"У задачи {self.name} нет рабочих дней")


class MoscowScheduler:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(TZ_MOSCOW))
        self.jobs: list[Job] = []

    def add(self, name: str, at: time, fn: Callable[[], Awaitable], weekdays=tuple(range(7))) -> Job:
        job = Job(name, at, fn, tuple(weekdays))
        self.jobs.append(job)
        return job

    async def _run(self, job: Job) -> None:
        logger.info("[SCHED] %s", job.name)
        try:
            await job.fn()
        except Exception:
            logger.exception("[SCHED] задача %s упала", job.name)

    async def run(self) -> None:
        now = self._clock()
        for j in self.jobs:
            j.next_fire = j.compute_next(now)
            logger.info("[SCHED] %s -> %s", j.name, j.next_fire.isoformat(timespec="minutes"))
        while True:
            now = self._clock()
            for j in self.jobs:
                if j.next_fire <= now:
                    j.next_fire = j.compute_next(now)
                    asyncio.create_task(self._run(j), name=f"job-{j.name}")
            wait = min(j.next_fire for j in self.jobs) - now
            # короткие сны: переживаем коррекцию часов NTP и suspend VPS
            await asyncio.sleep(min(max(wait.total_seconds(), 0.05), 30.0))


def in_session(now: datetime, start: time, stop: time, weekdays) -> bool:
    return now.weekday() in weekdays and start <= now.time() < stop
