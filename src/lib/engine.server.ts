import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { DEFAULT_SETTINGS, type Level, type Point, type Renter, type Settings } from "@/lib/desk-store";
import { loadCandles, loadTickers, type Ticker } from "@/lib/exchange.functions";
import { askGrok, xaiKey, type TunePatch } from "@/lib/grok-tune";
import {
  btcShock,
  capWeights,
  gridFitsLeverage,
  meanRevertLong,
  trendAgainstGrid,
} from "@/lib/market-math";
import {
  pinKeys,
  restoreSavedKeys,
  disarmLive,
  egressIp,
  fileLog,
  liveArmed,
  marketClose,
  openLongProtected,
  placeReducePlan,
  prepareFutures,
  probeKeys,
  readLocalSecrets,
  writeLocalSecrets,
  isFuturesSymbol,
  refreshEgress,
  refreshRub,
  rememberTelegram,
  reportTelegram,
  primeTelegram,
  quoteOrders,
  syncLimits,
  syncNeutral,
} from "@/lib/mexc-live";

const FILE = join(process.cwd(), "data", "engine.json");
const LOCK = join(process.cwd(), "data", "engine.lock");
const PID = process.pid;
const TOKEN = `${PID}-${Math.random().toString(36).slice(2)}`;

export type EngineLog = {
  id: number;
  ts: number;
  tone: "info" | "ok" | "warn" | "err";
  text: string;
};

export type EngineSnap = {
  running: boolean;
  lessons: number;
  role: "owner" | "renter";
  activeRenter: string;
  renters: Renter[];
  settings: Settings;
  symbol: string;
  price: number;
  cash: number;
  position: number;
  avg: number;
  equity: number;
  trades: number;
  wins: number;
  levels: Level[];
  series: Point[];
  logs: EngineLog[];
  ticks: number;
  beat: number;
  mode: "paper" | "live";
  egressIp: string;
  gate: string;
  rub: number;
  grokSync: string;
  goal: string;
  keySaved: boolean;
};

type Book = EngineSnap & {
  rebuild: boolean;
  anchor: number;
  day: string;
  dayEquity: number;
  dayPeak: number;
  restarts: number;
  lastFundingAt: number;
  grokAt: number;
  planId: string;
  stopPx: number;
  riskDist: number;
  liveVol: number;
  peakPx: number;
  trailPx: number;
  tpPx: number;
  tpPlanId: string;
  leftVol: number;
  suspended: boolean;
  memory: { key: string; context: string; action: string; fix: string; until: number; scored: boolean; born?: number }[];
  quietUntil: number;
  avoid: string[];
  model: Record<string, Record<string, number>>;
  signHoldUntil: number;
};

let logSeq = 1;
let ticking = false;
let warnedEmpty = false;
let timer: ReturnType<typeof setInterval> | null = null;
let announced = false;
let grokBusy = false;

function clampTune(current: Settings, patch: TunePatch): Settings {
  return { ...current, ...patch, leverage: 10, deposit: current.deposit };
}

let grokCooldown = 10 * 60_000;

async function maybeGrok() {
  if (grokBusy || !xaiKey()) {
    if (!xaiKey()) book.grokSync = "Grok недоступен: нет ключа API";
    return;
  }
  if (book.grokAt && Date.now() - book.grokAt < grokCooldown) return;
  grokBusy = true;
  book.grokAt = Date.now();
  book.grokSync = "Grok читает журнал и сетку…";
  try {
    const errors = book.logs.filter((line) => line.tone === "err").slice(0, 4).map((line) => line.text);
    const coach = await askGrok({
      symbol: book.symbol,
      price: book.price,
      equity: book.equity,
      trades: book.trades,
      wins: book.wins,
      gate: book.gate,
      settings: book.settings,
      notes: book.logs
        .filter((line) => !line.text.startsWith("Grok") && !line.text.startsWith("Урок"))
        .slice(0, 5)
        .map((line) => line.text),
      errors,
    });
    grokCooldown = 10 * 60_000;
    const patch = coach?.patch ?? null;
    const changed = patch
      ? (Object.keys(patch) as Array<keyof TunePatch>).filter((key) => {
          const next = patch[key];
          const prev = book.settings[key];
          if (typeof next !== "number" || typeof prev !== "number") return next !== prev;
          const gap = Math.abs(next - prev);
          if (key === "stepPct" || key === "geo" || key === "volumeK") return gap >= 0.05;
          if (key === "rangePct" || key === "maxPrice" || key === "maxDdPct") return gap >= 0.5;
          return gap >= 1;
        })
      : [];
    if (coach?.action && errors.length > 0) {
      const applied = applyFix(coach.action);
      book.lessons += 1;
      book.memory = [
        { key: `grok:${coach.action}`, context: "grok", action: coach.action, fix: applied.fix, until: applied.until, scored: false, born: Date.now() },
        ...(book.memory || []),
      ].slice(0, 20);
      log("info", `Grok разобрал ошибку: ${coach.note || applied.fix}`);
    }
    if (!patch || changed.length === 0) {
      book.grokSync = errors.length
        ? "Grok на связи и смотрит ошибки. Сетку не менял."
        : "Grok на связи, параметры оставлены";
      return;
    }
    book.settings = clampTune(book.settings, Object.fromEntries(changed.map((key) => [key, patch[key]])) as TunePatch);
    book.lessons += 1;
    book.rebuild = true;
    book.grokSync = `Grok обновил ${changed.join(", ")}`;
    log(
      "ok",
      `Grok: шаг ${book.settings.stepPct}%, коридор ${book.settings.rangePct}%, геометрия ${book.settings.geo}. Плечо осталось 10x.`,
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    if (/credit|spending limit|permission-denied/i.test(message)) {
      grokCooldown = 6 * 60 * 60_000;
      book.grokSync = "Ключ xAI подключён. Кредиты команды кончились, локальное обучение продолжается.";
      return;
    }
    book.grokSync = /401|403/.test(message)
      ? "xAI отклонил запрос. Сетка работает, локальное обучение включено."
      : "Grok не ответил, сетка без изменений";
  } finally {
    grokBusy = false;
    save();
  }
}

function stamp() {
  return new Date().toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Europe/Moscow",
  });
}

function moscowDay() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date());
}

function fresh(deposit: number): Pick<
  Book,
  "symbol" | "anchor" | "price" | "cash" | "position" | "avg" | "equity" | "trades" | "wins" | "levels" | "series"
> {
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
    series: [{ t: stamp(), equity: deposit }],
  };
}

function blank(): Book {
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
    mode: "paper",
    egressIp: egressIp(),
    gate: "Бумажной сетки нет. Ордера уйдут после боевого ключа. Ключ не должен быть привязан к одному IP.",
    rub: 0,
    day: "",
    dayEquity: DEFAULT_SETTINGS.deposit,
    dayPeak: DEFAULT_SETTINGS.deposit,
    restarts: 0,
    lastFundingAt: 0,
    grokAt: 0,
    planId: "",
    stopPx: 0,
    riskDist: 0,
    liveVol: 0,
    peakPx: 0,
    trailPx: 0,
    tpPx: 0,
    tpPlanId: "",
    leftVol: 0,
    suspended: false,
    memory: [],
    quietUntil: 0,
    avoid: [],
    model: {},
    signHoldUntil: 0,
    grokSync: "Grok ещё не синхронизировался",
    goal: "Цель 300 000 ₽",
    keySaved: false,
    logs: [
      {
        id: logSeq++,
        ts: Date.now(),
        tone: "info",
        text: "Сервер сетки запущен без вкладки. Цикл идёт, пока жив процесс. Grok правит только числа сетки.",
      },
    ],
    ...fresh(DEFAULT_SETTINGS.deposit),
  };
}

const book = load();

function load(): Book {
  try {
    const raw = readFileSync(FILE, "utf8");
    const parsed = JSON.parse(raw) as Partial<Book>;
    const next = blank();
    return {
      ...next,
      ...parsed,
      settings: { ...DEFAULT_SETTINGS, ...(parsed.settings ?? {}) },
      logs: Array.isArray(parsed.logs) ? parsed.logs.slice(0, 80) : next.logs,
      renters: Array.isArray(parsed.renters) ? parsed.renters : [],
      series: Array.isArray(parsed.series) ? parsed.series.slice(-48) : next.series,
      levels: Array.isArray(parsed.levels) ? parsed.levels : [],
    };
  } catch {
    return blank();
  }
}

function save() {
  try {
    mkdirSync(dirname(FILE), { recursive: true });
    writeFileSync(FILE, JSON.stringify(book));
  } catch {
    /* disk can be read-only on a serverless host */
  }
}

function log(tone: EngineLog["tone"], text: string) {
  book.logs = [{ id: logSeq++, ts: Date.now(), tone, text }, ...book.logs].slice(0, 80);
  fileLog(`${tone} ${text}`);
  if (!learning && (tone === "err" || tone === "warn")) {
    learning = true;
    try {
      rememberError(text);
    } finally {
      learning = false;
    }
  }
}

let learning = false;

function errorClass(text: string) {
  if (/whitelist|700006|список IP/i.test(text)) return "ip";
  if (/balance insufficient|insufficient balance|недостаточно баланса/i.test(text)) return "funds";
  if (/429|418|частот|frequent|frequency/i.test(text)) return "rate";
  if (/cancel active orders|order direction|position mode/i.test(text)) return "mode";
  if (/стакан|тонк/i.test(text)) return "book";
  if (/4,5%|ATR/i.test(text)) return "stop";
  if (/недостаточно марж|margin insufficient/i.test(text)) return "margin";
  if (/спецификац|контракт/i.test(text)) return "spec";
  if (/signature|подпись|Confirming|602/i.test(text)) return "sign";
  if (/не пустила|закрыла доступ|403|Access Denied/i.test(text)) return "blocked";
  if (/ликвидных пар/i.test(text)) return "universe";
  if (/network|timeout|ECONN|сеть/i.test(text)) return "net";
  return "other";
}

const FIXES: Record<string, string[]> = {
  ip: ["pause15m"],
  rate: ["pause60"],
  net: ["pause60", "pause20"],
  book: ["skipPair", "widenStep"],
  stop: ["skipPair", "widenStep"],
  spec: ["skipPair"],
  margin: ["pause60"],
  funds: ["shrinkBatch"],
  mode: ["keepMode"],
  blocked: ["pause15m"],
  sign: ["reloadSecret"],
  universe: ["raiseCap"],
  other: ["pause20"],
};

function weightOf(context: string, action: string) {
  return book.model?.[context]?.[action] ?? 1;
}

function learn(context: string, action: string, reward: number) {
  const row = { ...(book.model?.[context] ?? {}) };
  const next = Math.min(20, Math.max(0.05, weightOf(context, action) * Math.exp(0.35 * reward)));
  row[action] = Number(next.toFixed(3));
  book.model = { ...(book.model ?? {}), [context]: row };
}

function chooseFix(context: string) {
  const options = FIXES[context] ?? FIXES.other;
  const weights = options.map((action) => weightOf(context, action));
  const sum = weights.reduce((total, value) => total + value, 0) || 1;
  let draw = Math.random() * sum;
  for (let i = 0; i < options.length; i += 1) {
    draw -= weights[i] ?? 1;
    if (draw <= 0) return options[i];
  }
  return options[0];
}

function settleMemory() {
  const now = Date.now();
  for (const item of book.memory || []) {
    if (!item.context || !item.action) {
      item.scored = true;
      item.until = 0;
      continue;
    }
    if (!item.scored && item.until <= now) {
      const born = item.born ?? now - 60_000;
      const repeated = (book.logs || []).some(
        (line) => line.tone === "err" && line.ts > born + 1000 && errorClass(line.text) === item.context,
      );
      learn(item.context, item.action, repeated ? -1 : 1);
      item.scored = true;
    }
  }
  book.memory = (book.memory || []).filter((item) => item.until > now);
  const held = new Set(book.memory.map((item) => item.key.split(":")[1]).filter(Boolean));
  book.avoid = (book.avoid || []).filter((symbol) => held.has(symbol));
}

function applyFix(action: string) {
  const now = Date.now();
  if (action === "pause20") {
    book.quietUntil = Math.max(book.quietUntil || 0, now + 20_000);
    return { until: now + 20_000, fix: "пауза 20 секунд" };
  }
  if (action === "pause60") {
    book.quietUntil = Math.max(book.quietUntil || 0, now + 60_000);
    return { until: now + 60_000, fix: "пауза 60 секунд" };
  }
  if (action === "pause15m") {
    book.quietUntil = Math.max(book.quietUntil || 0, now + 15 * 60_000);
    return { until: now + 15 * 60_000, fix: "пауза 15 минут" };
  }
  if (action === "skipPair") {
    const symbol = book.symbol;
    if (symbol && !(book.avoid || []).includes(symbol)) book.avoid = [...(book.avoid || []), symbol].slice(-12);
    book.rebuild = true;
    return { until: now + 30 * 60_000, fix: symbol ? `${symbol} отложена` : "пара отложена" };
  }
  if (action === "cutMargin") {
    const marginPct = Math.max(20, book.settings.marginPct - 10);
    book.settings = { ...book.settings, marginPct };
    return { until: now + 60 * 60_000, fix: `маржа ${marginPct}%` };
  }
  if (action === "shrinkBatch") {
    batchCap = Math.max(1, batchCap - 1);
    return { until: now + 30 * 60_000, fix: `не больше ${batchCap} новых ордеров за цикл` };
  }
  if (action === "keepMode") {
    return { until: now + 60 * 60_000, fix: "режим позиции не трогаю, ордера уже стоят" };
  }
  if (action === "raiseCap") {
    const maxPrice = Number(Math.min(1, book.settings.maxPrice + 0.2).toFixed(2));
    book.settings = { ...book.settings, maxPrice };
    book.rebuild = true;
    return { until: now + 60 * 60_000, fix: `потолок цены ${maxPrice} USDT` };
  }
  if (action === "reloadSecret") {
    restoreSavedKeys();
    book.signHoldUntil = now + 10 * 60_000;
    book.rebuild = false;
    return { until: now + 10 * 60_000, fix: "секрет перечитан, шаг сетки не менялся" };
  }
  const stepPct = Number(Math.min(0.8, book.settings.stepPct + 0.05).toFixed(2));
  book.settings = { ...book.settings, stepPct };
  book.rebuild = true;
  return { until: now + 30 * 60_000, fix: `шаг ${stepPct}%` };
}

function rememberError(text: string) {
  const now = Date.now();
  settleMemory();
  const context = errorClass(text);
  const key = context === "book" || context === "stop" || context === "spec" ? `${context}:${book.symbol || "pair"}` : context;
  const open = (book.memory || []).find((item) => item.key === key);
  if (open) {
    if (open.context === "sign") {
      book.signHoldUntil = now + 60 * 60_000;
      book.mode = "paper";
      book.rebuild = false;
      if (!open.scored) {
        open.scored = true;
        book.gate = "Подпись снова не сошлась. Вставьте Secret заново. Шаг сетки не трогаю.";
        log("warn", book.gate);
      }
      return;
    }
    if (!open.scored) {
      learn(open.context, open.action, -1);
      open.scored = true;
      open.until = now + 5 * 60_000;
      book.quietUntil = Math.max(book.quietUntil || 0, open.until);
      book.gate = `Урок не сработал: «${open.action}». Вес ${weightOf(open.context, open.action)}`;
      log("info", book.gate);
    }
    return;
  }
  const action = chooseFix(context);
  const applied = applyFix(action);
  book.memory = [{ key, context, action, fix: applied.fix, until: applied.until, scored: false, born: now }, ...(book.memory || [])].slice(0, 20);
  book.lessons += 1;
  book.gate = `Учёба: ${applied.fix}`;
  log("info", `Урок ${context}: ${applied.fix}`);
}

let journalSwept = false;
let watchAnnounced = false;
let deskCleaned = false;
let batchCap = 3;
const learnedLines = new Set<number>();

function reviewJournal() {
  if (!deskCleaned) {
    deskCleaned = true;
    book.logs = (book.logs || []).filter((line) => line.tone !== "err" && line.tone !== "warn" && !line.text.startsWith("Ошибка «") && !line.text.startsWith("Повтор ошибки"));
    if (book.settings.marginPct < 100) book.settings = { ...book.settings, marginPct: 100 };
    book.memory = (book.memory || []).filter((item) => item.context !== "margin");
  }
  book.memory = (book.memory || []).filter((item) => !(item.context === "other" && item.action === "widenStep"));
  const fresh = (book.logs || []).filter(
    (line) => Date.now() - line.ts < 90_000 && (line.tone === "err" || line.tone === "warn") && !learnedLines.has(line.id),
  );
  for (const line of fresh) {
    learnedLines.add(line.id);
    if (/^Ошибка «|^Повтор ошибки|^Журнал:|Лимит 48/.test(line.text)) continue;
    const context = errorClass(line.text);
    const key = context === "book" || context === "stop" || context === "spec" ? `${context}:${book.symbol || "pair"}` : context;
    const known = (book.memory || []).some((item) => item.key === key && item.until > Date.now());
    if (!known) rememberError(line.text);
  }
  if (!journalSwept && book.restarts >= 48 && (book.logs || []).some((line) => line.text.includes("Лимит 48 перестроек"))) {
    journalSwept = true;
    book.restarts = 0;
    book.running = true;
    book.suspended = false;
    log("ok", "Журнал: остановка снята. Ошибка подписи больше не двигает шаг сетки.");
  }
  if (!watchAnnounced) {
    watchAnnounced = true;
    log("ok", "Режим 24/7. Журнал читается каждый цикл, сработавшее исправление запоминается.");
  }
}

function offsets(count: number, step: number, geo: number, range: number) {
  const raw: number[] = [];
  const g = Math.abs(geo - 1) < 0.001 ? 1 : geo;
  for (let i = 1; i <= count; i += 1) {
    raw.push(g === 1 ? step * i : (step * (g ** i - 1)) / (g - 1));
  }
  const last = raw[raw.length - 1] || step;
  const scale = last > range ? range / last : 1;
  return raw.map((value) => value * scale);
}

function weights(count: number, k: number) {
  return capWeights(count, k);
}

function buildGrid(price: number, step: number) {
  const { settings } = book;
  const range = Math.max(settings.rangePct, 3) / 100;
  const buyAt = offsets(settings.levels, step, settings.geo, range);
  const sellAt = offsets(settings.sellLevels, step, settings.geo, range);
  const levels: Level[] = [];
  for (const distance of buyAt) levels.push({ price: price * (1 - distance), side: "buy", filled: false, vol: 0 });
  for (const distance of sellAt) levels.push({ price: price * (1 + distance), side: "sell", filled: false, vol: 0 });
  return levels;
}

function learnFromMarket(row: Ticker) {
  if (book.ticks % 20 !== 0) return;
  const span = row.lastPrice > 0 ? (row.high24Price - row.lower24Price) / row.lastPrice : 0;
  const next = { ...book.settings, leverage: 10, maxDdPct: 10 };
  let reason = "";
  if (span > 0 && span < 0.03 && next.stepPct > 0.2) {
    next.stepPct = Number(Math.max(0.2, next.stepPct - 0.05).toFixed(2));
    reason = `тихий рынок ${(span * 100).toFixed(1)}%, шаг уже`;
  } else if (span > 0.08 && next.stepPct < 0.5) {
    next.stepPct = Number(Math.min(0.5, next.stepPct + 0.05).toFixed(2));
    reason = `широкий день ${(span * 100).toFixed(1)}%, шаг шире`;
  }
  const rate = book.trades >= 6 ? book.wins / book.trades : null;
  if (rate !== null && rate < 0.4 && next.rangePct < 10) {
    next.rangePct = Number(Math.min(10, next.rangePct + 0.5).toFixed(1));
    reason = `винрейт ${Math.round(rate * 100)}%, коридор шире`;
  } else if (rate !== null && rate > 0.65 && next.rangePct > 4) {
    next.rangePct = Number(Math.max(4, next.rangePct - 0.5).toFixed(1));
    reason = `винрейт ${Math.round(rate * 100)}%, коридор уже`;
  }
  if (!reason || (next.stepPct === book.settings.stepPct && next.rangePct === book.settings.rangePct)) return;
  book.settings = next;
  book.lessons += 1;
  book.rebuild = true;
  log("ok", `Обучение: ${reason}. Шаг ${next.stepPct}%, коридор ${next.rangePct}%. Риск сделки 5%, дневной стоп 10%.`);
}

function sidewaysScore(row: Ticker) {
  if (!isFuturesSymbol(row.symbol) || !(row.lastPrice > 0) || row.amount24 < 3_000_000) return -1;
  const span = (row.high24Price - row.lower24Price) / row.lastPrice;
  const drift = Math.abs(row.riseFallRate);
  // агрессивно: берём широкие дни, отсекаем тихие и односторонние прострелы
  if (span < 0.1 || span > 1.5 || drift > 0.18) return -1;
  return span * span * Math.log(row.amount24) / (1 + drift * 3);
}

function rankBasket(rows: Ticker[]) {
  const ranked = rows
    .filter((row) => sidewaysScore(row) > 0 && !(book.avoid || []).includes(row.symbol))
    .sort((a, b) => sidewaysScore(b) - sidewaysScore(a));
  const picked: Ticker[] = [];
  if (book.position > 0 && book.symbol) {
    const held = rows.find((row) => row.symbol === book.symbol);
    if (held) picked.push(held);
  }
  for (const row of ranked) {
    if (picked.some((item) => item.symbol === row.symbol)) continue;
    picked.push(row);
    if (picked.length >= 3) break;
  }
  return picked.slice(0, 3);
}

function resolveStep(row: Ticker) {
  const span = Math.max(0, (row.high24Price - row.lower24Price) / row.lastPrice);
  // узкий шаг от дневного размаха: на волатильной монете круги закрываются чаще
  return Math.min(0.0045, Math.max(0.0018, span / 40));
}

function holdLock() {
  try {
    const raw = readFileSync(LOCK, "utf8");
    const current = JSON.parse(raw) as { token?: string; at?: number };
    if (current.token !== TOKEN && Date.now() - Number(current.at ?? 0) < 15_000) return false;
  } catch {
    /* lock is free */
  }
  mkdirSync(dirname(LOCK), { recursive: true });
  writeFileSync(LOCK, JSON.stringify({ token: TOKEN, pid: PID, at: Date.now() }));
  try {
    const confirm = JSON.parse(readFileSync(LOCK, "utf8")) as { token?: string };
    return confirm.token === TOKEN;
  } catch {
    return false;
  }
}

function ownsLock() {
  try {
    const current = JSON.parse(readFileSync(LOCK, "utf8")) as { token?: string };
    return current.token === TOKEN;
  } catch {
    return false;
  }
}

let metaAt = 0;
let gateAt = 0;
let budgetScale = 1;

async function refreshMeta() {
  if (Date.now() - metaAt < 600_000 && book.rub > 0 && book.egressIp) return;
  metaAt = Date.now();
  book.egressIp = await refreshEgress();
  book.rub = await refreshRub();
}

function tradingBudget() {
  const free = book.cash > 0 ? book.cash : book.settings.deposit;
  return Math.max(free, 1) * (Math.min(book.settings.marginPct, 100) / 100) * budgetScale;
}

function goalLine() {
  if (!(book.rub > 0) || !(book.equity > 0)) return "Цель 300 000 ₽. Жду курс рубля.";
  const now = book.equity * book.rub;
  const left = Math.max(0, 300_000 - now);
  const done = Math.min(100, (now / 300_000) * 100);
  return `До цели ${Math.round(left).toLocaleString("ru-RU")} ₽, пройдено ${done.toFixed(1)}%. Риск сделки 5%, плавающие TP и SL на бирже, дневной предел 10%.`;
}

async function entryGate(row: Ticker, step: number) {
  if (book.mode !== "live" && /не пустила|закрыла доступ|403|signature|подпись|Confirming/i.test(book.gate)) return;
  if (Date.now() - gateAt < 45_000) return;
  gateAt = Date.now();
  const spread = row.ask > row.bid ? (row.ask - row.bid) / row.lastPrice : 0;
  if (spread > Math.max(step * 1.4, 0.0015)) {
    book.gate = `Спред ${(spread * 100).toFixed(2)}% шире шага. Покупки запрещены.`;
    return;
  }
  const [own, btc] = await Promise.all([loadCandles(row.symbol), loadCandles("BTC_USDT")]);
  if (book.mode === "live" && (own.length < 30 || btc.length < 30)) {
    book.gate = "Свечи MEXC не пришли. Сетка идёт по тикеру, фильтр RSI пропущен.";
    return;
  }
  if (btcShock(btc)) {
    book.gate = "Импульс BTC. Новые покупки сняты.";
    return;
  }
  const trend = trendAgainstGrid(own);
  if (trend.block) {
    book.gate = `Тренд: RSI ${trend.rsi?.toFixed(0) ?? "—"}, CCI ${trend.cci?.toFixed(0) ?? "—"}. Набор запрещён.`;
    return;
  }
  const rsiText = trend.rsi === null ? "" : `, RSI ${trend.rsi.toFixed(0)}`;
  book.gate =
    book.mode === "live"
      ? `Боевой режим. Фильтр спокоен${rsiText}.`
      : `Фильтр спокоен${rsiText}. Бумажных сделок нет, ордера только в боевом режиме.`;
}

let secretsBooted = false;
let lastSecretProbe = 0;

async function bootSecrets() {
  if (book.suspended) return;
  if ((book.signHoldUntil || 0) > Date.now()) return;
  if (book.mode === "live" && liveArmed()) return;
  const pair = readLocalSecrets();
  if (pair.apiKey.length < 8 || pair.secret.length < 8) return;
  if (secretsBooted && Date.now() - lastSecretProbe < 60_000) return;
  secretsBooted = true;
  lastSecretProbe = Date.now();
  const result = await probeKeys(pair.apiKey, pair.secret);
  pinKeys(pair.apiKey, pair.secret);
  book.keySaved = true;
  if (!result.ok) {
    book.mode = "paper";
    const gate = `Ключ сохранён. Биржа пока не пустила: ${result.message}`;
    if (book.gate !== gate) {
      book.gate = gate;
      log("warn", book.gate);
    }
    return;
  }
  book.mode = "live";
  book.running = true;
  if (result.equity > 0) {
    book.cash = result.equity;
    book.equity = result.equity;
    book.dayEquity = result.equity;
    book.dayPeak = result.equity;
  }
  book.gate = "Ключ сохранён. Боевой режим включён.";
  log("ok", "Ключ взят из secrets.env. Боевой режим включён без окна и без пульта.");
}
let preparedFor = "";
let impulseAt = 0;

async function _manageImpulse(row: Ticker, price: number) {
  if (book.suspended || Date.now() < (book.quietUntil || 0)) return;
  if (preparedFor !== row.symbol) {
    const ready = await prepareFutures(row.symbol);
    fileLog(ready.message);
    log(ready.ok ? "ok" : "warn", ready.message);
    preparedFor = row.symbol;
    if (!ready.ok) return;
  }
  if (book.position > 0 && book.avg > 0 && book.trailPx > 0 && book.liveVol > 0) {
    book.peakPx = Math.max(book.peakPx || price, price);
    const chandelier = book.peakPx - book.trailPx;
    const breakeven = book.riskDist > 0 && price >= book.avg + book.riskDist ? book.avg : 0;
    const nextStop = Math.max(book.stopPx, chandelier, breakeven);
    const nextTp = Math.max(book.tpPx, book.peakPx + book.trailPx * 0.5);
    if (nextStop < price && nextStop > book.stopPx + price * 0.001) {
      const moved = await placeReducePlan({
        symbol: row.symbol,
        vol: book.liveVol,
        trigger: nextStop,
        above: false,
        planId: book.planId,
      });
      fileLog(moved.message);
      if (moved.ok) {
        book.planId = moved.planId || book.planId;
        book.stopPx = moved.trigger || nextStop;
        log("ok", `Плавающий SL ${book.stopPx.toPrecision(4)}`);
      }
    }
    if (nextTp > price && nextTp > book.tpPx + price * 0.001) {
      const moved = await placeReducePlan({
        symbol: row.symbol,
        vol: book.liveVol,
        trigger: nextTp,
        above: true,
        planId: book.tpPlanId,
      });
      fileLog(moved.message);
      if (moved.ok) {
        book.tpPlanId = moved.planId || book.tpPlanId;
        book.tpPx = moved.trigger || nextTp;
        log("ok", `Плавающий TP ${book.tpPx.toPrecision(4)}`);
      }
    }
    return;
  }
  if (book.leftVol > 0 && book.stopPx > 0 && Date.now() - impulseAt >= 6_000) {
    const opened = await openLongProtected({
      symbol: row.symbol,
      price,
      stop: book.stopPx,
      balance: book.equity,
      vol: book.leftVol,
    });
    impulseAt = Date.now();
    log(opened.ok ? "ok" : "warn", opened.message);
    if (opened.ok) book.leftVol = opened.left ?? 0;
    return;
  }
  if (Date.now() - impulseAt < 15 * 60 * 1000) return;
  const plan = meanRevertLong(await loadCandles(row.symbol));
  if (!plan.ok) {
    if (!/Мало свечей/.test(plan.reason)) book.gate = plan.reason;
    return;
  }
  const opened = await openLongProtected({
    symbol: row.symbol,
    price: plan.entry,
    stop: plan.stop,
    balance: book.equity > 0 ? book.equity : book.settings.deposit,
  });
  impulseAt = Date.now();
  fileLog(`лот ${opened.vol} стоп ${plan.stop} ${opened.message}`);
  log(opened.ok ? "ok" : "warn", opened.message);
  void reportTelegram(opened.message, true).catch(() => undefined);
  if (!opened.ok) return;
  book.planId = opened.planId ?? "";
  book.stopPx = opened.stop ?? plan.stop;
  book.riskDist = plan.entry - plan.stop;
  book.trailPx = plan.trail;
  book.peakPx = plan.entry;
  book.liveVol = opened.vol;
  book.leftVol = opened.left ?? 0;
  const firstTp = plan.entry + book.riskDist * 2;
  const tp = await placeReducePlan({ symbol: row.symbol, vol: opened.vol, trigger: firstTp, above: true });
  if (tp.ok) {
    book.tpPlanId = tp.planId;
    book.tpPx = tp.trigger;
    log("ok", `Плавающий TP ${book.tpPx.toPrecision(4)}, SL ${book.stopPx.toPrecision(4)}`);
  }
}

async function halt(reason: string) {
  fileLog(reason);
  if (book.mode === "live" && liveArmed() && book.symbol) {
    const closed = await marketClose(book.symbol);
    log(closed.ok ? "warn" : "err", closed.ok ? `${reason}. Позиция закрыта рынком, ордера сняты.` : `${reason}. ${closed.message}`);
    void reportTelegram(`${reason}. ${closed.message}`, true).catch(() => undefined);
    book.running = false;
    book.suspended = true;
    book.planId = "";
    book.tpPlanId = "";
    book.stopPx = 0;
    book.tpPx = 0;
    book.leftVol = 0;
    return;
  }
  flatten(reason);
  book.running = false;
  book.suspended = true;
}

async function tick() {
  if (ticking) return;
  if (!holdLock()) return;
  ticking = true;
  const dayNow = moscowDay();
  if (book.day && book.day !== dayNow && book.suspended) {
    book.suspended = false;
    book.running = true;
    book.day = dayNow;
    log("ok", "Новые сутки. Пауза после дневного стопа снята, торговля продолжается сама.");
  }
  if (book.suspended) {
    book.running = false;
    ticking = false;
    return;
  }
  if (!announced) {
    announced = true;
    book.running = true;
    log("info", "Автозапуск без вкладки и без бумажных сделок. Ключ MEXC должен пускать любой IP: DNS белый список не заменяет.");
  }
  book.beat = Date.now();
  book.ticks += 1;
  try {
    if (!book.running) return;
    book.keySaved = restoreSavedKeys();
    settleMemory();
    reviewJournal();
    void maybeGrok();
    if (book.mode === "live" && !liveArmed()) book.mode = "paper";
    await bootSecrets();
    await primeTelegram();
    await refreshMeta();
    const day = moscowDay();
    if (book.day !== day) {
      book.day = day;
      book.dayEquity = book.equity || book.cash;
      book.dayPeak = book.dayEquity;
      book.restarts = 0;
    }
    const rows = await loadTickers();
    const basket = rankBasket(rows);
    const row = basket[0];
    if (!row) {
      if (!warnedEmpty) {
        log("warn", "Нет волатильного боковика. Жду пару с широким днём и без тренда.");
        warnedEmpty = true;
      }
      return;
    }
    warnedEmpty = false;
    if (book.symbol && !isFuturesSymbol(book.symbol)) {
      book.symbol = "";
      book.levels = [];
      book.position = 0;
      book.rebuild = true;
      log("warn", "Спот отключён. Сетка строится только по фьючерсам MEXC.");
    }
    const staleGrid = book.settings.stepPct === 0.4 && book.settings.rangePct === 8 && book.settings.geo === 1.06;
    if (staleGrid) {
      book.settings = { ...DEFAULT_SETTINGS, deposit: book.settings.deposit || DEFAULT_SETTINGS.deposit };
      book.rebuild = true;
    }
    if (book.settings.leverage !== 10 || book.settings.maxDdPct !== 10) {
      book.settings = { ...book.settings, leverage: 10, maxDdPct: 10 };
      book.rebuild = true;
    }
    if (book.levels.some((level) => level.side === "buy" && !Number.isFinite(level.vol))) book.rebuild = true;
    const step = resolveStep(row);
    let price = row.lastPrice;
    const range = Math.max(book.settings.rangePct, 3) / 100;
    const outside =
      book.anchor > 0 && (price < book.anchor * (1 - range) || price > book.anchor * (1 + range));
    await entryGate(row, step);
    const plannedNow = offsets(book.settings.levels, step, book.settings.geo, range);
    const weightsNow = weights(book.settings.levels, book.settings.volumeK);
    const entryNow = plannedNow.reduce((sum, distance, index) => sum + price * (1 - distance) * (weightsNow[index] ?? 0), 0);
    const lowestNow = price * (1 - (plannedNow[plannedNow.length - 1] ?? range));
    const fitNow = gridFitsLeverage(price, lowestNow, entryNow || price);
    budgetScale = fitNow.ok ? 1 : 0;
    if (!fitNow.ok) {
      book.gate = `Плечо 10x. Ликвидация ${fitNow.liq.toPrecision(4)} не ниже последней покупки ${lowestNow.toPrecision(4)}. Ордер не ставлю.`;
    }
    if (book.symbol !== row.symbol || book.levels.length === 0 || book.rebuild || outside) {
      if (book.restarts >= 48) {
        log("warn", "Лимит 48 перестроек за сутки. Набор остановлен до следующего дня.");
        book.running = false;
        return;
      }
      book.settings = { ...book.settings, leverage: 10 };
      const plannedWeights = weights(book.settings.levels, book.settings.volumeK);
      book.restarts += 1;
      book.symbol = row.symbol;
      book.anchor = price;
      book.levels = buildGrid(price, step);
      const quote = await quoteOrders(
        row.symbol,
        book.levels.filter((level) => level.side === "buy").map((level) => level.price),
        plannedWeights,
        tradingBudget(),
        10,
      );
      if (quote) {
        let cursor = 0;
        book.levels = book.levels.map((level) => {
          if (level.side !== "buy") return level;
          const vol = quote.vols[cursor] ?? 0;
          cursor += 1;
          return { ...level, vol };
        });
        const total = quote.vols.reduce((sum, vol) => sum + vol, 0);
        log(
          "info",
          `Ордер: post-only лимит, изолированная маржа, 10x. Контракт ${quote.contractSize}. Покупки ${quote.vols.join("+")} = ${total} контр. Продажа только закрывает лонг.`,
        );
      }
      book.rebuild = false;
      log(
        "info",
        outside
          ? `Коридор перестроен вокруг ${row.symbol.replace("_", "/")} · ${book.settings.rangePct}%`
          : `Сетка ${row.symbol.replace("_", "/")} · шаг ${(step * 100).toFixed(2)}% · геометрия ${book.settings.geo}`,
      );
    }

    if (book.mode !== "live") {
      book.position = 0;
      book.avg = 0;
      book.cash = book.settings.deposit;
      book.equity = book.cash;
    } else {
      const budget = tradingBudget() / basket.length;
      let primary = basket[0];
      for (const item of basket) {
        if (book.position > 0 && item.symbol !== book.symbol) continue;
        const synced = await syncNeutral({
          symbol: item.symbol,
          price: item.lastPrice,
          bid: item.bid,
          ask: item.ask,
          step: resolveStep(item),
          budget,
          equity: book.equity,
          positionCoin: item.symbol === book.symbol ? book.position : 0,
          avgPrice: item.symbol === book.symbol ? book.avg : 0,
        });
        if (!synced.ok) {
          if (/IP|whitelist|Ключ|429|частот/i.test(synced.message)) {
            book.mode = "paper";
            restoreSavedKeys();
          }
          log("err", synced.message);
          break;
        }
        if (item.symbol === book.symbol && book.position > 0 && synced.position > 0 && synced.position < book.position * 0.8) {
          book.trades += 1;
          book.wins += 1;
          book.position = synced.position;
          if (synced.avg > 0) book.avg = synced.avg;
          log("ok", `Сделка закрыта ${item.symbol.replace("_", "/")}. Всего ${book.trades}.`);
        } else if (synced.message) log("ok", synced.message);
        if (item.symbol === book.symbol) {
          book.position = synced.position;
          if (synced.avg > 0) book.avg = synced.avg;
          else if (synced.position === 0) book.avg = 0;
        } else if (synced.position > 0) {
          book.position = synced.position;
          book.avg = synced.avg;
          book.symbol = item.symbol;
          primary = item;
        }
      }
      price = primary.lastPrice;
      const names = basket
        .map((item) => {
          const span = item.lastPrice > 0 ? ((item.high24Price - item.lower24Price) / item.lastPrice) * 100 : 0;
          return `${item.symbol.replace("_USDT", "")} ${span.toFixed(0)}%`;
        })
        .join(", ");
      book.gate = `Агрессивный нейтраль: ${names}. Шаг ${(resolveStep(primary) * 100).toFixed(2)}%. Сделок ${book.trades}.`;
    }

    if (book.mode === "live" && book.position > 0 && row.fundingRate && Date.now() - book.lastFundingAt >= 3_600_000) {
      const hours = book.lastFundingAt === 0 ? 0 : (Date.now() - book.lastFundingAt) / 3_600_000;
      if (hours > 0) book.cash -= book.position * price * row.fundingRate * (hours / 8);
      book.lastFundingAt = Date.now();
    }
    const unreal = book.position > 0 ? (price - book.avg) * book.position : 0;
    book.price = price;
    book.equity = book.cash + unreal;
    book.dayPeak = Math.max(book.dayPeak || 0, book.dayEquity || 0, book.equity);
    book.goal = goalLine();
    book.series = [...book.series, { t: stamp(), equity: Number(book.equity.toFixed(2)) }].slice(-48);
    const dayDrawdown = book.dayEquity > 0 ? ((book.dayEquity - book.equity) / book.dayEquity) * 100 : 0;
    const peakDrawdown = book.dayPeak > 0 ? ((book.dayPeak - book.equity) / book.dayPeak) * 100 : 0;
    if (dayDrawdown > book.settings.maxDdPct || peakDrawdown > book.settings.maxDdPct) {
      await halt(`Стоп: просадка ${Math.max(dayDrawdown, peakDrawdown).toFixed(1)}%`);
    } else {
      learnFromMarket(row);
    }
    const rubText = book.rub > 0 ? ` / ${Math.round(book.equity * book.rub)} ₽` : "";
    void reportTelegram(`Коридор ${book.mode}. ${book.symbol || "без пары"} ${book.equity.toFixed(2)} USDT${rubText}. ${book.gate}`).catch(
      () => undefined,
    );
    void maybeGrok();
  } catch (error) {
    const message = error instanceof Error ? error.message : "Сбой серверного тика";
    log("err", message);
  } finally {
    ticking = false;
    save();
  }
}

function flatten(reason: string) {
  book.position = 0;
  book.avg = 0;
  book.cash = book.settings.deposit;
  book.equity = book.cash;
  log("warn", reason);
}

export function ensureEngine() {
  if (timer) return;
  timer = setInterval(() => {
    void tick();
  }, 6_000);
  void tick();
}

export function snapshot(): EngineSnap {
  ensureEngine();
  if (!ownsLock()) Object.assign(book, load());
  const { rebuild: _rebuild, anchor: _anchor, day: _day, dayEquity: _dayEquity, dayPeak: _dayPeak, restarts: _restarts, lastFundingAt: _funding, grokAt: _grokAt, planId: _plan, stopPx: _stop, riskDist: _risk, liveVol: _vol, peakPx: _peak, trailPx: _trail, tpPx: _tp, tpPlanId: _tpId, leftVol: _left, suspended: _suspended, memory: _memory, quietUntil: _quiet, avoid: _avoid, model: _model, signHoldUntil: _signHold, ...snap } = book;
  return snap;
}

export function configure(settings: Settings) {
  book.settings = {
    ...DEFAULT_SETTINGS,
    ...settings,
    levels: Math.round(settings.levels),
    sellLevels: Math.round(settings.sellLevels),
    leverage: 10,
  };
  book.rebuild = true;
  if (!book.running) {
    Object.assign(book, fresh(book.settings.deposit));
  }
  log("ok", `Сетка обновлена: коридор ${book.settings.rangePct}%, геометрия ${book.settings.geo}, объём ×${book.settings.volumeK}`);
  save();
  return snapshot();
}

export function commandStart() {
  book.suspended = false;
  book.running = true;
  book.quietUntil = 0;
  if (book.equity > 0) {
    book.dayEquity = book.equity;
    book.dayPeak = book.equity;
  }
  log("ok", "Ручной запуск. Аварийная пауза снята.");
  save();
  void tick();
  return snapshot();
}

export async function commandStop() {
  await halt("Остановлен");
  save();
  return snapshot();
}

export function commandReset() {
  const deposit = book.settings.deposit;
  Object.assign(book, fresh(deposit));
  book.running = false;
  log("info", `Сетка сброшена. Бумажных сделок нет. Депозит в расчёте ${deposit} USDT`);
  save();
  return snapshot();
}

export async function commandBuy() {
  if (!(book.price > 0) || !book.symbol) {
    log("warn", "Нет цены для быстрой покупки");
    save();
    return snapshot();
  }
  if (book.mode !== "live" || !liveArmed()) {
    log("warn", "Бумажная покупка отключена. Нужен боевой ключ без привязки к одному IP.");
    save();
    return snapshot();
  }
  const synced = await syncLimits({
    symbol: book.symbol,
    bids: [book.price],
    bidVols: [Math.max(book.levels.find((level) => level.side === "buy")?.vol ?? 1, 1)],
    asks: [],
    equity: book.equity,
    leverage: 10,
    budget: tradingBudget(),
    blockBuys: false,
    positionCoin: book.position,
  });
  if (!synced.ok) log("err", synced.message);
  else log("ok", synced.message || `Лимит на покупку ${book.symbol.replace("_", "/")} отправлен`);
  save();
  return snapshot();
}

export function commandRent(name: string) {
  const clean = name.trim().slice(0, 32);
  if (!clean) return snapshot();
  book.role = "renter";
  book.activeRenter = clean;
  if (!book.renters.some((renter) => renter.name === clean)) {
    book.renters = [...book.renters, { name: clean, realized: 0, fee: 0 }];
  }
  log("info", `Аренда для ${clean}. Ставка 5% с прибыли, без вывода с биржи.`);
  save();
  return snapshot();
}

export function commandRole(role: "owner" | "renter") {
  book.role = role;
  if (role === "owner") book.activeRenter = "";
  save();
  return snapshot();
}

export async function commandSaveKeys(apiKey: string, secret: string) {
  const cleanKey = apiKey.trim();
  const cleanSecret = secret.trim();
  writeLocalSecrets({ MEXC_API_KEY: cleanKey, MEXC_SECRET: cleanSecret });
  pinKeys(cleanKey, cleanSecret);
  book.keySaved = true;
  const result = await probeKeys(cleanKey, cleanSecret);
  pinKeys(cleanKey, cleanSecret);
  book.egressIp = egressIp();
  if (!result.ok) {
    book.mode = "paper";
    book.gate = `Ключ сохранён. Биржа пока не пустила: ${result.message}`;
    log("warn", book.gate);
  } else {
    book.mode = "live";
    book.running = true;
    book.settings = { ...book.settings, leverage: Math.min(book.settings.leverage, 10) };
    if (result.equity > 0) {
      book.cash = result.equity;
      book.equity = result.equity;
    }
    book.gate = "Ключ сохранён. Боевой режим включён.";
    log("ok", book.gate);
  }
  save();
  return snapshot();
}

export async function commandForgetKeys() {
  writeLocalSecrets({ MEXC_API_KEY: "", MEXC_SECRET: "" });
  disarmLive();
  book.keySaved = false;
  book.mode = "paper";
  book.gate = "Ключ стёрт с сервера.";
  log("warn", book.gate);
  save();
  return snapshot();
}

export async function commandArm(apiKey: string, secret: string) {
  const result = await probeKeys(apiKey, secret);
  book.egressIp = egressIp();
  if (!result.ok) {
    book.mode = "paper";
    disarmLive();
    book.gate = result.message;
    log("err", result.message);
  } else {
    book.mode = "live";
    book.running = true;
    book.settings = { ...book.settings, leverage: Math.min(book.settings.leverage, 10) };
    if (result.equity > 0) {
      book.cash = result.equity;
      book.equity = result.equity;
    }
    book.gate = "Боевой режим. Только лимиты, изолированная маржа, вывод не вызывается.";
    log("ok", book.gate);
  }
  save();
  return snapshot();
}

export async function commandNotify(token: string, chat: string) {
  rememberTelegram(token, chat);
  const rubText = book.rub > 0 ? ` / ${Math.round(book.equity * book.rub)} ₽` : "";
  const sent = await reportTelegram(
    `Коридор на связи. ${book.mode === "live" ? "Боевой" : "Без сделок"} ${book.symbol || "без пары"} ${book.equity.toFixed(2)} USDT${rubText}.`,
    true,
  );
  if (!sent) {
    rememberTelegram("", "");
    log("err", "Telegram не принял токен или chat id. Токен стёрт из памяти процесса.");
  } else {
    log("ok", "Telegram принят в память процесса. Отчёт раз в 8 часов. Токен на диск не пишется.");
  }
  save();
  return snapshot();
}

export function metricsText() {
  const rub = book.rub > 0 ? book.equity * book.rub : 0;
  return [
    "# TYPE koridor_equity gauge",
    `koridor_equity ${book.equity.toFixed(4)}`,
    "# TYPE koridor_equity_rub gauge",
    `koridor_equity_rub ${rub.toFixed(2)}`,
    "# TYPE koridor_position gauge",
    `koridor_position ${book.position.toFixed(6)}`,
    "# TYPE koridor_ticks counter",
    `koridor_ticks ${book.ticks}`,
    "# TYPE koridor_live gauge",
    `koridor_live ${book.mode === "live" ? 1 : 0}`,
    "# TYPE koridor_wins counter",
    `koridor_wins ${book.wins}`,
    "# TYPE koridor_trades counter",
    `koridor_trades ${book.trades}`,
  ].join("\n");
}
