globalThis.__nitro_main__ = import.meta.url;
import { i as toEventHandler, n as HTTPError, o as NodeResponse, r as defineLazyEventHandler, t as H3Core } from "./_libs/h3+rou3+srvx.mjs";
import { t as create } from "./_libs/zustand.mjs";
import { t as createServerFn } from "./_libs/@tanstack/start-client-core+[...].mjs";
import { a as object, o as string } from "./_libs/zod.mjs";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
//#region node_modules/nitro/dist/runtime/internal/route-rules.mjs
var headers = ((m) => function headersRouteRule(event) {
	for (const [key, value] of Object.entries(m.options || {})) event.res.headers.set(key, value);
});
//#endregion
//#region src/lib/desk-store.ts
var DEFAULT_SETTINGS = {
	leverage: 5,
	marginPct: 8,
	levels: 7,
	sellLevels: 4,
	deposit: 320,
	maxDdPct: 4,
	maxPrice: 1,
	stepPct: 0,
	rangePct: 12,
	geo: 1.12,
	volumeK: 1.2
};
var STORAGE_KEY = "koridor-desk-v1";
var logSeq$1 = 1;
function stamp$1() {
	return (/* @__PURE__ */ new Date()).toLocaleTimeString("ru-RU", {
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit"
	});
}
function freshBook(deposit) {
	return {
		symbol: "",
		price: 0,
		cash: deposit,
		position: 0,
		avg: 0,
		equity: deposit,
		trades: 0,
		wins: 0,
		levels: [],
		series: [{
			t: stamp$1(),
			equity: deposit
		}]
	};
}
function buildLevels(price, step, buys, sells) {
	const levels = [];
	for (let i = 1; i <= buys; i += 1) levels.push({
		price: price * (1 - step * i),
		side: "buy",
		filled: false
	});
	for (let i = 1; i <= sells; i += 1) levels.push({
		price: price * (1 + step * i),
		side: "sell",
		filled: false
	});
	return levels;
}
var useDesk = create((set, get) => ({
	keys: {
		mexcKey: "",
		mexcSecret: "",
		tgToken: "",
		tgChat: ""
	},
	settings: { ...DEFAULT_SETTINGS },
	hydrated: false,
	serverReady: false,
	mexcStatus: "Ключ не проверен",
	tgStatus: "Бот не проверен",
	running: false,
	autonomous: true,
	rebuild: false,
	lessons: 0,
	role: "owner",
	activeRenter: "",
	renters: [],
	...freshBook(DEFAULT_SETTINGS.deposit),
	logs: [{
		id: logSeq$1++,
		ts: 0,
		tone: "info",
		local: true,
		text: "Ключи не записываются. Вывод с MEXC в роботе отсутствует."
	}],
	setKeys: (patch) => set((s) => ({ keys: {
		...s.keys,
		...patch
	} })),
	setSettings: (patch) => set((s) => ({ settings: {
		...s.settings,
		...patch
	} })),
	setHydrated: (value) => set({ hydrated: value }),
	setMexcStatus: (value) => set({ mexcStatus: value }),
	setTgStatus: (value) => set({ tgStatus: value }),
	setAutonomous: (value) => set({ autonomous: value }),
	setRole: (role) => set({
		role,
		activeRenter: role === "owner" ? "" : get().activeRenter
	}),
	beginRental: (name) => {
		const clean = name.trim().slice(0, 32);
		if (!clean) return;
		set((s) => ({
			role: "renter",
			activeRenter: clean,
			renters: s.renters.some((r) => r.name === clean) ? s.renters : [...s.renters, {
				name: clean,
				realized: 0,
				fee: 0
			}]
		}));
		get().pushLog("info", `Аренда для ${clean}. Ставка 5% с прибыли, без вывода с биржи.`);
		saveDesk();
	},
	forgetKeys: () => set({ keys: {
		mexcKey: "",
		mexcSecret: "",
		tgToken: "",
		tgChat: ""
	} }),
	ingestServer: (snap) => set((s) => ({
		running: snap.running,
		autonomous: snap.running,
		lessons: snap.lessons,
		role: snap.role,
		activeRenter: snap.activeRenter,
		renters: snap.renters,
		symbol: snap.symbol,
		price: snap.price,
		cash: snap.cash,
		position: snap.position,
		avg: snap.avg,
		equity: snap.equity,
		trades: snap.trades,
		wins: snap.wins,
		levels: snap.levels,
		series: snap.series,
		settings: s.serverReady ? s.settings : {
			...DEFAULT_SETTINGS,
			...snap.settings
		},
		serverReady: true,
		logs: [...snap.logs.map((line) => ({
			...line,
			local: false
		})), ...s.logs.filter((line) => line.local)].slice(0, 80)
	})),
	pushLog: (tone, text) => set((s) => ({ logs: [{
		id: logSeq$1++,
		ts: Date.now(),
		tone,
		text,
		local: true
	}, ...s.logs].slice(0, 80) })),
	start: () => set({ running: true }),
	quickBuy: (symbol, price) => {
		const state = get();
		if (!(price > 0) || !symbol) {
			state.pushLog("warn", "Нет цены для быстрой покупки");
			return;
		}
		let { cash, position, avg, trades, wins } = state;
		const unit = Math.max(cash, 1) * (state.settings.marginPct / 100) * state.settings.leverage / Math.max(state.settings.levels, 1);
		const qty = unit / price;
		const nextPos = position + qty;
		avg = nextPos > 0 ? (avg * position + price * qty) / nextPos : 0;
		cash -= unit * 1e-4;
		const equity = cash + (price - avg) * nextPos;
		set((s) => ({
			symbol,
			price,
			cash,
			position: nextPos,
			avg,
			trades,
			wins,
			equity,
			series: [...s.series, {
				t: stamp$1(),
				equity: Number(equity.toFixed(2))
			}].slice(-48)
		}));
		get().pushLog("ok", `Быстрая покупка ${symbol.replace("_", "/")} по ${price.toPrecision(5)}`);
	},
	stop: (reason) => {
		const state = get();
		if (state.position > 0 && state.price > 0) {
			const pnl = (state.price - state.avg) * state.position;
			const cash = state.cash + pnl;
			set((s) => ({
				running: false,
				autonomous: false,
				position: 0,
				avg: 0,
				cash,
				equity: cash,
				trades: state.trades + 1,
				wins: state.wins + (pnl >= 0 ? 1 : 0),
				series: [...s.series, {
					t: stamp$1(),
					equity: Number(cash.toFixed(2))
				}].slice(-48)
			}));
			get().pushLog(pnl >= 0 ? "ok" : "warn", `Стоп. Позиция закрыта ${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)} USDT`);
			return;
		}
		set({
			running: false,
			autonomous: false
		});
		if (reason) get().pushLog("warn", reason);
	},
	resetBook: () => set({
		...freshBook(get().settings.deposit),
		running: false,
		rebuild: false
	}),
	requestRebuild: () => set({ rebuild: true }),
	applyTick: (symbol, price, step) => {
		const state = get();
		if (!state.running || state.serverReady) return;
		let { cash, position, avg, trades, wins, levels } = state;
		const { settings } = state;
		let nextLevels = levels;
		if (state.symbol !== symbol || nextLevels.length === 0 || state.rebuild) {
			if (state.symbol && state.symbol !== symbol && position !== 0) {
				const pnl = (price - avg) * position;
				cash += pnl;
				trades += 1;
				if (pnl >= 0) wins += 1;
				position = 0;
				avg = 0;
			}
			nextLevels = buildLevels(price, step, settings.levels, settings.sellLevels);
		}
		const unit = Math.max(cash, 1) * (settings.marginPct / 100) * settings.leverage / Math.max(settings.levels, 1);
		nextLevels = nextLevels.map((level) => {
			if (level.filled) return level;
			if (level.side === "buy" && price <= level.price) {
				const qty = unit / level.price;
				const nextPos = position + qty;
				avg = nextPos > 0 ? (avg * position + level.price * qty) / nextPos : 0;
				position = nextPos;
				cash -= unit * 1e-4;
				return {
					...level,
					filled: true
				};
			}
			if (level.side === "sell" && price >= level.price && position > 0) {
				const qty = Math.min(position, unit * .55 / level.price);
				const pnl = (level.price - avg) * qty;
				cash += pnl;
				position -= qty;
				trades += 1;
				if (pnl >= 0) wins += 1;
				return {
					...level,
					filled: true
				};
			}
			return level;
		});
		const equity = cash + (position > 0 ? (price - avg) * position : 0);
		set({
			symbol,
			price,
			cash,
			position,
			avg,
			trades,
			wins,
			levels: nextLevels,
			equity,
			rebuild: false,
			series: [...state.series, {
				t: stamp$1(),
				equity: Number(equity.toFixed(2))
			}].slice(-48)
		});
		if ((settings.deposit - equity) / Math.max(settings.deposit, 1) * 100 > settings.maxDdPct) get().stop(`Стоп: просадка бумаги`);
	}
}));
function saveDesk() {
	const { settings } = useDesk.getState();
	localStorage.setItem(STORAGE_KEY, JSON.stringify({ settings }));
}
DEFAULT_SETTINGS.deposit;
//#endregion
//#region src/lib/exchange.functions.ts
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
createServerFn({ method: "POST" }).validator(object({
	apiKey: string().trim().min(8).max(128),
	secret: string().trim().min(8).max(128)
})).handler(async ({ data }) => {
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
createServerFn({ method: "POST" }).validator(object({
	token: string().trim().min(20).max(120),
	chatId: string().trim().min(1).max(64)
})).handler(async ({ data }) => {
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
createServerFn({ method: "GET" }).handler(async () => loadTickers());
//#endregion
//#region src/lib/whitelist.ts
var MEXC_WHITELIST_IP = "212.164.64.196";
//#endregion
//#region src/lib/engine.server.ts
var RENT_FEE = .05;
var FILE = join(process.cwd(), "data", "engine.json");
var LOCK = join(process.cwd(), "data", "engine.lock");
var PID = process.pid;
var logSeq = 1;
var ticking = false;
var warnedEmpty = false;
var timer = null;
var announced = false;
function stamp() {
	return (/* @__PURE__ */ new Date()).toLocaleTimeString("ru-RU", {
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit"
	});
}
function fresh(deposit) {
	return {
		symbol: "",
		anchor: 0,
		price: 0,
		cash: deposit,
		position: 0,
		avg: 0,
		equity: deposit,
		trades: 0,
		wins: 0,
		levels: [],
		series: [{
			t: stamp(),
			equity: deposit
		}]
	};
}
function blank() {
	return {
		running: true,
		lessons: 0,
		role: "owner",
		activeRenter: "",
		renters: [],
		settings: { ...DEFAULT_SETTINGS },
		rebuild: false,
		ticks: 0,
		beat: Date.now(),
		logs: [{
			id: logSeq++,
			ts: Date.now(),
			tone: "info",
			text: "Сервер сетки запущен. Цикл идёт, пока жив процесс приложения."
		}],
		...fresh(DEFAULT_SETTINGS.deposit)
	};
}
var book = load();
function load() {
	try {
		const raw = readFileSync(FILE, "utf8");
		const parsed = JSON.parse(raw);
		const next = blank();
		return {
			...next,
			...parsed,
			settings: {
				...DEFAULT_SETTINGS,
				...parsed.settings ?? {}
			},
			logs: Array.isArray(parsed.logs) ? parsed.logs.slice(0, 80) : next.logs,
			renters: Array.isArray(parsed.renters) ? parsed.renters : [],
			series: Array.isArray(parsed.series) ? parsed.series.slice(-48) : next.series,
			levels: Array.isArray(parsed.levels) ? parsed.levels : []
		};
	} catch {
		return blank();
	}
}
function save() {
	try {
		mkdirSync(dirname(FILE), { recursive: true });
		writeFileSync(FILE, JSON.stringify(book));
	} catch {}
}
function log(tone, text) {
	book.logs = [{
		id: logSeq++,
		ts: Date.now(),
		tone,
		text
	}, ...book.logs].slice(0, 80);
}
function offsets(count, step, geo, range) {
	const raw = [];
	const g = Math.abs(geo - 1) < .001 ? 1 : geo;
	for (let i = 1; i <= count; i += 1) raw.push(g === 1 ? step * i : step * (g ** i - 1) / (g - 1));
	const last = raw[raw.length - 1] || step;
	const scale = last > range ? range / last : 1;
	return raw.map((value) => value * scale);
}
function weights(count, k) {
	const raw = Array.from({ length: count }, (_, index) => k ** index);
	const sum = raw.reduce((total, value) => total + value, 0) || 1;
	return raw.map((value) => value / sum);
}
function buildGrid(price, step) {
	const { settings } = book;
	const range = Math.max(settings.rangePct, 3) / 100;
	const buyAt = offsets(settings.levels, step, settings.geo, range);
	const sellAt = offsets(settings.sellLevels, step, settings.geo, range);
	const levels = [];
	for (const distance of buyAt) levels.push({
		price: price * (1 - distance),
		side: "buy",
		filled: false
	});
	for (const distance of sellAt) levels.push({
		price: price * (1 + distance),
		side: "sell",
		filled: false
	});
	return levels;
}
function credit(pnl) {
	if (!book.activeRenter || pnl === 0) return;
	const fee = pnl > 0 ? pnl * RENT_FEE : 0;
	book.renters = book.renters.map((renter) => renter.name === book.activeRenter ? {
		...renter,
		realized: renter.realized + pnl,
		fee: renter.fee + fee
	} : renter);
	if (fee > 0) log("info", `Аренда 5%: ${fee.toFixed(2)} USDT в журнале. Вывод с MEXC не вызывается.`);
}
function learn() {
	if (book.trades < 4 || book.trades % 4 !== 0) return;
	const rate = book.wins / book.trades;
	const next = { ...book.settings };
	if (rate < .45 && next.leverage > 2) {
		next.leverage -= 1;
		next.rangePct = Math.min(24, Number((next.rangePct + 1).toFixed(1)));
	} else if (rate > .62 && next.rangePct > 6) next.rangePct = Math.max(6, Number((next.rangePct - .5).toFixed(1)));
	else return;
	book.settings = next;
	book.lessons += 1;
	book.rebuild = true;
	log("ok", `Самонастройка: плечо ${next.leverage}x, коридор ${next.rangePct}%`);
}
function pick(rows) {
	const pool = rows.filter((row) => row.lastPrice > 0 && row.lastPrice <= book.settings.maxPrice);
	if (pool.length === 0) return null;
	const ranked = [...pool].sort((a, b) => {
		const span = (row) => (row.high24Price - row.lower24Price) / row.lastPrice;
		return span(b) * Math.log(b.amount24) - span(a) * Math.log(a.amount24);
	});
	return ranked.find((row) => row.symbol === book.symbol) ?? ranked[0];
}
function resolveStep(row) {
	if (book.settings.stepPct > 0) return book.settings.stepPct / 100;
	const span = Math.max(0, (row.high24Price - row.lower24Price) / row.lastPrice);
	return Math.min(.03, Math.max(.006, span / Math.max(book.settings.levels + 2, 4)));
}
function holdLock() {
	try {
		const raw = readFileSync(LOCK, "utf8");
		const current = JSON.parse(raw);
		if (current.pid !== PID && Date.now() - Number(current.at ?? 0) < 15e3) return false;
	} catch {}
	mkdirSync(dirname(LOCK), { recursive: true });
	writeFileSync(LOCK, JSON.stringify({
		pid: PID,
		at: Date.now()
	}));
	try {
		return JSON.parse(readFileSync(LOCK, "utf8")).pid === PID;
	} catch {
		return false;
	}
}
async function tick() {
	if (ticking) return;
	if (!holdLock()) return;
	ticking = true;
	if (!announced) {
		announced = true;
		book.running = true;
		log("info", `Автозапуск. В ключе MEXC разрешите только IP ${MEXC_WHITELIST_IP}.`);
	}
	book.beat = Date.now();
	book.ticks += 1;
	try {
		if (!book.running) return;
		const row = pick(await loadTickers());
		if (!row) {
			if (!warnedEmpty) {
				log("warn", `Нет ликвидных пар дешевле ${book.settings.maxPrice} USDT`);
				warnedEmpty = true;
			}
			return;
		}
		warnedEmpty = false;
		const price = row.lastPrice;
		const step = resolveStep(row);
		const range = Math.max(book.settings.rangePct, 3) / 100;
		const outside = book.anchor > 0 && (price < book.anchor * (1 - range) || price > book.anchor * (1 + range));
		if (book.symbol !== row.symbol || book.levels.length === 0 || book.rebuild || outside) {
			if (book.symbol && book.symbol !== row.symbol && book.position > 0) {
				const pnl = (price - book.avg) * book.position;
				book.cash += pnl;
				book.trades += 1;
				if (pnl >= 0) book.wins += 1;
				book.position = 0;
				book.avg = 0;
				credit(pnl);
				log(pnl >= 0 ? "ok" : "warn", `Смена пары. Закрыт остаток ${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)} USDT`);
			}
			book.symbol = row.symbol;
			book.anchor = price;
			book.levels = buildGrid(price, step);
			book.rebuild = false;
			log("info", outside ? `Коридор перестроен вокруг ${row.symbol.replace("_", "/")} · ${book.settings.rangePct}%` : `Сетка ${row.symbol.replace("_", "/")} · шаг ${(step * 100).toFixed(2)}% · геометрия ${book.settings.geo}`);
		}
		const feeRate = 1e-4;
		const notion = Math.max(book.cash, 1) * (book.settings.marginPct / 100) * book.settings.leverage;
		const buyWeights = weights(book.settings.levels, book.settings.volumeK);
		let buyCursor = 0;
		const filled = [];
		const sellsLeft = () => book.levels.filter((level) => level.side === "sell" && !level.filled).length;
		book.levels = book.levels.map((level) => {
			if (level.side === "buy") {
				const notional = notion * (buyWeights[buyCursor] ?? buyWeights[buyWeights.length - 1] ?? 1);
				buyCursor += 1;
				if (level.filled || price > level.price) return level;
				const qty = notional / level.price;
				const fee = notional * feeRate;
				const nextPos = book.position + qty;
				book.avg = nextPos > 0 ? (book.avg * book.position + level.price * qty) / nextPos : 0;
				book.position = nextPos;
				book.cash -= fee;
				filled.push(`покупка ${level.price.toPrecision(4)}`);
				return {
					...level,
					filled: true
				};
			}
			if (level.filled || price < level.price || book.position <= 0) return level;
			const parts = Math.max(sellsLeft(), 1);
			const qty = book.position / parts;
			const fee = qty * level.price * feeRate;
			const pnl = (level.price - book.avg) * qty - fee;
			book.cash += pnl;
			book.position -= qty;
			book.trades += 1;
			if (pnl >= 0) book.wins += 1;
			credit(pnl);
			filled.push(`продажа ${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`);
			return {
				...level,
				filled: true
			};
		});
		if (filled.length) log("ok", filled.join(" · "));
		if (!book.levels.some((level) => level.side === "buy" && !level.filled) && book.position <= 1e-7) {
			book.position = 0;
			book.anchor = price;
			book.levels = buildGrid(price, step);
			log("info", "Сетка отработана, выставлена новая");
		}
		const unreal = book.position > 0 ? (price - book.avg) * book.position : 0;
		book.price = price;
		book.equity = book.cash + unreal;
		book.series = [...book.series, {
			t: stamp(),
			equity: Number(book.equity.toFixed(2))
		}].slice(-48);
		const base = book.settings.deposit > 0 ? book.settings.deposit : 1;
		const drawdown = (base - book.equity) / base * 100;
		if (drawdown > book.settings.maxDdPct) {
			flatten(`Стоп: просадка ${drawdown.toFixed(1)}%`);
			book.running = false;
		} else learn();
	} catch (error) {
		log("err", error instanceof Error ? error.message : "Сбой серверного тика");
	} finally {
		ticking = false;
		save();
	}
}
function flatten(reason) {
	if (book.position > 0 && book.price > 0) {
		const fee = book.position * book.price * 1e-4;
		const pnl = (book.price - book.avg) * book.position - fee;
		book.cash += pnl;
		book.equity = book.cash;
		book.trades += 1;
		if (pnl >= 0) book.wins += 1;
		book.position = 0;
		book.avg = 0;
		credit(pnl);
		book.series = [...book.series, {
			t: stamp(),
			equity: Number(book.cash.toFixed(2))
		}].slice(-48);
		log(pnl >= 0 ? "ok" : "warn", `${reason}. Позиция закрыта ${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)} USDT`);
		learn();
		return;
	}
	log("warn", reason);
}
function ensureEngine() {
	if (timer) return;
	timer = setInterval(() => {
		tick();
	}, 1e4);
	tick();
}
//#endregion
//#region server/middleware/engine-boot.ts
ensureEngine();
function engineBootMiddleware(_event, next) {
	return next();
}
//#endregion
//#region scripts/install-page.html?raw
var install_page_default = "<!DOCTYPE html>\n<html lang=\"en\" class=\"device-desktop\">\n  <head>\n    <meta charset=\"utf-8\" />\n    <meta\n      name=\"viewport\"\n      content=\"width=device-width, initial-scale=1, viewport-fit=cover\"\n    />\n    <meta name=\"color-scheme\" content=\"dark\" />\n    <meta name=\"theme-color\" content=\"#000000\" />\n    <meta name=\"apple-mobile-web-app-status-bar-style\" content=\"black\" />\n    <meta name=\"apple-mobile-web-app-title\" content=\"{{APP_NAME}}\" />\n    <title>Add {{APP_NAME}} to your Home Screen</title>\n    <link rel=\"manifest\" href=\"/__grok/manifest.webmanifest\" />\n    <link rel=\"apple-touch-icon\" href=\"/__grok/icon-180.png\" />\n    <link rel=\"stylesheet\" href=\"/__grok/install/styles.css\" />\n    <script>\n      (function () {\n        var ua = navigator.userAgent || \"\";\n        var touch = navigator.maxTouchPoints || 0;\n        var isiPad = /iPad/.test(ua) || (/Macintosh/.test(ua) && touch > 1);\n        var isiPhone = /iPhone|iPod/.test(ua);\n        var isIOS = isiPhone || isiPad;\n        var isAndroid = /Android/i.test(ua);\n        var isAndroidPhone = isAndroid && /Mobile/i.test(ua);\n        var isAndroidTablet = isAndroid && !/Mobile/i.test(ua);\n        var minSide = Math.min(screen.width || 0, screen.height || 0);\n        var maxSide = Math.max(screen.width || 0, screen.height || 0);\n\n        var type = \"desktop\";\n        if (isiPhone) type = \"phone\";\n        else if (isiPad || isAndroidTablet) type = \"tablet\";\n        else if (isAndroidPhone) type = \"phone\";\n        else if (touch > 0 && minSide > 0 && minSide <= 500) type = \"phone\";\n        else if (touch > 0 && minSide > 500 && maxSide <= 1400) type = \"tablet\";\n\n        var iosMajor = null;\n        var osToken = null;\n        var safariToken = null;\n        var iphoneOs = ua.match(/iPhone OS (\\d+)[._]/);\n        var ipadOs = ua.match(/CPU OS (\\d+)[._](\\d+) like Mac OS X/);\n        var safariVer = ua.match(/Version\\/(\\d+)[._]/);\n        if (iphoneOs) osToken = parseInt(iphoneOs[1], 10);\n        else if (ipadOs) osToken = parseInt(ipadOs[1], 10);\n        if (isIOS && safariVer) safariToken = parseInt(safariVer[1], 10);\n        if (osToken != null || safariToken != null) {\n          iosMajor = Math.max(osToken || 0, safariToken || 0);\n        }\n\n        var root = document.documentElement;\n        var classes = [\"device-\" + type];\n        if (iosMajor != null) {\n          root.dataset.ios = String(iosMajor);\n          classes.push(iosMajor >= 27 ? \"ios-27-plus\" : \"ios-below-27\");\n        }\n        root.className = classes.join(\" \");\n      })();\n    <\/script>\n  </head>\n  <body>\n    <div class=\"page\">\n      <header class=\"powered\" aria-label=\"Powered by Grok\">\n        <span class=\"powered-by\">Powered by</span>\n        <span class=\"powered-brand\">\n          <img\n            class=\"grok-logo\"\n            src=\"/__grok/install/assets/homescreen/logo-grok.svg\"\n            width=\"14\"\n            height=\"14\"\n            alt=\"\"\n          />\n          <span class=\"powered-grok\">Grok</span>\n        </span>\n      </header>\n\n      <main class=\"content\">\n        <div class=\"ob\" aria-hidden=\"true\">\n          <img\n            class=\"ob-img ob-phone\"\n            src=\"/__grok/install/assets/homescreen/ob-phone.png\"\n            width=\"338\"\n            height=\"294\"\n            alt=\"\"\n          />\n          <img\n            class=\"ob-img ob-ipad\"\n            src=\"/__grok/install/assets/homescreen/ob-ipad.png\"\n            width=\"634\"\n            height=\"294\"\n            alt=\"\"\n          />\n        </div>\n\n        <section class=\"copy\">\n          <h1>Add {{APP_NAME}} to your&nbsp;Home&nbsp;Screen</h1>\n\n          <div class=\"steps\">\n            <p class=\"step step-tap step-ios27\">\n              <span class=\"muted\">Tap</span>\n              <span class=\"glass glass--icon\" aria-hidden=\"true\">\n                <img src=\"/__grok/install/assets/homescreen/glass-puzzle.svg\" width=\"24\" height=\"24\" alt=\"\" />\n              </span>\n              <span class=\"muted loc loc-phone\">in the bottom bar, then</span>\n              <span class=\"muted loc loc-ipad\">in the tool bar, then</span>\n              <span class=\"glass glass--icon\" aria-hidden=\"true\">\n                <img src=\"/__grok/install/assets/homescreen/glass-share.svg\" width=\"24\" height=\"24\" alt=\"\" />\n              </span>\n            </p>\n\n            <p class=\"step step-tap step-ios-legacy\">\n              <span class=\"muted\">Tap</span>\n              <span class=\"glass glass--icon\" aria-hidden=\"true\">\n                <img src=\"/__grok/install/assets/homescreen/glass-share.svg\" width=\"24\" height=\"24\" alt=\"\" />\n              </span>\n              <span class=\"muted loc loc-phone\">in the bottom bar</span>\n              <span class=\"muted loc loc-ipad\">in the tool bar</span>\n            </p>\n\n            <p class=\"step step-select\">\n              <span class=\"muted\">Select</span>\n              <span class=\"add-label\">\n                <img\n                  class=\"plus-icon\"\n                  src=\"/__grok/install/assets/homescreen/plus.svg\"\n                  width=\"16\"\n                  height=\"16\"\n                  alt=\"\"\n                />\n                <span class=\"add-text\">Add to Home Screen</span>\n              </span>\n            </p>\n          </div>\n        </section>\n      </main>\n\n      <main class=\"content content-desktop\">\n        <section class=\"copy\">\n          <h1>Open this link on your iPhone&nbsp;or&nbsp;iPad</h1>\n          <p class=\"desktop-note\">\n            This page shows how to add {{APP_NAME}} to an iOS Home Screen.\n          </p>\n          <a class=\"desktop-open\" href=\"{{APP_URL}}\">Open {{APP_NAME}}</a>\n        </section>\n      </main>\n    </div>\n  </body>\n</html>\n";
//#endregion
//#region \0virtual:grok-og-identity
var grokOgIdentity = { "site": {} };
//#endregion
//#region scripts/grok-pwa-shared.mjs
/**
* Single source of truth for platform head chrome (PWA, extensions.js, OG),
* shared by the Vite plugin and Nitro middleware. Plain ESM so `node --test`
* and the Nitro bundler can both consume it.
*/
var DEFAULT_APP_NAME = "Grok App";
var OG_SITE_REL_PATH = "src/lib/og/site.json";
var SHARE_META_KEYS = /* @__PURE__ */ new Set([
	"og:title",
	"og:description",
	"og:image",
	"og:image:width",
	"og:image:height",
	"og:type",
	"og:url",
	"og:site_name",
	"twitter:card",
	"twitter:title",
	"twitter:image",
	"twitter:description",
	"x:game:image",
	"x:game:image:width",
	"x:game:image:height"
]);
function escapeHtml(value) {
	return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("\"", "&quot;").replaceAll("'", "&#39;");
}
/** Inverse of escapeHtml. Decode &amp; last so a single pass undoes one encode. */
function unescapeHtml(value) {
	return String(value).replaceAll("&lt;", "<").replaceAll("&gt;", ">").replaceAll("&quot;", "\"").replaceAll("&#39;", "'").replaceAll("&amp;", "&");
}
/** 6-digit hex for the og.grok.me placeholder, or "" if site.color is missing/invalid. */
function placeholderCardColor(site = {}) {
	const raw = String(site.color ?? "").trim();
	const hex = raw.startsWith("#") ? raw.slice(1) : raw;
	return /^[0-9a-fA-F]{6}$/.test(hex) ? hex : "";
}
/**
* "wild-race.grok.me" → "Wild Race". Only published app hosts encode the
* display name in the first label. Preview / guest hosts are image origins
* only — slugifying them produced internal names like "Hds Abc 3000 Xy".
*/
function appNameFromHost(hostHeader) {
	const host = String(hostHeader ?? "").split(",")[0].trim().split(":")[0].toLowerCase();
	if (!host.endsWith(".grok.me")) return DEFAULT_APP_NAME;
	const slug = host.split(".")[0] ?? "";
	if (!slug || slug === "www" || !/^[a-z0-9-]{1,63}$/.test(slug)) return DEFAULT_APP_NAME;
	return slug.split("-").filter(Boolean).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ") || "Grok App";
}
/** True for Vercel system domains. Envoy rewrites origin Host to these; they SSO-protect `/og.jpg`. */
function isVercelSystemHost(host) {
	return host === "vercel.app" || host.endsWith(".vercel.app") || host === "vercel.com" || host.endsWith(".vercel.com");
}
/** Hostname suitable for absolute og:image URLs. Preview guests (X-Forwarded-Host) are allowed. */
function publicAppHost(hostHeader) {
	const host = String(hostHeader ?? "").split(",")[0].trim().split(":")[0].toLowerCase();
	if (!host || !/^[a-z0-9.-]+$/.test(host) || !host.includes(".")) return "";
	if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) return "";
	if (isVercelSystemHost(host)) return "";
	return host;
}
/**
* Published apps always use `VITE_PUBLIC_HOSTNAME` (the grok.me host the
* deployer injects). Live preview has no such env, so fall back to the
* request host / X-Forwarded-Host. Never prefer request Host on a published
* app — Envoy rewrites it to `*.vercel.app`.
*/
function resolvePublicHost(hostHeader) {
	return publicAppHost(process.env?.VITE_PUBLIC_HOSTNAME) || publicAppHost(hostHeader);
}
function isInstallQuery(url) {
	const query = String(url ?? "").split("?", 2)[1] ?? "";
	const params = new URLSearchParams(query);
	const install = params.get("install");
	const platform = (params.get("platform") ?? "").toLowerCase();
	return (install === "1" || install === "true") && platform === "ios";
}
/** Paths that can carry an app document (vs assets / API / internals). */
function isDocumentPath(pathname) {
	const path = String(pathname ?? "");
	return !path.startsWith("/__grok/") && !path.startsWith("/api/") && !path.startsWith("/@") && !path.startsWith("/node_modules") && !/\.[a-z0-9]+$/i.test(path);
}
function acceptsHtml(accept) {
	const value = String(accept ?? "");
	return value === "" || value.includes("text/html") || value.includes("*/*");
}
/** The same URL without the install-tutorial params (used as the app link). */
function stripInstallParams(url) {
	const [path = "/", query = ""] = String(url ?? "/").split("?", 2);
	const params = new URLSearchParams(query);
	params.delete("install");
	params.delete("platform");
	const rest = params.toString();
	return rest ? `${path}?${rest}` : path;
}
function renderInstallPageHtml(template, { host, url } = {}) {
	return String(template).replaceAll("{{APP_NAME}}", escapeHtml(appNameFromHost(host))).replaceAll("{{APP_URL}}", escapeHtml(stripInstallParams(url)));
}
function renderWebManifest(hostHeader) {
	const name = appNameFromHost(hostHeader);
	return JSON.stringify({
		name,
		short_name: name,
		id: "/",
		start_url: "/",
		scope: "/",
		display: "standalone",
		background_color: "#000000",
		theme_color: "#000000",
		icons: [{
			src: "/__grok/icon-180.png",
			sizes: "180x180",
			type: "image/png"
		}]
	}, null, 2);
}
function grokPwaHeadTags(appName = DEFAULT_APP_NAME) {
	return [
		["manifest", "<link rel=\"manifest\" href=\"/__grok/manifest.webmanifest\">"],
		["apple-touch-icon", "<link rel=\"apple-touch-icon\" href=\"/__grok/icon-180.png\">"],
		["apple-mobile-web-app-title", `<meta name="apple-mobile-web-app-title" content="${escapeHtml(appName)}">`],
		["apple-mobile-web-app-status-bar-style", "<meta name=\"apple-mobile-web-app-status-bar-style\" content=\"black\">"],
		["theme-color", "<meta name=\"theme-color\" content=\"#000000\">"]
	];
}
var GROK_EXTENSIONS_SCRIPT_SRC = "https://grok.com/grok-app-builder/extensions.js";
function readGrokProjectId() {
	const fromProcess = typeof process !== "undefined" ? process.env?.VITE_PROJECT_ID : "";
	return String(fromProcess ?? "").trim();
}
function readGrokExtensionsEnabled() {
	const fromProcess = typeof process !== "undefined" ? process.env?.VITE_GROK_EXTENSIONS : "";
	return String(fromProcess ?? "").trim() !== "0";
}
function readXCreator() {
	const fromProcess = typeof process !== "undefined" ? process.env?.X_CREATOR : "";
	return String(fromProcess ?? "").trim();
}
function readXCreatorId() {
	const fromProcess = typeof process !== "undefined" ? process.env?.X_CREATOR_ID : "";
	return String(fromProcess ?? "").trim();
}
function grokXCreatorHeadTags(creator = readXCreator(), creatorId = readXCreatorId()) {
	const name = String(creator ?? "").trim();
	const id = String(creatorId ?? "").trim();
	if (!name || !id) return [];
	return [`<meta property="x:creator" content="${escapeHtml(name)}">`, `<meta property="x:creator:id" content="${escapeHtml(id)}">`];
}
/** Platform "Created with Grok" banner — injected into every HTML document. */
function grokExtensionsHeadTags(projectId = readGrokProjectId()) {
	const id = escapeHtml(projectId);
	const tags = [];
	if (projectId) tags.push(`<meta name="grok-project-id" content="${id}">`);
	if (!readGrokExtensionsEnabled()) return tags;
	tags.push(`<script src="${GROK_EXTENSIONS_SCRIPT_SRC}"${projectId ? ` data-project-id="${id}"` : ""} defer><\/script>`);
	return tags;
}
function readOgSite(cwd = process.cwd()) {
	try {
		const raw = readFileSync(join(cwd, OG_SITE_REL_PATH), "utf8");
		const parsed = JSON.parse(raw);
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
	} catch {
		return {};
	}
}
/** Public path of an on-disk share card, or "" if neither file exists. */
function ogCardPublicPath(cwd = process.cwd()) {
	if (existsSync(join(cwd, "public/og.jpg"))) return "/og.jpg";
	if (existsSync(join(cwd, "public/og.png"))) return "/og.png";
	return "";
}
function detectCustomOgCard(cwd = process.cwd(), site = {}) {
	if (ogCardPublicPath(cwd)) return true;
	return siteHasCustomCard(site) || Boolean(String(site.image ?? "").trim());
}
/** Snapshot for Vite/Nitro to bake into the server bundle (Vercel has no workspace FS). */
function snapshotOgIdentity(cwd = process.cwd()) {
	const site = { ...readOgSite(cwd) };
	const disk = ogCardPublicPath(cwd);
	if (disk) {
		site.card = "custom";
		site.image = disk;
	} else {
		if (siteHasCustomCard(site)) delete site.card;
		if (site.image) delete site.image;
	}
	if (existsSync(join(cwd, "public/x-banner.jpg"))) site.banner = site.banner || "/x-banner.jpg";
	return { site };
}
function ogServiceUrl() {
	return (String(process.env?.VITE_OG_SERVICE_URL ?? "").trim() || "https://og.grok.me").replace(/\/+$/, "");
}
function titleFromDocument(html) {
	const match = String(html ?? "").match(/<title\b[^>]*>([^<]*)<\/title>/i);
	return match ? unescapeHtml(match[1]).trim() : "";
}
function resolveOgTitle(site = {}, appName = DEFAULT_APP_NAME, host = "", documentTitle = "") {
	const fromSite = String(site.title ?? "").trim();
	if (fromSite) return fromSite;
	const fromDoc = String(documentTitle ?? "").trim();
	if (fromDoc) return fromDoc;
	const fromHost = appNameFromHost(host);
	if (fromHost && fromHost !== "Grok App") return fromHost;
	return String(appName ?? "").trim() || "Grok App";
}
function siteHasCustomCard(site = {}) {
	return String(site.card ?? "").toLowerCase() === "custom";
}
/**
* Preview: public/og.jpg|png on disk.
* Vercel: the bake (`card=custom` / `image`) because the function cannot stat public/.
* Otherwise empty — caller emits the og.grok.me placeholder.
*/
function resolveOgCardAsset(site = {}, cwd = process.cwd()) {
	return ogCardPublicPath(cwd) || (detectCustomOgCard(cwd, site) ? String(site.image ?? "").trim() || "/og.jpg" : "");
}
/** Stamp `card=custom` when public/og.jpg or public/og.png is on disk. */
function applyCustomCardFromFs(site, cwd) {
	const disk = ogCardPublicPath(cwd);
	if (!disk) return site;
	return {
		...site,
		card: "custom",
		image: disk
	};
}
function grokOgHeadTags({ host = "", appName = DEFAULT_APP_NAME, site = {}, documentTitle = "", cwd = process.cwd() } = {}) {
	const title = resolveOgTitle(site, appName, host, documentTitle);
	const publicHost = resolvePublicHost(host);
	const tags = [`<meta name="twitter:card" content="summary_large_image">`, `<meta property="og:title" content="${escapeHtml(title)}">`];
	const description = String(site.description ?? "").trim();
	if (description) tags.push(`<meta property="og:description" content="${escapeHtml(description)}">`);
	if (String(site.type ?? "").toLowerCase() === "x:game") tags.push(`<meta property="og:type" content="x:game">`);
	if (publicHost) {
		const asset = resolveOgCardAsset(site, cwd);
		const custom = Boolean(asset);
		let image = custom ? `https://${publicHost}${asset.startsWith("/") ? asset : `/${asset}`}` : `${ogServiceUrl()}/v1/card.png?host=${encodeURIComponent(publicHost)}&title=${encodeURIComponent(title)}`;
		const color = !custom ? placeholderCardColor(site) : "";
		if (color) image += `&color=${encodeURIComponent(color)}`;
		tags.push(`<meta property="og:image" content="${escapeHtml(image)}">`);
		tags.push(`<meta property="og:image:width" content="1200">`);
		tags.push(`<meta property="og:image:height" content="630">`);
		const banner = String(site.banner ?? "").trim();
		if (banner) {
			const bannerUrl = `https://${publicHost}${banner.startsWith("/") ? banner : `/${banner}`}`;
			tags.push(`<meta property="x:game:image" content="${escapeHtml(bannerUrl)}">`);
			tags.push(`<meta property="x:game:image:width" content="1200">`);
			tags.push(`<meta property="x:game:image:height" content="264">`);
		}
	}
	return tags;
}
function stripGrokExtensionsScript(html) {
	return String(html).replace(/<script\b[^>]*\bsrc\s*=\s*["'][^"']*\/grok-app-builder\/extensions\.js[^"']*["'][^>]*>\s*<\/script>/gi, "");
}
function stripShareMetaTags(html) {
	return String(html).replace(/<meta\b[^>]*>/gi, (tag) => {
		const attrs = [...tag.matchAll(/\b(?:property|name)\s*=\s*["']([^"']+)["']/gi)];
		for (const match of attrs) if (SHARE_META_KEYS.has(String(match[1]).toLowerCase())) return "";
		return tag;
	});
}
function insertAfterHeadOpen(html, snippet) {
	if (/<head\b[^>]*>/i.test(html)) return html.replace(/<head\b[^>]*>/i, (open) => `${open}${snippet}`);
	if (/<html\b[^>]*>/i.test(html)) return html.replace(/<html\b[^>]*>/i, (open) => `${open}<head>${snippet}</head>`);
	return `<!doctype html><html><head>${snippet}</head>${html}`;
}
function insertBeforeHeadClose(html, snippet) {
	if (/<\/head>/i.test(html)) return html.replace(/<\/head>/i, `${snippet}</head>`);
	return insertAfterHeadOpen(html, snippet);
}
function normalizeHeadContext(ctx = {}) {
	const cwd = ctx.cwd ?? process.cwd();
	const site = applyCustomCardFromFs(ctx.site !== void 0 ? ctx.site : snapshotOgIdentity(cwd).site, cwd);
	return {
		appName: resolveOgTitle(site, ctx.appName ?? "Grok App", ctx.host ?? ""),
		projectId: ctx.projectId ?? readGrokProjectId(),
		creator: ctx.creator ?? readXCreator(),
		creatorId: ctx.creatorId ?? readXCreatorId(),
		host: ctx.host ?? "",
		cwd,
		site
	};
}
function injectGrokPwaHead(html, ctx = {}) {
	if (typeof html !== "string") return html;
	const { site, projectId, creator, creatorId, host, cwd } = normalizeHeadContext(ctx);
	const documentTitle = titleFromDocument(html);
	const appName = resolveOgTitle(site, ctx.appName ?? "Grok App", host, documentTitle);
	let next = stripShareMetaTags(html);
	if (!readGrokExtensionsEnabled()) next = stripGrokExtensionsScript(next);
	const missing = grokPwaHeadTags(appName).filter(([key]) => {
		if (key === "manifest") return !next.includes("href=\"/__grok/manifest.webmanifest\"");
		if (key === "apple-touch-icon") return !next.includes("href=\"/__grok/icon-180.png\"");
		return !next.includes(`name="${key}"`);
	}).map(([, tag]) => tag);
	next = insertAfterHeadOpen(next, grokOgHeadTags({
		host,
		appName,
		site,
		documentTitle,
		cwd
	}).join(""));
	if (readGrokExtensionsEnabled() && !next.includes("/grok-app-builder/extensions.js")) missing.push(...grokExtensionsHeadTags(projectId));
	else if (projectId && !next.includes("name=\"grok-project-id\"")) missing.push(`<meta name="grok-project-id" content="${escapeHtml(projectId)}">`);
	if (projectId && !next.includes("property=\"grok:app_id\"") && !next.includes("property='grok:app_id'")) missing.push(`<meta property="grok:app_id" content="${escapeHtml(projectId)}">`);
	const creatorTags = grokXCreatorHeadTags(creator, creatorId);
	if (creatorTags.length > 0) {
		if (!(next.includes("property=\"x:creator\" content=") || next.includes("property='x:creator' content="))) missing.push(creatorTags[0]);
		if (!next.includes("property=\"x:creator:id\"")) missing.push(creatorTags[1]);
	}
	if (missing.length === 0) return next;
	return insertBeforeHeadClose(next, missing.join(""));
}
function findHeadClose(buf) {
	return buf.toString("latin1").search(/<\/head>/i);
}
/**
* Streaming head injector: buffers only until `</head>` (ASCII marker; never
* appears inside a UTF-8 continuation byte), overwrites share-card metas,
* then passes later chunks through so streaming SSR keeps streaming.
*/
function createHeadInjector(ctx = {}) {
	const normalized = normalizeHeadContext(ctx);
	/** @type {Buffer[]} */
	let pending = [];
	let done = false;
	const apply = (html) => injectGrokPwaHead(html, {
		appName: normalized.appName,
		projectId: normalized.projectId,
		creator: normalized.creator,
		creatorId: normalized.creatorId,
		host: normalized.host,
		cwd: normalized.cwd,
		site: normalized.site
	});
	return {
		/** @param {Uint8Array | string} chunk @returns {Buffer[]} chunks ready to emit */
		push(chunk) {
			const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
			if (done) return [buf];
			pending.push(buf);
			const joined = Buffer.concat(pending);
			const at = findHeadClose(joined);
			if (at === -1) return [];
			done = true;
			pending = [];
			const closeLen = joined.toString("latin1", at).match(/^<\/head>/i)[0].length;
			const head = apply(joined.subarray(0, at + closeLen).toString("utf8"));
			return [Buffer.concat([Buffer.from(head, "utf8"), joined.subarray(at + closeLen)])];
		},
		/** @returns {Buffer[]} whatever is still buffered (no `</head>` seen) */
		flush() {
			if (done || pending.length === 0) return [];
			const rest = Buffer.concat(pending);
			pending = [];
			done = true;
			return [Buffer.from(apply(rest.toString("utf8")), "utf8")];
		}
	};
}
//#endregion
//#region server/middleware/grok-pwa.ts
/**
* Deployed-app (Nitro) half of the platform PWA chrome. Auto-registered as
* global h3 middleware because vite.config.ts sets `serverDir: "./server"` —
* without that option Nitro v3 never scans this directory.
*
* - `?install=1&platform=ios` on a document path → the Home Screen tutorial,
*   bundled into the server build via `?raw` (the public/ directory is CDN
*   static output on Vercel and not readable from the function).
* - `/__grok/manifest.webmanifest` → per-app-named manifest (kept out of
*   public/ so this dynamic response is the only one).
* - Other HTML documents → stream-inject PWA + OG head tags at `</head>`.
*   OG identity is baked via `virtual:grok-og-identity` at `vite build`
*   (this function cannot read `src/lib/og/site.json` or `public/og.jpg`).
*   This must be a middleware transforming `next()`: h3 discards the `response`
*   runtime hook's return value, and `render:html` does not exist in Nitro v3.
*/
function requestHost(event) {
	return event.req.headers.get("x-forwarded-host") ?? event.req.headers.get("host") ?? event.url.host;
}
function injectHeadStreaming(response, host) {
	const injector = createHeadInjector({
		host,
		site: grokOgIdentity.site
	});
	const transformed = response.body.pipeThrough(new TransformStream({
		transform(chunk, controller) {
			for (const out of injector.push(chunk)) controller.enqueue(out);
		},
		flush(controller) {
			for (const out of injector.flush()) controller.enqueue(out);
		}
	}));
	const headers = new Headers(response.headers);
	headers.delete("content-length");
	return new Response(transformed, {
		status: response.status,
		statusText: response.statusText,
		headers
	});
}
async function grokPwaMiddleware(event, next) {
	if ((event.req.method ?? "GET").toUpperCase() !== "GET") return next();
	const path = event.url.pathname;
	const urlWithQuery = path + event.url.search;
	if (path === "/__grok/manifest.webmanifest" || path === "/__grok/manifest.json") return new Response(renderWebManifest(requestHost(event)), { headers: {
		"content-type": "application/manifest+json; charset=utf-8",
		"cache-control": "no-cache"
	} });
	if (isInstallQuery(urlWithQuery) && isDocumentPath(path) && acceptsHtml(event.req.headers.get("accept"))) {
		const html = renderInstallPageHtml(install_page_default, {
			host: requestHost(event),
			url: urlWithQuery
		});
		return new Response(html, { headers: {
			"content-type": "text/html; charset=utf-8",
			"cache-control": "no-cache"
		} });
	}
	if (!isDocumentPath(path)) return next();
	const result = await next();
	if (result instanceof Response && result.body && String(result.headers.get("content-type") ?? "").includes("text/html") && !result.headers.get("content-encoding")) return injectHeadStreaming(result, requestHost(event));
	return result;
}
//#endregion
//#region #nitro/virtual/routing
var findRouteRules = /* @__PURE__ */ (() => {
	const $0 = [{
		name: "headers",
		route: "/assets/**",
		handler: headers,
		options: { "cache-control": "public, max-age=31536000, immutable" }
	}];
	return (m, p) => {
		let r = [];
		if (p.charCodeAt(p.length - 1) === 47) p = p.slice(0, -1) || "/";
		let s = p.split("/");
		if (s.length > 1) {
			if (s[1] === "assets") r.unshift({
				data: $0,
				params: { "_": s.slice(2).join("/") }
			});
		}
		return r;
	};
})();
var _lazy_IO091Z = defineLazyEventHandler(() => import("./_chunks/ssr-renderer.mjs"));
var findRoute = /* @__PURE__ */ (() => {
	const data = {
		route: "/**",
		handler: _lazy_IO091Z
	};
	return ((_m, p) => {
		return {
			data,
			params: { "_": p.slice(1) }
		};
	});
})();
var globalMiddleware = [toEventHandler(engineBootMiddleware), toEventHandler(grokPwaMiddleware)].filter(Boolean);
//#endregion
//#region node_modules/nitro/dist/runtime/internal/error/prod.mjs
var errorHandler = (error, event) => {
	const res = defaultHandler(error, event);
	return new NodeResponse(typeof res.body === "string" ? res.body : JSON.stringify(res.body, null, 2), res);
};
function defaultHandler(error, event) {
	const unhandled = error.unhandled ?? !HTTPError.isError(error);
	const { status = 500, statusText = "" } = unhandled ? {} : error;
	if (status === 404) {
		const url = event.url || new URL(event.req.url);
		const baseURL = "/";
		if (/^\/[^/]/.test(baseURL) && !url.pathname.startsWith(baseURL)) return {
			status: 302,
			headers: new Headers({ location: `${baseURL}${url.pathname.slice(1)}${url.search}` })
		};
	}
	const headers = new Headers(unhandled ? {} : error.headers);
	headers.set("content-type", "application/json; charset=utf-8");
	return {
		status,
		statusText,
		headers,
		body: {
			error: true,
			...unhandled ? {
				status,
				unhandled: true
			} : typeof error.toJSON === "function" ? error.toJSON() : {
				status,
				statusText,
				message: error.message
			}
		}
	};
}
//#endregion
//#region #nitro/virtual/error-handler
var errorHandlers = [errorHandler];
async function error_handler_default(error, event) {
	for (const handler of errorHandlers) try {
		const response = await handler(error, event, { defaultHandler });
		if (response) return response;
	} catch (error) {
		console.error(error);
	}
}
//#endregion
//#region #nitro/virtual/app
function createNitroApp() {
	const captureError = (error, errorCtx) => {
		if (errorCtx?.event) {
			const errors = errorCtx.event.req.context?.nitro?.errors;
			if (errors) errors.push({
				error,
				context: errorCtx
			});
		}
	};
	const h3App = createH3App({ onError(error, event) {
		return error_handler_default(error, event);
	} });
	let appHandler = (req) => {
		req.context ||= {};
		req.context.nitro = req.context.nitro || { errors: [] };
		return h3App.fetch(req);
	};
	return {
		fetch: appHandler,
		h3: h3App,
		hooks: void 0,
		captureError
	};
}
function createH3App(config) {
	const h3App = new H3Core(config);
	h3App["~findRoute"] = (event) => findRoute(event.req.method, event.url.pathname);
	h3App["~middleware"].push(...globalMiddleware);
	h3App["~getMiddleware"] = (event, route) => {
		const pathname = event.url.pathname;
		const method = event.req.method;
		const middleware = [];
		const routeRules = getRouteRules(method, pathname);
		event.context.routeRules = routeRules?.routeRules;
		if (routeRules?.routeRuleMiddleware.length) middleware.push(...routeRules.routeRuleMiddleware);
		middleware.push(...h3App["~middleware"]);
		if (route?.data?.middleware?.length) middleware.push(...route.data.middleware);
		return middleware;
	};
	return h3App;
}
//#endregion
//#region node_modules/nitro/dist/runtime/internal/app.mjs
var APP_ID = "default";
function useNitroApp() {
	let instance = useNitroApp._instance;
	if (instance) return instance;
	instance = useNitroApp._instance = createNitroApp();
	globalThis.__nitro__ = globalThis.__nitro__ || {};
	globalThis.__nitro__[APP_ID] = instance;
	return instance;
}
function getRouteRules(method, pathname) {
	const m = findRouteRules(method, pathname);
	if (!m?.length) return { routeRuleMiddleware: [] };
	const routeRules = {};
	for (const layer of m) for (const rule of layer.data) {
		const currentRule = routeRules[rule.name];
		if (currentRule) {
			if (rule.options === false) {
				delete routeRules[rule.name];
				continue;
			}
			if (typeof currentRule.options === "object" && typeof rule.options === "object") currentRule.options = {
				...currentRule.options,
				...rule.options
			};
			else currentRule.options = rule.options;
			currentRule.route = rule.route;
			currentRule.params = {
				...currentRule.params,
				...layer.params
			};
		} else if (rule.options !== false) routeRules[rule.name] = {
			...rule,
			params: layer.params
		};
	}
	const middleware = [];
	const orderedRules = Object.values(routeRules).sort((a, b) => (a.handler?.order || 0) - (b.handler?.order || 0));
	for (const rule of orderedRules) {
		if (rule.options === false || !rule.handler) continue;
		middleware.push(rule.handler(rule));
	}
	return {
		routeRules,
		routeRuleMiddleware: middleware
	};
}
//#endregion
//#region node_modules/nitro/dist/presets/vercel/runtime/isr.mjs
var ISR_URL_PARAM = "__isr_route";
function isrRouteRewrite(reqUrl, xNowRouteMatches) {
	if (xNowRouteMatches) {
		const isrURL = new URLSearchParams(xNowRouteMatches).get(ISR_URL_PARAM);
		if (isrURL) return [decodeURIComponent(isrURL), ""];
	} else {
		const queryIndex = reqUrl.indexOf("?");
		if (queryIndex !== -1) {
			const params = new URLSearchParams(reqUrl.slice(queryIndex + 1));
			const isrURL = params.get(ISR_URL_PARAM);
			if (isrURL) {
				params.delete(ISR_URL_PARAM);
				return [decodeURIComponent(isrURL), params.toString()];
			}
		}
	}
}
//#endregion
//#region node_modules/nitro/dist/presets/vercel/runtime/vercel.web.mjs
var nitroApp = useNitroApp();
var vercel_web_default = { async fetch(req, context) {
	const isrURL = isrRouteRewrite(req.url, req.headers.get("x-now-route-matches"));
	if (isrURL) {
		const { routeRules } = getRouteRules("", isrURL[0]);
		if (routeRules?.isr) req = new Request(new URL(isrURL[0] + (isrURL[1] ? `?${isrURL[1]}` : ""), req.url).href, req);
	}
	req.runtime ??= { name: "vercel" };
	req.runtime.vercel = { context };
	let ip;
	Object.defineProperty(req, "ip", { get() {
		const h = req.headers.get("x-forwarded-for");
		return ip ??= h?.split(",").shift()?.trim();
	} });
	req.waitUntil = context?.waitUntil;
	return nitroApp.fetch(req);
} };
//#endregion
export { vercel_web_default as default };
