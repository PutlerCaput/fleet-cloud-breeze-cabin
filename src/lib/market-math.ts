export type Candle = { high: number; low: number; close: number; vol?: number };

function ema(values: number[], period: number) {
  if (values.length === 0) return [];
  const k = 2 / (period + 1);
  const out: number[] = [values[0]];
  for (let i = 1; i < values.length; i += 1) out.push(values[i] * k + out[i - 1] * (1 - k));
  return out;
}

export function rsi(closes: number[], period = 14) {
  if (closes.length <= period) return null;
  let gain = 0;
  let loss = 0;
  for (let i = closes.length - period; i < closes.length; i += 1) {
    const diff = closes[i] - closes[i - 1];
    if (diff >= 0) gain += diff;
    else loss -= diff;
  }
  if (loss === 0) return 100;
  const rs = gain / loss;
  return 100 - 100 / (1 + rs);
}

export function macdHist(closes: number[]) {
  if (closes.length < 35) return null;
  const fast = ema(closes, 12);
  const slow = ema(closes, 26);
  const macd = fast.map((value, index) => value - slow[index]);
  const signal = ema(macd, 9);
  return macd[macd.length - 1] - signal[signal.length - 1];
}

export function cci(candles: Candle[], period = 20) {
  if (candles.length < period) return null;
  const slice = candles.slice(-period);
  const typical = slice.map((candle) => (candle.high + candle.low + candle.close) / 3);
  const mean = typical.reduce((sum, value) => sum + value, 0) / period;
  const deviation = typical.reduce((sum, value) => sum + Math.abs(value - mean), 0) / period;
  if (deviation === 0) return 0;
  return (typical[typical.length - 1] - mean) / (0.015 * deviation);
}

export function atr(candles: Candle[], period = 14) {
  if (candles.length <= period) return null;
  let sum = 0;
  for (let i = candles.length - period; i < candles.length; i += 1) {
    const prev = candles[i - 1]?.close ?? candles[i].close;
    const range = Math.max(
      candles[i].high - candles[i].low,
      Math.abs(candles[i].high - prev),
      Math.abs(candles[i].low - prev),
    );
    sum += range;
  }
  return sum / period;
}

export function impulseLong(candles: Candle[]) {
  if (candles.length < 200) return { ok: false as const, reason: "Мало свечей для EMA 200", entry: 0, stop: 0 };
  const closes = candles.map((candle) => candle.close);
  const price = closes[closes.length - 1];
  const k = 2 / 201;
  let trend = closes[0];
  for (let i = 1; i < closes.length; i += 1) trend = closes[i] * k + trend * (1 - k);
  const prior = candles.slice(-21, -1);
  const upper = Math.max(...prior.map((candle) => candle.high));
  const lower = Math.min(...prior.map((candle) => candle.low));
  const width = atr(candles) ?? price * 0.01;
  const stop = Math.min(price - width * 1.5, lower);
  const distance = price > 0 ? (price - stop) / price : 1;
  if (!(price > upper && price > trend)) return { ok: false as const, reason: "Нет импульса Дончиана выше EMA 200", entry: price, stop };
  if (!(stop < price) || distance > 0.045) {
    return { ok: false as const, reason: "Стоп дальше 4,5% от входа. При плече 10x ордер блокируется.", entry: price, stop };
  }
  return { ok: true as const, reason: "", entry: price, stop, distance };
}

export function meanRevertLong(candles: Candle[]) {
  if (candles.length < 30) return { ok: false as const, reason: "Мало свечей для Боллинджера", entry: 0, stop: 0, trail: 0 };
  const slice = candles.slice(-20);
  const closes = slice.map((candle) => candle.close);
  const mean = closes.reduce((sum, value) => sum + value, 0) / closes.length;
  const sd = Math.sqrt(closes.reduce((sum, value) => sum + (value - mean) ** 2, 0) / closes.length);
  const price = candles[candles.length - 1].close;
  const z = sd > 0 ? (price - mean) / sd : 0;
  let weighted = 0;
  let volume = 0;
  for (const candle of slice) {
    const size = candle.vol && candle.vol > 0 ? candle.vol : 1;
    weighted += ((candle.high + candle.low + candle.close) / 3) * size;
    volume += size;
  }
  const vwap = volume > 0 ? weighted / volume : mean;
  const width = atr(candles) ?? price * 0.01;
  const trail = width * 3;
  const stop = price - trail;
  const distance = price > 0 ? (price - stop) / price : 1;
  if (!(stop < price) || distance > 0.045) {
    return { ok: false as const, reason: "Стоп ATR дальше 4,5% от входа. Ордер заблокирован.", entry: price, stop, trail };
  }
  if (!(z <= -1.5 && price <= mean - 2 * sd && price < vwap)) {
    return { ok: false as const, reason: `Нет возврата к среднему. Z ${z.toFixed(2)}, VWAP ${vwap.toPrecision(4)}.`, entry: price, stop, trail };
  }
  return { ok: true as const, reason: "", entry: price, stop, trail, distance };
}

export function riskContracts(balance: number, entry: number, stop: number, contractSize: number) {
  const perCoin = entry - stop;
  if (!(balance > 0) || !(perCoin > 0) || !(contractSize > 0)) return 0;
  return Math.floor((balance * 0.05) / perCoin / contractSize);
}
export function btcShock(candles: Candle[]) {
  const width = atr(candles);
  if (!width || candles.length < 20) return false;
  const last = candles[candles.length - 1].close;
  const prev = candles[candles.length - 5].close;
  return Math.abs(last - prev) > width * 2.2;
}

export function trendAgainstGrid(candles: Candle[]) {
  const closes = candles.map((candle) => candle.close);
  const reading = { rsi: rsi(closes), macd: macdHist(closes), cci: cci(candles) };
  const down =
    reading.rsi !== null && reading.macd !== null && reading.cci !== null && reading.rsi < 35 && reading.macd < 0 && reading.cci < -140;
  const up =
    reading.rsi !== null && reading.macd !== null && reading.cci !== null && reading.rsi > 82 && reading.macd > 0 && reading.cci > 140;
  return { ...reading, block: down || up };
}

export function capWeights(count: number, k: number) {
  const factor = Math.min(Math.max(k, 1), 1.35);
  const raw = Array.from({ length: count }, (_, index) => factor ** index);
  const normalize = (values: number[]) => {
    const sum = values.reduce((total, value) => total + value, 0) || 1;
    return values.map((value) => value / sum);
  };
  let parts = normalize(raw);
  if (Math.max(...parts) > 0.28) parts = normalize(parts.map((value) => Math.min(value, 0.28)));
  return parts;
}

export function liquidationPrice(entry: number, leverage: number) {
  const lev = Math.min(Math.max(leverage, 1), 10);
  return entry * (1 - 1 / lev + 0.004);
}

export function gridFitsLeverage(price: number, lowestBuy: number, entry: number) {
  const leverage = 10;
  const liq = liquidationPrice(entry, leverage);
  const room = price > 0 ? (price - liq) / price : 0;
  return { leverage, liq, room, ok: lowestBuy > 0 && liq < lowestBuy * 0.995 };
}

export function lossToLowest(notional: number, prices: number[], weights: number[], lowest: number) {
  let qty = 0;
  for (let index = 0; index < prices.length; index += 1) {
    const price = prices[index] ?? 0;
    if (!(price > 0)) continue;
    qty += (notional * (weights[index] ?? 0)) / price;
  }
  return notional - lowest * qty;
}
