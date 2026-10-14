import { a as loadTickers, n as MEXC_WHITELIST_IP, t as DEFAULT_SETTINGS } from "./whitelist-DREGUXAX.mjs";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
//#region node_modules/.nitro/vite/services/ssr/assets/engine.server-SdAdnuEr.js
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
function snapshot() {
	ensureEngine();
	const { rebuild: _rebuild, anchor: _anchor, ...snap } = book;
	return snap;
}
function configure(settings) {
	book.settings = {
		...DEFAULT_SETTINGS,
		...settings,
		levels: Math.round(settings.levels),
		sellLevels: Math.round(settings.sellLevels)
	};
	book.rebuild = true;
	if (!book.running) Object.assign(book, fresh(book.settings.deposit));
	log("ok", `Сетка обновлена: коридор ${book.settings.rangePct}%, геометрия ${book.settings.geo}, объём ×${book.settings.volumeK}`);
	save();
	return snapshot();
}
function commandStart() {
	book.running = true;
	log("ok", "Серверная сетка включена");
	save();
	tick();
	return snapshot();
}
function commandStop() {
	flatten("Остановлен");
	book.running = false;
	save();
	return snapshot();
}
function commandReset() {
	const deposit = book.settings.deposit;
	Object.assign(book, fresh(deposit));
	book.running = false;
	log("info", `Бумажный счёт сброшен на ${deposit} USDT`);
	save();
	return snapshot();
}
function commandBuy() {
	if (!(book.price > 0) || !book.symbol) {
		log("warn", "Нет цены для быстрой покупки");
		save();
		return snapshot();
	}
	const notion = Math.max(book.cash, 1) * (book.settings.marginPct / 100) * book.settings.leverage * (weights(book.settings.levels, book.settings.volumeK)[0] ?? 1);
	const qty = notion / book.price;
	const fee = notion * 1e-4;
	const nextPos = book.position + qty;
	book.avg = nextPos > 0 ? (book.avg * book.position + book.price * qty) / nextPos : 0;
	book.position = nextPos;
	book.cash -= fee;
	book.equity = book.cash;
	book.series = [...book.series, {
		t: stamp(),
		equity: Number(book.equity.toFixed(2))
	}].slice(-48);
	log("ok", `Быстрая покупка ${book.symbol.replace("_", "/")} по ${book.price.toPrecision(5)} · ${notion.toFixed(2)} USDT`);
	save();
	return snapshot();
}
function commandRent(name) {
	const clean = name.trim().slice(0, 32);
	if (!clean) return snapshot();
	book.role = "renter";
	book.activeRenter = clean;
	if (!book.renters.some((renter) => renter.name === clean)) book.renters = [...book.renters, {
		name: clean,
		realized: 0,
		fee: 0
	}];
	log("info", `Аренда для ${clean}. Ставка 5% с прибыли, без вывода с биржи.`);
	save();
	return snapshot();
}
function commandRole(role) {
	book.role = role;
	if (role === "owner") book.activeRenter = "";
	save();
	return snapshot();
}
//#endregion
export { commandBuy, commandRent, commandReset, commandRole, commandStart, commandStop, configure, ensureEngine, snapshot };
