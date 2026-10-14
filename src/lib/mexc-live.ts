import { createHmac } from "node:crypto";
import { appendFileSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const MEXC = "https://api.mexc.com";

type Secrets = { apiKey: string; secret: string; tgToken: string; tgChat: string };

const secrets: Secrets = { apiKey: "", secret: "", tgToken: "", tgChat: "" };
let rubRate = 0;
let nextReportAt = 0;

export function egressIp() {
  return "";
}

export function rubPerUsdt() {
  return rubRate;
}

export function liveArmed() {
  return secrets.apiKey.length >= 8 && secrets.secret.length >= 8;
}

export function explainAccessError(message: string) {
  if (/whitelist|700006/i.test(message)) {
    return "Ключ MEXC привязан к списку IP. Удалите все адреса в ключе и сохраните его. Робот свой IP не хранит.";
  }
  if (/signature|602/i.test(message)) {
    return "Confirming signature failed: секрет не совпадает с ключом. Сетку не меняю.";
  }
  if (/429|418|too many|frequen/i.test(message)) {
    return "Биржа ограничила частоту. Новые ордера не отправляются, пока лимит не спадёт.";
  }
  return message;
}

export async function refreshEgress() {
  return "";
}

export async function refreshRub() {
  try {
    const res = await fetch("https://www.cbr-xml-daily.ru/daily_json.js", { signal: AbortSignal.timeout(8_000) });
    const body = (await res.json()) as { Valute?: { USD?: { Value?: number } } };
    const usd = Number(body.Valute?.USD?.Value);
    if (usd > 0) rubRate = usd;
  } catch {
    /* rub display is optional */
  }
  return rubRate;
}

export function isFuturesSymbol(symbol: string) {
  return /^[A-Z0-9]+_USDT$/.test(symbol);
}

async function takeToken() {
  const now = Date.now();
  tokens = Math.min(BURST, tokens + ((now - filledAt) / 1000) * RATE);
  filledAt = now;
  if (tokens >= 1) {
    tokens -= 1;
    return;
  }
  const wait = ((1 - tokens) / RATE) * 1000;
  await new Promise((resolve) => setTimeout(resolve, wait));
  tokens = 0;
}

let tokens = 2;
let filledAt = Date.now();
const RATE = 2;
const BURST = 2;

function splitRequest(path: string, method: "GET" | "POST", body?: Record<string, unknown>) {
  const cut = path.indexOf("?");
  const route = cut >= 0 ? path.slice(0, cut) : path;
  if (method === "POST") {
    const parameter = JSON.stringify(body ?? {});
    return { url: `${MEXC}${route}`, parameter };
  }
  const pairs = cut >= 0
    ? [...new URLSearchParams(path.slice(cut + 1)).entries()].filter(([, value]) => value !== "")
    : [];
  pairs.sort(([a], [b]) => a.localeCompare(b));
  const parameter = pairs.map(([key, value]) => `${key}=${value}`).join("&");
  return { url: `${MEXC}${route}${parameter ? `?${parameter}` : ""}`, parameter };
}

async function signed(path: string, method: "GET" | "POST", body?: Record<string, unknown>) {
  if (path.includes("/api/v3") || path.includes("/spot")) {
    throw new Error("Спот отключён. Робот торгует только фьючерсами MEXC.");
  }
  await takeToken();
  let wait = 500;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const timestamp = Date.now().toString();
    const { url, parameter } = splitRequest(path, method, body);
    const signature = createHmac("sha256", secrets.secret).update(secrets.apiKey + timestamp + parameter).digest("hex");
    try {
      const res = await fetch(url, {
        method,
        headers: {
          ApiKey: secrets.apiKey,
          "Request-Time": timestamp,
          Signature: signature,
          "Recv-Window": "30",
          "Content-Type": "application/json",
        },
        body: method === "POST" ? parameter : undefined,
        signal: AbortSignal.timeout(12_000),
      });
      if (res.status === 429 || res.status === 418) {
        const jitter = wait * (0.5 + Math.random());
        await new Promise((resolve) => setTimeout(resolve, jitter));
        wait *= 2;
        continue;
      }
      if (res.status === 403) {
        return { status: 403, json: { success: false, message: "MEXC закрыла доступ с IP этого сервера (403). Ключ сохранён." } };
      }
      const text = await res.text();
      let json: { success?: boolean; code?: number; message?: string; data?: unknown };
      try {
        json = JSON.parse(text) as { success?: boolean; code?: number; message?: string; data?: unknown };
      } catch {
        return { status: res.status, json: { success: false, message: `Биржа вернула не JSON, код ${res.status}` } };
      }
      if (/frequent|too many|частот|510/i.test(json.message || "") || json.code === 510) {
        const jitter = wait * (0.5 + Math.random());
        await new Promise((resolve) => setTimeout(resolve, jitter));
        wait *= 2;
        continue;
      }
      return { status: res.status, json };
    } catch {
      const jitter = wait * (0.5 + Math.random());
      await new Promise((resolve) => setTimeout(resolve, jitter));
      wait *= 2;
    }
  }
  return { status: 429, json: { success: false, message: "429" } };
}

export async function probeKeys(apiKey: string, secret: string) {
  await refreshEgress();
  const previous = { ...secrets };
  secrets.apiKey = apiKey;
  secrets.secret = secret;
  try {
    const { json } = await signed("/api/v1/private/account/assets", "GET");
    if (json.success === false || (json.code && json.code !== 0)) {
      secrets.apiKey = previous.apiKey;
      secrets.secret = previous.secret;
      return { ok: false as const, message: explainAccessError(json.message || "Ключ не принят") };
    }
    const rows = Array.isArray(json.data) ? json.data : [];
    const usdt = rows.find((row) => (row as { currency?: string }).currency === "USDT") as
      | { availableBalance?: number | string; equity?: number | string }
      | undefined;
    return {
      ok: true as const,
      available: Number(usdt?.availableBalance ?? 0),
      equity: Number(usdt?.equity ?? usdt?.availableBalance ?? 0),
    };
  } catch (error) {
    secrets.apiKey = previous.apiKey;
    secrets.secret = previous.secret;
    const message = error instanceof Error ? error.message : "Сеть MEXC недоступна";
    return { ok: false as const, message };
  }
}

export function pinKeys(apiKey: string, secret: string) {
  secrets.apiKey = apiKey.trim();
  secrets.secret = secret.trim();
}

export function restoreSavedKeys() {
  const saved = readLocalSecrets();
  if (saved.apiKey.length < 8 || saved.secret.length < 8) return false;
  secrets.apiKey = saved.apiKey;
  secrets.secret = saved.secret;
  return true;
}

export function disarmLive() {
  secrets.apiKey = "";
  secrets.secret = "";
}

type Spec = { contractSize: number; priceScale: number; minVol: number };
const specs = new Map<string, Spec>();

async function contractSpec(symbol: string): Promise<Spec | null> {
  const cached = specs.get(symbol);
  if (cached) return cached;
  const res = await fetch(`${MEXC}/api/v1/contract/detail?symbol=${symbol}`, { signal: AbortSignal.timeout(12_000) });
  const body = (await res.json()) as { data?: { contractSize?: number; priceScale?: number; minVol?: number } };
  const size = Number(body.data?.contractSize);
  if (!size) return null;
  const spec = { contractSize: size, priceScale: Number(body.data?.priceScale ?? 4), minVol: Number(body.data?.minVol ?? 1) };
  specs.set(symbol, spec);
  return spec;
}

export async function cancelSymbol(symbol: string) {
  if (!isFuturesSymbol(symbol)) return { ok: false, message: "Спот отключён. Снимать можно только фьючерсные ордера." };
  if (!liveArmed()) return { ok: false, message: "Ключ не вооружён" };
  const { json } = await signed("/api/v1/private/order/cancel_all", "POST", { symbol });
  if (json.success === false) return { ok: false, message: explainAccessError(json.message || "Не снял ордера") };
  return { ok: true, message: "" };
}

export function fileLog(text: string) {
  try {
    const file = "/workspace/data/robot.log";
    mkdirSync(dirname(file), { recursive: true });
    if (statSync(file, { throwIfNoEntry: false })?.size ?? 0 > 1_000_000) renameSync(file, `${file}.1`);
    appendFileSync(file, `${new Date().toISOString()} ${text}\n`);
  } catch {
    /* журнал в интерфейсе остаётся */
  }
}

function modeBusy(message: string) {
  return /cancel active orders|close positions|position mode|order direction/i.test(message);
}

export async function prepareFutures(symbol: string) {
  if (!isFuturesSymbol(symbol)) return { ok: false, message: "Спот отключён" };
  if (!liveArmed()) return { ok: false, message: "Ключ не вооружён" };
  const mode = await signed("/api/v1/private/position/position_mode", "GET");
  const current = Number((mode.json.data as { positionMode?: number } | undefined)?.positionMode ?? 0);
  if (current !== 2) {
    const changed = await signed("/api/v1/private/position/change_position_mode", "POST", { positionMode: 2 });
    if (changed.json.success === false && !modeBusy(changed.json.message || "")) {
      return { ok: false, message: explainAccessError(changed.json.message || "Не переключил one-way. Нужны пустые ордера и позиция.") };
    }
  }
  const leverage = await signed("/api/v1/private/position/change_leverage", "POST", {
    symbol,
    leverage: 10,
    openType: 1,
    positionType: 1,
  });
  if (leverage.json.success === false && !modeBusy(leverage.json.message || "")) {
    return { ok: false, message: explainAccessError(leverage.json.message || "Биржа не поставила плечо 10x") };
  }
  return { ok: true, message: "Изолированная маржа, плечо 10x, односторонний режим" };
}

export async function depthAskNotional(symbol: string) {
  const spec = await contractSpec(symbol);
  if (!spec) return 0;
  const res = await fetch(`${MEXC}/api/v1/contract/depth/${symbol}?limit=5`, { signal: AbortSignal.timeout(8_000) });
  if (!res.ok) return 0;
  const body = (await res.json()) as { data?: { asks?: unknown[] } };
  const asks = body.data?.asks ?? [];
  let notional = 0;
  for (const row of asks.slice(0, 3)) {
    const pair = row as [number, number] | { price?: number; vol?: number };
    const price = Array.isArray(pair) ? Number(pair[0]) : Number(pair.price);
    const vol = Array.isArray(pair) ? Number(pair[1]) : Number(pair.vol);
    if (price > 0 && vol > 0) notional += price * vol * spec.contractSize;
  }
  return notional;
}

export async function openLongProtected(input: { symbol: string; price: number; stop: number; balance: number; vol?: number }) {
  if (!isFuturesSymbol(input.symbol)) return { ok: false, message: "Спот отключён", vol: 0, orderId: "", left: 0 };
  const spec = await contractSpec(input.symbol);
  if (!spec) return { ok: false, message: "Нет спецификации контракта", vol: 0, orderId: "", left: 0 };
  let vol = input.vol && input.vol > 0 ? Math.floor(input.vol) : Math.floor((input.balance * 0.05) / (input.price - input.stop) / spec.contractSize);
  const depth = await depthAskNotional(input.symbol);
  const cap = depth > 0 ? depth * 0.05 : 0;
  let left = 0;
  if (cap > 0) {
    const slice = Math.floor(cap / (spec.contractSize * input.price));
    if (slice < spec.minVol) return { ok: false, message: "Лот больше 5% трёх уровней стакана. Вход запрещён.", vol: 0, orderId: "", left: 0 };
    if (vol > slice) {
      left = vol - slice;
      vol = slice;
    }
  }
  if (vol < spec.minVol) return { ok: false, message: "Риск 5% не набирает минимальный контракт", vol: 0, orderId: "", left: 0 };
  const price = Number(input.price.toFixed(spec.priceScale));
  const stop = Number(input.stop.toFixed(spec.priceScale));
  const sent = await signed("/api/v1/private/order/create", "POST", {
    symbol: input.symbol,
    price,
    vol,
    side: 1,
    type: 1,
    openType: 1,
    leverage: 10,
    positionMode: 2,
    stopLossPrice: stop,
    lossTrend: 1,
  });
  if (sent.json.success === false) {
    return { ok: false, message: explainAccessError(sent.json.message || "Вход не принят"), vol: 0, orderId: "", left: 0 };
  }
  const orderId = String((sent.json.data as { orderId?: string } | undefined)?.orderId ?? sent.json.data ?? "");
  const plan = await signed("/api/v1/private/planorder/place/v2", "POST", {
    symbol: input.symbol,
    vol,
    side: 4,
    openType: 1,
    leverage: 10,
    triggerPrice: stop,
    triggerType: 2,
    executeCycle: 1,
    orderType: 5,
    trend: 1,
    positionMode: 2,
    reduceOnly: true,
  });
  const planId = plan.json.success === false ? "" : String(plan.json.data ?? "");
  return { ok: true, message: `Лимит ${vol} контр., стоп ${stop}${left > 0 ? `, осталось добрать ${left}` : ""}`, vol, orderId, planId, stop, left };
}

export async function moveStop(input: { symbol: string; planId: string; vol: number; stop: number }) {
  if (!input.planId) return { ok: false, message: "Нет id стоп-ордера" };
  await signed("/api/v1/private/planorder/cancel", "POST", { symbol: input.symbol, orderId: input.planId });
  const spec = await contractSpec(input.symbol);
  const stop = Number(input.stop.toFixed(spec?.priceScale ?? 4));
  const plan = await signed("/api/v1/private/planorder/place/v2", "POST", {
    symbol: input.symbol,
    vol: input.vol,
    side: 4,
    openType: 1,
    leverage: 10,
    triggerPrice: stop,
    triggerType: 2,
    executeCycle: 1,
    orderType: 5,
    trend: 1,
    positionMode: 2,
    reduceOnly: true,
  });
  if (plan.json.success === false) return { ok: false, message: explainAccessError(plan.json.message || "Стоп не перенесён"), planId: input.planId, stop };
  return { ok: true, message: `Стоп перенесён на ${stop}`, planId: String(plan.json.data ?? ""), stop };
}

export async function placeReducePlan(input: { symbol: string; vol: number; trigger: number; above: boolean; planId?: string }) {
  if (!isFuturesSymbol(input.symbol)) return { ok: false, message: "Спот отключён", planId: "", trigger: input.trigger };
  if (input.planId) await signed("/api/v1/private/planorder/cancel", "POST", { symbol: input.symbol, orderId: input.planId });
  const spec = await contractSpec(input.symbol);
  const trigger = Number(input.trigger.toFixed(spec?.priceScale ?? 4));
  const plan = await signed("/api/v1/private/planorder/place/v2", "POST", {
    symbol: input.symbol,
    vol: input.vol,
    side: 4,
    openType: 1,
    leverage: 10,
    triggerPrice: trigger,
    triggerType: input.above ? 1 : 2,
    executeCycle: 1,
    orderType: 5,
    trend: 1,
    positionMode: 2,
    reduceOnly: true,
  });
  if (plan.json.success === false) {
    return { ok: false, message: explainAccessError(plan.json.message || "Плавающий выход не принят"), planId: input.planId ?? "", trigger };
  }
  return { ok: true, message: `${input.above ? "TP" : "SL"} ${trigger}`, planId: String(plan.json.data ?? ""), trigger };
}

export async function marketClose(symbol: string) {
  if (!isFuturesSymbol(symbol) || !liveArmed()) return { ok: false, message: "Закрытие недоступно" };
  const spec = await contractSpec(symbol);
  const { json } = await signed("/api/v1/private/position/open_positions", "GET");
  const rows = Array.isArray(json.data) ? json.data : [];
  const position = rows.find((row) => (row as { symbol?: string }).symbol === symbol) as { holdVol?: number } | undefined;
  const vol = Math.floor(Number(position?.holdVol ?? 0));
  await cancelSymbol(symbol);
  await signed("/api/v1/private/planorder/cancel_all", "POST", { symbol });
  if (vol <= 0 || !spec) return { ok: true, message: "Позиции нет" };
  const sent = await signed("/api/v1/private/order/create", "POST", {
    symbol,
    price: 0,
    vol,
    side: 4,
    type: 5,
    openType: 1,
    positionMode: 2,
    reduceOnly: true,
  });
  if (sent.json.success === false) return { ok: false, message: explainAccessError(sent.json.message || "Рыночное закрытие не принято") };
  return { ok: true, message: "Позиция закрыта рынком, отложенные ордера сняты" };
}

export async function quoteOrders(symbol: string, bids: number[], weights: number[], budget: number, leverage: number) {
  if (!isFuturesSymbol(symbol)) return null;
  const spec = await contractSpec(symbol);
  if (!spec) return null;
  const lev = Math.min(10, Math.max(1, Math.round(leverage)));
  const vols = bids.map((price, index) => {
    const notional = budget * lev * (weights[index] ?? 0);
    const vol = Math.floor(notional / (price * spec.contractSize));
    return vol >= spec.minVol ? vol : 0;
  });
  return { vols, contractSize: spec.contractSize, priceScale: spec.priceScale, minVol: spec.minVol };
}

export async function syncLimits(input: {
  symbol: string;
  bids: number[];
  bidVols: number[];
  asks: number[];
  equity: number;
  leverage: number;
  budget: number;
  blockBuys: boolean;
  positionCoin: number;
}) {
  if (!liveArmed()) return { ok: false, message: "Ключ не вооружён", position: 0, avg: 0 };
  if (!isFuturesSymbol(input.symbol)) {
    return { ok: false, message: "Спот отключён. Ордер разрешён только на фьючерс MEXC.", position: 0, avg: 0 };
  }
  const spec = await contractSpec(input.symbol);
  if (!spec) return { ok: false, message: "Нет спецификации контракта, ордер не отправлен", position: 0, avg: 0 };
  const open = await signed(`/api/v1/private/order/list/open_orders/${input.symbol}?page_num=1&page_size=50`, "GET");
  if (open.json.success === false || /whitelist/i.test(open.json.message || "")) {
    return { ok: false, message: explainAccessError(open.json.message || "Список ордеров недоступен"), position: 0, avg: 0 };
  }
  const resting = Array.isArray(open.json.data) ? open.json.data : [];
  const prices = resting.map((row) => Number((row as { price?: number }).price));
  const leverage = Math.min(Math.max(Math.round(input.leverage), 1), 10);
  const placed: string[] = [];
  let reserved = resting.reduce((sum, row) => {
    const order = row as { side?: number; price?: number; vol?: number };
    if (order.side !== 1 && order.side !== 3) return sum;
    return sum + (Number(order.price) * Number(order.vol) * spec.contractSize) / leverage;
  }, 0);
  if (!input.blockBuys) {
    for (let index = 0; index < input.bids.length; index += 1) {
      const price = input.bids[index] ?? 0;
      if (!(price > 0)) continue;
      if (prices.some((rest) => Math.abs(rest - price) / price < 0.001)) continue;
      const vol = input.bidVols[index] ?? 0;
      if (vol < spec.minVol) continue;
      const need = (price * vol * spec.contractSize) / leverage;
      if (reserved + need > input.equity * 0.85) break;
      const sent = await signed("/api/v1/private/order/submit", "POST", {
        symbol: input.symbol,
        price: Number(price.toFixed(spec.priceScale)),
        vol,
        side: 1,
        type: 2,
        openType: 1,
        leverage,
      });
      if (/insufficient|balance/i.test(sent.json.message || "")) break;
      if (sent.json.success === false) {
        return { ok: false, message: explainAccessError(sent.json.message || "Лимит не принят"), position: 0, avg: 0 };
      }
      reserved += need;
      placed.push(`${vol}@${price.toPrecision(4)}`);
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }
  const heldContracts = Math.floor(input.positionCoin / spec.contractSize);
  if (heldContracts >= spec.minVol && input.asks.length > 0) {
    const each = Math.floor(heldContracts / input.asks.length);
    let leftover = heldContracts - each * input.asks.length;
    for (const price of input.asks) {
      const vol = each + (leftover > 0 ? 1 : 0);
      if (leftover > 0) leftover -= 1;
      if (vol < spec.minVol) continue;
      if (prices.some((rest) => Math.abs(rest - price) / price < 0.001)) continue;
      const sent = await signed("/api/v1/private/order/submit", "POST", {
        symbol: input.symbol,
        price: Number(price.toFixed(spec.priceScale)),
        vol,
        side: 4,
        type: 2,
        openType: 1,
        leverage,
      });
      if (/insufficient|balance/i.test(sent.json.message || "")) break;
      if (sent.json.success === false) {
        return { ok: false, message: explainAccessError(sent.json.message || "Лимит на закрытие не принят"), position: 0, avg: 0 };
      }
      placed.push(`close ${vol}@${price.toPrecision(4)}`);
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }
  const pos = await signed("/api/v1/private/position/open_positions", "GET");
  const rows = Array.isArray(pos.json.data) ? pos.json.data : [];
  const held = rows.find((row) => (row as { symbol?: string }).symbol === input.symbol) as
    | { holdVol?: number; openAvgPrice?: number }
    | undefined;
  return {
    ok: true,
    message: placed.length ? `Лимиты ${input.symbol}: ${placed.join(", ")}` : "",
    position: Number(held?.holdVol ?? 0) * spec.contractSize,
    avg: Number(held?.openAvgPrice ?? 0),
  };
}

export async function syncNeutral(input: {
  symbol: string;
  price: number;
  bid: number;
  ask: number;
  step: number;
  budget: number;
  equity: number;
  positionCoin?: number;
  avgPrice?: number;
}) {
  if (!liveArmed()) return { ok: false, message: "Ключ не вооружён", position: 0, avg: 0, closed: 0 };
  if (!isFuturesSymbol(input.symbol)) return { ok: false, message: "Спот отключён", position: 0, avg: 0, closed: 0 };
  const spec = await contractSpec(input.symbol);
  if (!spec) return { ok: false, message: "Нет спецификации контракта", position: 0, avg: 0, closed: 0 };
  const pos = await signed("/api/v1/private/position/open_positions", "GET");
  if (pos.json.success === false) {
    return { ok: false, message: explainAccessError(pos.json.message || "Позиция не прочитана"), position: 0, avg: 0, closed: 0 };
  }
  const rows = Array.isArray(pos.json.data) ? pos.json.data : [];
  const held = rows.find((row) => (row as { symbol?: string }).symbol === input.symbol) as
    | { holdVol?: number; openAvgPrice?: number; positionType?: number }
    | undefined;
  const apiContracts = Math.floor(Number(held?.holdVol ?? 0));
  const fallback = Math.floor((input.positionCoin ?? 0) / spec.contractSize);
  const contracts = apiContracts >= spec.minVol ? apiContracts : fallback;
  const avg = Number(held?.openAvgPrice ?? 0) || input.avgPrice || 0;
  const long = (held?.positionType ?? 1) !== 2;
  const position = contracts * spec.contractSize;
  const leverage = 10;
  const placed: string[] = [];
  const step = Math.min(0.008, Math.max(0.0015, input.step));
  const open = await signed(`/api/v1/private/order/list/open_orders/${input.symbol}?page_num=1&page_size=50`, "GET");
  if (open.json.success === false) {
    return { ok: false, message: explainAccessError(open.json.message || "Список ордеров недоступен"), position, avg, closed: 0 };
  }
  const resting = Array.isArray(open.json.data) ? open.json.data : [];
  const near = (price: number) => resting.some((row) => Math.abs(Number((row as { price?: number }).price) - price) / price < 0.0015);

  if (contracts >= spec.minVol && avg > 0) {
    const target = long ? avg * (1 + step) : avg * (1 - step);
    const ready = long ? input.price >= target : input.price <= target;
    const exitPx = Number((ready ? (long ? input.bid || input.price : input.ask || input.price) : target).toFixed(spec.priceScale));
    const side = long ? 4 : 2;
    const parked = resting.filter((row) => Number((row as { side?: number }).side) === side);
    const stuck = parked.length === 0 || parked.every((row) => {
      const price = Number((row as { price?: number }).price);
      return long ? price > target * 1.0015 : price < target * 0.9985;
    });
    if (stuck || ready) {
      await cancelSymbol(input.symbol);
      await new Promise((resolve) => setTimeout(resolve, 400));
      const sent = await signed("/api/v1/private/order/submit", "POST", {
        symbol: input.symbol,
        price: ready ? 0 : exitPx,
        vol: contracts,
        side,
        type: ready ? 5 : 2,
        openType: 1,
        leverage,
      });
      if (sent.json.success === false) {
        if (/nonexistent|already closed|closed/i.test(sent.json.message || "")) {
          return { ok: true, message: `${input.symbol} без позиции`, position: 0, avg: 0, closed: 0 };
        }
        return { ok: false, message: explainAccessError(sent.json.message || "Закрытие не принято"), position, avg, closed: 0 };
      }
      placed.push(`${long ? "закрыл лонг" : "закрыл шорт"} ${contracts}@${ready ? "рынком" : exitPx}`);
    }
    return {
      ok: true,
      message: placed.length ? `Нейтраль ${input.symbol}: ${placed.join(", ")}` : "",
      position,
      avg,
      closed: ready && placed.length ? 1 : 0,
    };
  }

  const levels = 5;
  const each = Math.max(spec.minVol, Math.floor((input.budget * leverage) / levels / 2 / (input.price * spec.contractSize)));
  let used = 0;
  const posts: Array<{ price: number; side: number }> = [];
  for (let i = 1; i <= levels; i += 1) {
    posts.push({ price: input.price * (1 - step * i), side: 1 });
    posts.push({ price: input.price * (1 + step * i), side: 3 });
  }
  for (const post of posts) {
    if (used >= 6) break;
    const price = Number(post.price.toFixed(spec.priceScale));
    if (!(price > 0) || near(price)) continue;
    const need = (price * each * spec.contractSize) / leverage;
    if (need > input.equity * 0.9) break;
    const sent = await signed("/api/v1/private/order/submit", "POST", {
      symbol: input.symbol,
      price,
      vol: each,
      side: post.side,
      type: 2,
      openType: 1,
      leverage,
    });
    if (/insufficient|balance|frequent/i.test(sent.json.message || "")) break;
    if (sent.json.success === false) break;
    used += 1;
    placed.push(`${post.side === 1 ? "buy" : "short"} ${each}@${price}`);
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return {
    ok: true,
    message: placed.length ? `Нейтраль ${input.symbol}: ${placed.join(", ")}` : "",
    position,
    avg,
    closed: 0,
  };
}

export function writeLocalSecrets(patch: Record<string, string>) {
  const found: Record<string, string> = {};
  try {
    for (const line of readFileSync("secrets.env", "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const cut = trimmed.indexOf("=");
      if (cut > 0) found[trimmed.slice(0, cut).trim()] = trimmed.slice(cut + 1).trim();
    }
  } catch {
    /* создаём файл с нуля */
  }
  for (const [key, value] of Object.entries(patch)) {
    if (value.trim()) found[key] = value.trim();
    else delete found[key];
  }
  const body = Object.entries(found)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  writeFileSync("secrets.env", `${body}\n`, { mode: 0o600 });
}

export function readLocalSecrets() {
  const found: Record<string, string> = {};
  try {
    for (const line of readFileSync("secrets.env", "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const cut = trimmed.indexOf("=");
      if (cut < 1) continue;
      found[trimmed.slice(0, cut).trim()] = trimmed.slice(cut + 1).trim().replace(/^["']|["']$/g, "");
    }
  } catch {
    /* файла может не быть, тогда ключ задаётся только в пульте */
  }
  const pick = (name: string) => process.env[name]?.trim() || found[name] || "";
  const xai = pick("XAI_API_KEY");
  if (xai) process.env.XAI_API_KEY = xai;
  const tgToken = pick("TG_TOKEN");
  const tgChat = pick("TG_CHAT");
  if (tgToken && tgChat) rememberTelegram(tgToken, tgChat);
  return { apiKey: pick("MEXC_API_KEY"), secret: pick("MEXC_SECRET") };
}

export function rememberTelegram(token: string, chat: string) {
  if (secrets.tgToken === token && secrets.tgChat === chat) return;
  secrets.tgToken = token;
  secrets.tgChat = chat;
  nextReportAt = Date.now() + 8 * 60 * 60 * 1000;
}

let telegramLookupAt = 0;

export async function primeTelegram() {
  if (!secrets.tgToken) return;
  if (/^-?\d+$/.test(secrets.tgChat)) return;
  if (Date.now() - telegramLookupAt < 60_000) return;
  telegramLookupAt = Date.now();
  try {
    const res = await fetch(`https://api.telegram.org/bot${secrets.tgToken}/getUpdates`, { signal: AbortSignal.timeout(12_000) });
    const body = (await res.json()) as { result?: Array<{ message?: { chat?: { id?: number; type?: string } } }> };
    const chat = (body.result || []).map((row) => row.message?.chat).find((item) => item && item.type === "private" && item.id);
    if (!chat?.id) return;
    secrets.tgChat = String(chat.id);
    writeLocalSecrets({ TG_CHAT: secrets.tgChat });
  } catch {
    /* сеть Telegram может не ответить */
  }
}

export async function reportTelegram(text: string, force = false) {
  if (!secrets.tgToken || !secrets.tgChat) return false;
  if (!/^-?\d+$/.test(secrets.tgChat)) {
    await primeTelegram();
    if (!/^-?\d+$/.test(secrets.tgChat)) return false;
  }
  if (!force && Date.now() < nextReportAt) return false;
  try {
    const res = await fetch(`https://api.telegram.org/bot${secrets.tgToken}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: secrets.tgChat, text }),
      signal: AbortSignal.timeout(12_000),
    });
    const sent = (await res.json()) as { ok?: boolean };
    if (!sent.ok) {
      nextReportAt = Date.now() + 15 * 60 * 1000;
      return false;
    }
  } catch {
    nextReportAt = Date.now() + 15 * 60 * 1000;
    return false;
  }
  nextReportAt = Date.now() + 8 * 60 * 60 * 1000;
  return true;
}
