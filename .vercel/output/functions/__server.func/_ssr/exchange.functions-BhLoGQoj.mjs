import { a as object, o as string } from "../_libs/zod.mjs";
import { t as createServerFn } from "./ssr.mjs";
import { t as createServerRpc } from "./createServerRpc-A6pJPYTF.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/exchange.functions-BhLoGQoj.js
var MEXC = "https://api.mexc.com";
async function signHeaders(apiKey, secret, paramString) {
	const { createHmac } = await import("node:crypto");
	const timestamp = Date.now().toString();
	return {
		ApiKey: apiKey,
		"Request-Time": timestamp,
		Signature: createHmac("sha256", secret).update(apiKey + timestamp + paramString).digest("hex"),
		"Content-Type": "application/json"
	};
}
var testMexcKeys_createServerFn_handler = createServerRpc({
	id: "571df81d03c5fb265880f8c4bd0441b7c938fa6377cf93b44ffe2500d69ad8fc",
	name: "testMexcKeys",
	filename: "src/lib/exchange.functions.ts"
}, (opts) => testMexcKeys.__executeServer(opts));
var testMexcKeys = createServerFn({ method: "POST" }).validator(object({
	apiKey: string().trim().min(8).max(128),
	secret: string().trim().min(8).max(128)
})).handler(testMexcKeys_createServerFn_handler, async ({ data }) => {
	try {
		const headers = await signHeaders(data.apiKey, data.secret, "");
		const res = await fetch(`${MEXC}/api/v1/private/account/assets`, {
			headers,
			signal: AbortSignal.timeout(12e3)
		});
		const body = await res.json();
		if (!res.ok || body.success === false || body.code && body.code !== 0) return {
			ok: false,
			message: body.message || `Биржа ответила ${res.status}. Проверьте ключ, секрет и права API.`
		};
		const rows = body.data ?? [];
		const usdt = rows.find((r) => r.currency === "USDT") ?? rows[0];
		if (!usdt) return {
			ok: false,
			message: "Ключ принят, но фьючерсный баланс пуст."
		};
		return {
			ok: true,
			currency: usdt.currency || "USDT",
			available: Number(usdt.availableBalance ?? 0),
			equity: Number(usdt.equity ?? usdt.availableBalance ?? 0)
		};
	} catch (error) {
		return {
			ok: false,
			message: error instanceof Error ? error.message : "Сеть недоступна"
		};
	}
});
var testTelegram_createServerFn_handler = createServerRpc({
	id: "256c19088e22efcdd277e6ff837959b46c19dfe1b8cc8344bb8ceaa82e95353f",
	name: "testTelegram",
	filename: "src/lib/exchange.functions.ts"
}, (opts) => testTelegram.__executeServer(opts));
var testTelegram = createServerFn({ method: "POST" }).validator(object({
	token: string().trim().min(20).max(120),
	chatId: string().trim().min(1).max(64)
})).handler(testTelegram_createServerFn_handler, async ({ data }) => {
	const base = `https://api.telegram.org/bot${data.token}`;
	try {
		const me = await (await fetch(`${base}/getMe`, { signal: AbortSignal.timeout(12e3) })).json();
		if (!me.ok || !me.result?.username) return {
			ok: false,
			message: me.description || "Токен бота не принят Telegram."
		};
		const sent = await (await fetch(`${base}/sendMessage`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				chat_id: data.chatId,
				text: "Коридор на связи. Ключ Telegram принят, тестовое сообщение доставлено."
			}),
			signal: AbortSignal.timeout(12e3)
		})).json();
		if (!sent.ok) return {
			ok: false,
			message: sent.description || "Бот найден, но сообщение не ушло. Напишите боту /start и проверьте chat id."
		};
		return {
			ok: true,
			username: me.result.username
		};
	} catch (error) {
		return {
			ok: false,
			message: error instanceof Error ? error.message : "Telegram недоступен"
		};
	}
});
async function loadTickers() {
	const res = await fetch(`${MEXC}/api/v1/contract/ticker`, { signal: AbortSignal.timeout(12e3) });
	if (!res.ok) throw new Error(`Котировки недоступны (${res.status})`);
	const body = await res.json();
	const rows = Array.isArray(body.data) ? body.data : [];
	const parsed = [];
	for (const row of rows) {
		if (!row || typeof row !== "object") continue;
		const item = row;
		const symbol = String(item.symbol ?? "");
		if (!symbol.endsWith("_USDT")) continue;
		const lastPrice = Number(item.lastPrice);
		const amount24 = Number(item.amount24);
		if (!Number.isFinite(lastPrice) || lastPrice <= 0 || lastPrice >= 1e5) continue;
		if (!Number.isFinite(amount24) || amount24 < 8e6) continue;
		parsed.push({
			symbol,
			lastPrice,
			amount24,
			high24Price: Number(item.high24Price) || lastPrice,
			lower24Price: Number(item.lower24Price) || lastPrice,
			riseFallRate: Number(item.riseFallRate) || 0
		});
	}
	parsed.sort((a, b) => b.amount24 - a.amount24);
	return parsed.slice(0, 12);
}
var fetchMarket_createServerFn_handler = createServerRpc({
	id: "aaebb364c72b4da32a4e0d2d494418f58e191e8d091c8e4f31a5af330eacbc41",
	name: "fetchMarket",
	filename: "src/lib/exchange.functions.ts"
}, (opts) => fetchMarket.__executeServer(opts));
var fetchMarket = createServerFn({ method: "GET" }).handler(fetchMarket_createServerFn_handler, async () => loadTickers());
//#endregion
export { fetchMarket_createServerFn_handler, testMexcKeys_createServerFn_handler, testTelegram_createServerFn_handler };
