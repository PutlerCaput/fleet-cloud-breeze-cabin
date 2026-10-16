"""Неблокирующие уведомления в Telegram.

`notify()` из горячего пути — это put_nowait в ограниченную очередь; отправка
идёт в отдельной задаче через одно постоянное соединение. Исходный вариант
делал `await send_tg_message()` с таймаутом 5 с прямо в торговом цикле и на
каждое сообщение открывал новый TLS, а URL `https://telegram.org{TOKEN}` не
является Bot API — сообщения не доходили.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger("ContestRobot")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, maxsize: int = 200) -> None:
        self.enabled = bool(token and chat_id)
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def notify(self, text: str) -> None:
        logger.info("[TG] %s", text.replace("\n", " | "))
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            self.dropped += 1

    async def run(self) -> None:
        if not self.enabled:
            return
        async with httpx.AsyncClient(http2=True, timeout=10.0) as http:
            while True:
                text = await self._queue.get()
                for _ in range(3):
                    try:
                        r = await http.post(self._url, json={"chat_id": self._chat_id, "text": text})
                        if r.status_code == 429:
                            retry = r.json().get("parameters", {}).get("retry_after", 1)
                            await asyncio.sleep(float(retry))
                            continue
                        if r.status_code != 200:
                            logger.error("[TG ERR] %s %s", r.status_code, r.text[:200])
                        break
                    except Exception as e:
                        logger.error("[TG ERR] Уведомление не отправлено: %s", e)
                        await asyncio.sleep(1.0)

    async def flush(self, timeout: float = 5.0) -> None:
        """Дать очереди опустеть (перед выключением)."""
        end = asyncio.get_running_loop().time() + timeout
        while self.enabled and not self._queue.empty() and asyncio.get_running_loop().time() < end:
            await asyncio.sleep(0.1)
