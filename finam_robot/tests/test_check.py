import asyncio
import json

import httpx

from finam_robot.check import run_check
from finam_robot.gateway import FinamRestGateway

JWT = "eyJ.test.jwt"


def finam_mock(*, token_ok=True, delay=0, readonly=False, accounts=("1899011",)):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append((request.method, path, request.headers.get("Authorization")))
        if path == "/sessions":
            if not token_ok:
                return httpx.Response(401, json={"code": 16, "message": "Api token could not be verified",
                                                 "details": []})
            assert json.loads(request.content)["secret"] == "sk_test"
            return httpx.Response(200, json={"token": JWT})
        assert request.headers.get("Authorization") == JWT
        if path == "/sessions/details":
            return httpx.Response(200, json={
                "created_at": "2026-10-17T08:00:00Z", "expires_at": "2026-10-17T08:15:00Z",
                "md_permissions": [{"quote_level": "QUOTE_LEVEL_DEPTH_OF_BOOK", "delay_minutes": delay, "mic": "RTSX"}],
                "account_ids": list(accounts), "readonly": readonly})
        if path == "/assets/clock":
            return httpx.Response(200, json={"timestamp": "2026-10-17T08:00:01Z"})
        if path.startswith("/accounts/"):
            return httpx.Response(200, json={"account_id": accounts[0], "type": "UNION",
                                             "status": "ACCOUNT_ACTIVE", "equity": {"value": "30000.00"},
                                             "positions": []})
        if path == "/assets/CRZ6@RTSX/params":
            go = {"currency_code": "RUB", "units": "1232", "nanos": 710000000}
            return httpx.Response(200, json={"symbol": "CRZ6@RTSX", "is_tradable": True,
                                             "long_collateral": go, "short_collateral": go})
        if path == "/assets/CRZ6@RTSX":
            return httpx.Response(200, json={"symbol": "CRZ6@RTSX", "decimals": 3, "min_step": "1",
                                             "lot_size": {"value": "1"}})
        if path == "/instruments/CRZ6@RTSX/orderbook":
            return httpx.Response(200, json={"symbol": "CRZ6@RTSX", "orderbook": {"rows": [
                {"price": {"value": "12.645"}, "buy_size": {"value": "120"}, "action": "ACTION_ADD"},
                {"price": {"value": "12.646"}, "sell_size": {"value": "80"}, "action": "ACTION_ADD"}]}})
        return httpx.Response(404, json={"code": 5, "message": "not found"})

    return httpx.MockTransport(handler), seen


def check(**kw):
    transport, seen = finam_mock(**{k: v for k, v in kw.items() if k in ("token_ok", "delay", "readonly", "accounts")})
    lines = []
    code = asyncio.run(run_check("sk_test", "CRZ6@RTSX", kw.get("account_id", ""),
                                 transport=transport, out=lines.append))
    return code, "\n".join(lines), seen


def test_invalid_token_reports_finam_message():
    code, text, _ = check(token_ok=False)
    assert code == 2
    assert "Api token could not be verified" in text and "401" in text


def test_full_check_passes_and_never_posts_orders():
    code, text, seen = check()
    assert code == 0, text
    assert "шаг цены 0.001" in text and "1232.71" in text and "12.645 / 12.646" in text
    assert not any(m == "POST" and "/orders" in p for m, p, _ in seen)


def test_delayed_market_data_and_readonly_are_warnings():
    code, text, _ = check(delay=15, readonly=True)
    assert code == 1
    assert "задержкой 15" in text and "только для чтения" in text


def test_unknown_account_id_fails():
    code, text, _ = check(account_id="999")
    assert code == 2 and "999" in text


def test_gateway_parses_tick_margin_and_position():
    transport, _ = finam_mock()

    async def scenario():
        gw = FinamRestGateway("sk_test", "1899011", transport=transport)
        await gw.connect()
        try:
            asset = await gw.get_asset_info("CRZ6@RTSX")
            margin = await gw.get_margin_per_lot("CRZ6@RTSX")
            pos = await gw.get_position("CRZ6@RTSX")
            eq = await gw.get_equity()
        finally:
            await gw.close()
        return asset, margin, pos, eq

    asset, margin, pos, eq = asyncio.run(scenario())
    assert asset.tick == 0.001 and asset.decimals == 3
    assert margin == (1232.71, 1232.71)
    assert pos == (0, 0.0) and eq == 30000.0
