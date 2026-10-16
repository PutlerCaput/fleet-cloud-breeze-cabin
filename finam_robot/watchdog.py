"""Контроль инфраструктуры. В отличие от исходника, сбой реально блокирует входы
(gate "infra" в роботе), а не только пишет в лог.

Исправлено:
  * `ping_target("1.1.1")` — невалидный URL, проверка всегда падала;
  * интернет проверяется только для диагностики, когда упал API;
  * новый httpx-клиент на каждую проверку заменён одним постоянным;
  * добавлена проверка живости потока исполнения;
  * гистерезис: N неудач подряд, чтобы один медленный ответ не дёргал режим.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

import httpx

logger = logging.getLogger("ContestRobot")


class RobotWatchdog:
    def __init__(self, robot, interval: float = 10.0, fail_threshold: int = 2,
                 min_free_gb: float = 1.0, internet_url: str = "https://1.1.1.1/cdn-cgi/trace",
                 disk_path: str = "/") -> None:
        self.robot = robot
        self.interval = interval
        self.fail_threshold = fail_threshold
        self.min_free_gb = min_free_gb
        self.internet_url = internet_url
        self.disk_path = disk_path
        self.is_connected = True
        self._fails = 0

    async def _internet_ok(self, http: httpx.AsyncClient) -> bool:
        try:
            return (await http.get(self.internet_url)).status_code < 500
        except Exception:
            return False

    async def check_once(self, http: httpx.AsyncClient) -> tuple[bool, str]:
        problems = []
        api_ok = await self.robot.api_healthy()
        if not api_ok:
            problems.append("API Finam" if await self._internet_ok(http) else "интернет")
        try:
            free_gb = shutil.disk_usage(self.disk_path).free / 2**30
            if free_gb < self.min_free_gb:
                problems.append(f"диск ({free_gb:.2f} ГБ)")
        except OSError:
            problems.append("диск")
        if not self.robot.exec_alive():
            problems.append("поток исполнения")
        return not problems, ", ".join(problems)

    async def check_health_loop(self) -> None:
        async with httpx.AsyncClient(timeout=3.0) as http:
            while True:
                await asyncio.sleep(self.interval)
                try:
                    ok, what = await self.check_once(http)
                except Exception as e:
                    ok, what = False, f"watchdog: {e!r}"
                self._fails = 0 if ok else self._fails + 1
                if self._fails >= self.fail_threshold and self.is_connected:
                    self.is_connected = False
                    logger.critical("[WATCHDOG] Сбой: %s. Блокировка новых входов.", what)
                    self.robot.set_gate("infra", False)
                    self.robot.notifier.notify(f"🚨 КРИТИЧЕСКИЙ СБОЙ: {what}. Новые входы заблокированы.")
                elif ok and not self.is_connected:
                    self.is_connected = True
                    logger.info("[WATCHDOG] Инфраструктура восстановлена.")
                    self.robot.set_gate("infra", True)
                    self.robot.notifier.notify("✅ Связь восстановлена. Робот вернулся в строй.")
