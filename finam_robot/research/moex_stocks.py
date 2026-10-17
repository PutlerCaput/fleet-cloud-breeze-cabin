"""Данные MOEX ISS по акциям (режим TQBR) для исследования стратегий.

Всё публичное, без авторизации:
  * дневные свечи ВСЕХ бумаг TQBR на каждую дату — список бумаг берётся на
    дату (point-in-time), поэтому делистинги и выпавшие бумаги не теряются;
  * сплиты/консолидации (/iss/statistics/engines/stock/splits);
  * дивиденды по бумагам (/iss/securities/{secid}/dividends) и дата отсечки
    -> экс-дата с учётом режима расчётов T+2 (до 31.07.2023) и T+1 (после);
  * текущие размеры лотов;
  * часовые свечи для выбранных бумаг.

Запуск: python -m finam_robot.research.moex_stocks <out_dir> [start] [end]
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd

# публичные данные без ключей; MOEX_ISS=http://iss.moex.com/iss, если HTTPS режется по пути
ISS = os.environ.get("MOEX_ISS", "https://iss.moex.com/iss")
T1_SWITCH = "2023-07-31"  # переход рынка акций на T+1


_local = threading.local()


def _client() -> httpx.Client:
    """Одно соединение на поток (меньше TLS-рукопожатий через прокси)."""
    c = getattr(_local, "client", None)
    if c is None:
        c = _local.client = httpx.Client(timeout=60)
    return c


def _get(http: httpx.Client | None, url: str, params: dict, tries: int = 10) -> dict:
    for attempt in range(tries):
        try:
            r = (http or _client()).get(url, params=params)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            if attempt == tries - 1:
                raise
            _local.client = None  # оборванное соединение — новое
            time.sleep(min(60, 2 * 2 ** attempt))
    raise RuntimeError("unreachable")


def trading_dates(start: str, end: str) -> list[str]:
    out: list[str] = []
    with httpx.Client(timeout=60) as http:
        s = 0
        while True:
            d = _get(http, f"{ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json",
                     {"from": start, "till": end, "start": s, "iss.meta": "off", "iss.only": "history",
                      "history.columns": "TRADEDATE,VALUE"})
            rows = d["history"]["data"]
            if not rows:
                return sorted(set(out))
            out += [r[0] for r in rows if (r[1] or 0) > 0]
            s += len(rows)


COLS = "TRADEDATE,SECID,SHORTNAME,OPEN,LOW,HIGH,CLOSE,VOLUME,VALUE,NUMTRADES,WAPRICE"


def _day(args) -> list[list]:
    date, cache = args
    cpath = os.path.join(cache, f"{date}.json")
    if os.path.exists(cpath):
        with open(cpath) as f:
            return json.load(f)
    rows: list[list] = []
    s = 0
    while True:
        d = _get(None, f"{ISS}/history/engines/stock/markets/shares/boards/TQBR/securities.json",
                 {"date": date, "start": s, "iss.meta": "off", "iss.only": "history,history.cursor",
                  "history.columns": COLS})
        chunk = d["history"]["data"]
        rows += chunk
        total = d["history.cursor"]["data"][0][1]
        s += len(chunk)
        if not chunk or s >= total:
            break
    with open(cpath + ".tmp", "w") as f:
        json.dump(rows, f)
    os.replace(cpath + ".tmp", cpath)
    return rows


def daily_panel(dates: list[str], path: str, workers: int = 4) -> str:
    cache = os.path.join(os.path.dirname(path), "cache_daily")
    os.makedirs(cache, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool:
        parts = list(pool.map(_day, [(d, cache) for d in dates]))
    df = pd.DataFrame([r for p in parts for r in p], columns=COLS.lower().split(","))
    df = df.rename(columns={"tradedate": "date"})
    df.to_csv(path, index=False, compression="gzip")
    return path


def splits(path: str) -> str:
    with httpx.Client(timeout=60) as http:
        d = _get(http, f"{ISS}/statistics/engines/stock/splits.json", {"iss.meta": "off"})
    pd.DataFrame(d["splits"]["data"], columns=d["splits"]["columns"]).to_csv(path, index=False)
    return path


def _divs(secid: str) -> list[list]:
    d = _get(None, f"{ISS}/securities/{secid}/dividends.json", {"iss.meta": "off"})
    blk = d.get("dividends", {"columns": [], "data": []})
    cols = blk["columns"]
    return [dict(zip(cols, r)) | {"secid": secid} for r in blk["data"]]


def dividends(secids: list[str], dates: list[str], path: str, workers: int = 4) -> str:
    """Дивиденды с экс-датой: последний торговый день, когда покупка ещё
    попадала в реестр, плюс один (T+2 до T1_SWITCH, T+1 после)."""
    with ThreadPoolExecutor(workers) as pool:
        rows = [r for part in pool.map(_divs, secids) for r in part]
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["secid", "registryclosedate", "value", "currencyid", "ex_date"])
        df.to_csv(path, index=False)
        return path
    ds = pd.Index(sorted(dates))
    ex = []
    for rc in df["registryclosedate"]:
        lag = 2 if rc < T1_SWITCH else 1
        k = ds.searchsorted(rc, side="right") - 1      # последний торговый день <= даты отсечки
        last_buy = k - lag                             # покупка с расчётом к отсечке
        ex.append(ds[last_buy + 1] if 0 <= last_buy + 1 < len(ds) else None)
    df["ex_date"] = ex
    df.to_csv(path, index=False)
    return path


def lot_sizes(path: str) -> str:
    with httpx.Client(timeout=60) as http:
        d = _get(http, f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities.json",
                 {"iss.meta": "off", "iss.only": "securities", "securities.columns": "SECID,SHORTNAME,LOTSIZE,MINSTEP"})
    pd.DataFrame(d["securities"]["data"], columns=["secid", "shortname", "lotsize", "minstep"]).to_csv(path, index=False)
    return path


def _candles(args) -> list[list]:
    secid, start, end, interval, cache = args
    cpath = os.path.join(cache, f"{secid}_{interval}.json")
    if os.path.exists(cpath):
        with open(cpath) as f:
            return json.load(f)
    rows: list[list] = []
    s = 0
    while True:
        d = _get(None, f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities/{secid}/candles.json",
                 {"from": start, "till": end, "interval": interval, "start": s, "iss.meta": "off",
                  "iss.only": "candles"})
        c = d["candles"]
        if not c["data"]:
            break
        idx = {k: i for i, k in enumerate(c["columns"])}
        rows += [[secid, r[idx["begin"]], r[idx["open"]], r[idx["high"]], r[idx["low"]], r[idx["close"]],
                  r[idx["volume"]], r[idx["value"]]] for r in c["data"]]
        s += len(c["data"])
    with open(cpath + ".tmp", "w") as f:
        json.dump(rows, f)
    os.replace(cpath + ".tmp", cpath)
    return rows


def candles(secids: list[str], start: str, end: str, path: str, interval: int = 60, workers: int = 4) -> str:
    cache = os.path.join(os.path.dirname(path), "cache_candles")
    os.makedirs(cache, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool:
        parts = list(pool.map(_candles, [(s, start, end, interval, cache) for s in secids]))
    df = pd.DataFrame([r for p in parts for r in p],
                      columns=["secid", "begin", "open", "high", "low", "close", "volume", "value"])
    df.to_csv(path, index=False, compression="gzip")
    return path


def main() -> None:
    out = sys.argv[1]
    start = sys.argv[2] if len(sys.argv) > 2 else "2019-01-01"
    end = sys.argv[3] if len(sys.argv) > 3 else "2026-10-16"
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    dates = trading_dates(start, end)
    print(f"dates {len(dates)} {dates[0]}..{dates[-1]}", flush=True)
    daily_panel(dates, os.path.join(out, "tqbr_daily.csv.gz"))
    print(f"daily panel done {time.time() - t0:.0f}s", flush=True)
    splits(os.path.join(out, "splits.csv"))
    lot_sizes(os.path.join(out, "lots.csv"))
    panel = pd.read_csv(os.path.join(out, "tqbr_daily.csv.gz"))
    # кандидаты: когда-либо дешевле 300 ₽ с медианным оборотом от 20 млн ₽/день в каком-либо году
    panel["year"] = panel["date"].str.slice(0, 4)
    g = panel[panel["close"] < 300].groupby(["secid", "year"])["value"].median()
    cand = sorted(set(g[g > 20e6].index.get_level_values(0)))
    print(f"candidates {len(cand)}", flush=True)
    dividends(cand, dates, os.path.join(out, "dividends.csv"))
    print(f"dividends done {time.time() - t0:.0f}s", flush=True)
    candles(cand, start, end, os.path.join(out, "tqbr_hourly.csv.gz"))
    print(f"hourly done {time.time() - t0:.0f}s", flush=True)
    with gzip.open(os.path.join(out, "DONE.gz"), "wt") as f:
        f.write(json.dumps({"dates": len(dates), "candidates": cand}))


if __name__ == "__main__":
    main()
