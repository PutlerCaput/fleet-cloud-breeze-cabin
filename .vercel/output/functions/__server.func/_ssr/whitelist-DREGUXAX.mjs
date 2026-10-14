import { t as create } from "../_libs/zustand.mjs";
import { a as object, o as string } from "../_libs/zod.mjs";
import { n as TSS_SERVER_FUNCTION, r as getServerFnById, t as createServerFn } from "./ssr.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/whitelist-DREGUXAX.js
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
var logSeq = 1;
function stamp() {
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
			t: stamp(),
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
		id: logSeq++,
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
		id: logSeq++,
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
				t: stamp(),
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
					t: stamp(),
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
				t: stamp(),
				equity: Number(equity.toFixed(2))
			}].slice(-48)
		});
		if ((settings.deposit - equity) / Math.max(settings.deposit, 1) * 100 > settings.maxDdPct) get().stop(`Стоп: просадка бумаги`);
	}
}));
function loadSavedDesk() {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return;
		const parsed = JSON.parse(raw);
		if (parsed.settings) useDesk.getState().setSettings({
			...DEFAULT_SETTINGS,
			...parsed.settings
		});
		if (raw.includes("\"mexcKey\"")) saveDesk();
	} catch {}
}
function saveDesk() {
	const { settings } = useDesk.getState();
	localStorage.setItem(STORAGE_KEY, JSON.stringify({ settings }));
}
DEFAULT_SETTINGS.deposit;
var createSsrRpc = (functionId) => {
	const url = "/_serverFn/" + functionId;
	const serverFnMeta = { id: functionId };
	const fn = async (...args) => {
		return (await getServerFnById(functionId, { origin: "server" }))(...args);
	};
	return Object.assign(fn, {
		url,
		serverFnMeta,
		[TSS_SERVER_FUNCTION]: true
	});
};
var MEXC = "https://api.mexc.com";
var testMexcKeys = createServerFn({ method: "POST" }).validator(object({
	apiKey: string().trim().min(8).max(128),
	secret: string().trim().min(8).max(128)
})).handler(createSsrRpc("571df81d03c5fb265880f8c4bd0441b7c938fa6377cf93b44ffe2500d69ad8fc"));
var testTelegram = createServerFn({ method: "POST" }).validator(object({
	token: string().trim().min(20).max(120),
	chatId: string().trim().min(1).max(64)
})).handler(createSsrRpc("256c19088e22efcdd277e6ff837959b46c19dfe1b8cc8344bb8ceaa82e95353f"));
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
createServerFn({ method: "GET" }).handler(createSsrRpc("aaebb364c72b4da32a4e0d2d494418f58e191e8d091c8e4f31a5af330eacbc41"));
var MEXC_WHITELIST_IP = "212.164.64.196";
//#endregion
export { loadTickers as a, testTelegram as c, loadSavedDesk as i, useDesk as l, MEXC_WHITELIST_IP as n, saveDesk as o, createSsrRpc as r, testMexcKeys as s, DEFAULT_SETTINGS as t };
