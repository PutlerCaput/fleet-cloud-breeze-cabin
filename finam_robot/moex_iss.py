"""Публичные данные MOEX ISS для бэктеста (без авторизации).

Анонимно ISS отдаёт ленту сделок фьючерса только за ТЕКУЩУЮ торговую сессию
(с признаком агрессора BUYSELL) и параметры контракта. Исторический стакан и
AlgoPack (obstats/orderstats) требуют подписки, поэтому для многодневного
бэктеста записывайте стакан самим роботом (RECORD_BOOKS=1).
"""

from __future__ import annotations

import csv
import gzip
import os
import time
from datetime import datetime

import httpx

from .config import TZ_MOSCOW

ISS = "https://iss.moex.com/iss/engines/futures/markets/forts/securities"


def fetch_security(secid: str) -> dict:
    cols = "SECID,SHORTNAME,MINSTEP,STEPPRICE,INITIALMARGIN,LASTTRADEDATE,PREVSETTLEPRICE"
    r = httpx.get(f"{ISS}/{secid}.json", params={"iss.meta": "off", "iss.only": "securities",
                                                "securities.columns": cols}, timeout=30)
    r.raise_for_status()
    sec = r.json()["securities"]
    return dict(zip(sec["columns"], sec["data"][0], strict=True))


def fetch_trades(secid: str, cache_dir: str, page: int = 5000) -> str:
    """Скачать всю ленту текущей сессии в cache_dir/{secid}_{дата}.csv.gz."""
    os.makedirs(cache_dir, exist_ok=True)
    rows: list[list] = []
    start = 0
    with httpx.Client(timeout=60) as http:
        while True:
            for attempt in range(4):
                try:
                    r = http.get(f"{ISS}/{secid}/trades.json",
                                 params={"iss.meta": "off", "iss.only": "trades", "start": start, "limit": page})
                    r.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
            data = r.json()["trades"]
            cols = data["columns"]
            chunk = data["data"]
            if not chunk:
                break
            rows.extend(chunk)
            start += len(chunk)
    if not rows:
        raise RuntimeError(f"ISS не вернул сделок по {secid}")
    day = rows[0][cols.index("TRADEDATE")]
    path = os.path.join(cache_dir, f"{secid}_{day}.csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return path


def load_trades(path: str):
    """-> (ts_epoch[float], price, qty, side[+1 buy aggressor / -1 sell], tradeno).

    TRADETIME у ISS с точностью до секунды; сделки одной секунды равномерно
    раскладываются внутри неё в порядке TRADENO (порядок исполнения сохраняется).
    """
    import numpy as np

    with gzip.open(path, "rt", newline="") as f:
        reader = csv.reader(f)
        cols = next(reader)
        c = {name: i for i, name in enumerate(cols)}
        recs = [r for r in reader if r[c["OFFMARKETDEAL"]] in ("0", "")]
    recs.sort(key=lambda r: int(r[c["TRADENO"]]))
    secs = np.empty(len(recs))
    cache: dict[str, float] = {}
    for i, r in enumerate(recs):
        key = r[c["TRADEDATE"]] + " " + r[c["TRADETIME"]]
        t = cache.get(key)
        if t is None:
            t = datetime.strptime(key, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_MOSCOW).timestamp()
            cache[key] = t
        secs[i] = t
    price = np.array([float(r[c["PRICE"]]) for r in recs])
    qty = np.array([float(r[c["QUANTITY"]]) for r in recs])
    side = np.array([1.0 if r[c["BUYSELL"]] == "B" else -1.0 for r in recs])
    tradeno = np.array([int(r[c["TRADENO"]]) for r in recs], dtype=np.int64)
    # раскладка внутри секунды
    ts = secs.copy()
    i = 0
    n = len(secs)
    while i < n:
        j = i
        while j < n and secs[j] == secs[i]:
            j += 1
        k = j - i
        if k > 1:
            ts[i:j] = secs[i] + np.arange(k) / k
        i = j
    return ts, price, qty, side, tradeno
