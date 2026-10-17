"""Проверка подключения к Finam Trade API: `python -m finam_robot.check`.

Токен берётся только из переменной окружения FINAM_API_TOKEN, не из
аргументов командной строки: аргументы видны другим пользователям в списке
процессов (ps). Задавайте его через EnvironmentFile/.env с правами 600,
а не через `export` в интерактивном shell. Только чтение: токен,
счета, права на маркет-дату, инструмент, шаг цены, ГО, стакан, время
сервера. Заявок не выставляет.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

from .gateway import FinamAuthError, FinamRestGateway, FinamUnavailableError, dec, money

_QUOTE_RANK = {"QUOTE_LEVEL_LAST_PRICE": 1, "QUOTE_LEVEL_BEST_BID_OFFER": 2, "QUOTE_LEVEL_DEPTH_OF_MARKET": 3,
               "QUOTE_LEVEL_DEPTH_OF_BOOK": 3, "QUOTE_LEVEL_ACCESS_FORBIDDEN": -1}


def best_md_permission(perms: list, mic: str) -> dict | None:
    """Права на площадку: сначала строки именно этой MIC, иначе — широкие
    (worldwide / country / continent). Из выбранных — с минимальной задержкой
    и самым подробным уровнем котировок."""
    exact = [p for p in perms if mic and p.get("mic") == mic]
    pool = exact or [p for p in perms if p.get("worldwide") or p.get("country") or p.get("continent")
                     or (not mic and p.get("mic"))]
    if not pool:
        return None
    return min(pool, key=lambda p: (int(p.get("delay_minutes") or 0),
                                    -_QUOTE_RANK.get(str(p.get("quote_level", "")), 0)))

OK, WARN, FAIL = "✅", "⚠️", "⛔"


async def run_check(secret: str, symbol: str, account_id: str = "", *, transport=None, out=print) -> int:
    """0 — всё нужное роботу есть, 1 — есть предупреждения, 2 — работать нельзя."""
    if not secret:
        out(f"{FAIL} FINAM_API_TOKEN не задан")
        return 2
    gw = FinamRestGateway(secret, account_id, transport=transport)
    worst = 0

    def note(level: str, text: str) -> None:
        nonlocal worst
        worst = max(worst, {OK: 0, WARN: 1, FAIL: 2}[level])
        out(f"{level} {text}")

    try:
        try:
            await gw.connect()
        except FinamAuthError as e:
            note(FAIL, f"{e}. Токен истёк, отозван или скопирован не полностью: "
                       "создайте новый на портале Finam Trade API.")
            return worst
        except (FinamUnavailableError, httpx.TransportError) as e:
            note(FAIL, f"Finam API или сеть недоступны ({e}). Токен не проверен — повторите позже.")
            return worst
        note(OK, "API-токен принят, JWT получен")

        details = await gw.token_details()
        ids = [str(x) for x in details.get("account_ids", [])]
        if details.get("readonly"):
            note(WARN, "токен только для чтения: торговать по нему нельзя (бумажный режим работает)")
        else:
            note(OK, "токен с правом торговли")
        if details.get("expires_at"):
            out(f"   срок действия JWT: до {details['expires_at']}")
        if not ids:
            note(FAIL, "в токене нет ни одного счёта")
            return worst
        if account_id and account_id not in ids:
            note(FAIL, f"FINAM_ACCOUNT_ID={account_id} нет в токене; доступны: {', '.join(ids)}")
            return worst
        gw.account_id = account_id or ids[0]
        if account_id:
            note(OK, f"счёт {gw.account_id}")
        else:
            note(WARN, f"FINAM_ACCOUNT_ID не задан, без него робот не стартует: "
                       f"задайте FINAM_ACCOUNT_ID={ids[0]} (в токене: {', '.join(ids)})")

        mic = symbol.split("@")[-1] if "@" in symbol else ""
        perm = best_md_permission(details.get("md_permissions", []), mic)
        if perm is None or _QUOTE_RANK.get(str(perm.get("quote_level", "")), 0) < 0:
            note(WARN, f"в токене нет прав на маркет-дату площадки {mic or symbol}: стакан может не отдаваться")
        elif int(perm.get("delay_minutes") or 0) > 0:
            note(WARN, f"маркет-дата {mic} с задержкой {perm.get('delay_minutes')} мин — для торговли непригодно")
        else:
            note(OK, f"маркет-дата {mic or 'площадки'} в реальном времени, уровень {perm.get('quote_level', '?')}")

        acc = await gw.get_account()
        out(f"   тип счёта {acc.get('type', '?')}, статус {acc.get('status', '?')}, "
            f"эквити {dec(acc.get('equity')):.2f}, позиций {len(acc.get('positions', []))}")

        try:
            asset = await gw.get_asset_info(symbol)
        except RuntimeError as e:
            hint = "; проверьте FINAM_SYMBOL" if " 404 " in f" {e} " or "not found" in str(e).lower() else ""
            note(FAIL, f"инструмент {symbol} недоступен ({e}){hint}")
            return worst
        note(OK, f"{symbol}: шаг цены {asset.tick:g}, знаков {asset.decimals}, лот {asset.lot_size:g}")

        params = await gw.get_asset_params(symbol)
        lc, sc = money(params.get("long_collateral")), money(params.get("short_collateral"))
        tradeable = params.get("is_tradable", params.get("tradeable"))
        if tradeable is False:
            note(WARN, f"{symbol} сейчас не торгуется на этом счёте")
        if lc > 0 and sc > 0:
            note(OK, f"ГО на лот: лонг {lc:.2f}, шорт {sc:.2f}")
        else:
            note(WARN, "ГО на лот не пришло: робот не сможет посчитать размер позиции")

        book = await gw.get_order_book(symbol)
        rows = book.get("orderbook", {}).get("rows", [])
        bids = [r for r in rows if r.get("buy_size") or r.get("buySize")]
        asks = [r for r in rows if r.get("sell_size") or r.get("sellSize")]
        if bids and asks:
            best_bid = max(dec(r.get("price")) for r in bids)
            best_ask = min(dec(r.get("price")) for r in asks)
            note(OK, f"стакан: {len(bids)} bid / {len(asks)} ask, лучшие {best_bid:g} / {best_ask:g}")
            out(f"   поля строки: {', '.join(sorted(rows[0].keys()))}")
        else:
            note(WARN, "стакан пуст: торги закрыты или нет прав на маркет-дату")

        out(f"   время сервера: {await gw.server_time()}")
        return worst
    except Exception as e:  # сеть, лимиты, неожиданный формат ответа
        note(FAIL, f"ошибка: {e!r}")
        return worst
    finally:
        await gw.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="finam_robot.check")
    ap.add_argument("--symbol", default=os.getenv("FINAM_SYMBOL", "CRZ6@RTSX"))
    args = ap.parse_args(argv)
    code = asyncio.run(run_check(os.getenv("FINAM_API_TOKEN", ""), args.symbol, os.getenv("FINAM_ACCOUNT_ID", "")))
    sys.exit(code)


if __name__ == "__main__":
    main()
