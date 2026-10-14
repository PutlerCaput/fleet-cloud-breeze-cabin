import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { explainAccessError, refreshEgress } from "@/lib/mexc-live";
import type { Candle } from "@/lib/market-math";

const MEXC = "https://api.mexc.com";

export type Ticker = {
  symbol: string;
  lastPrice: number;
  amount24: number;
  high24Price: number;
  lower24Price: number;
  riseFallRate: number;
  bid: number;
  ask: number;
  fundingRate: number;
};

type MexcResult =
  | { ok: true; currency: string; available: number; equity: number }
  | { ok: false; message: string };

type TgResult = { ok: true; username: string } | { ok: false; message: string };

async function signHeaders(apiKey: string, secret: string, paramString: string) {
  const { createHmac } = await import("node:crypto");
  const timestamp = Date.now().toString();
  const signature = createHmac("sha256", secret)
    .update(apiKey + timestamp + paramString)
    .digest("hex");
  return {
    ApiKey: apiKey,
    "Request-Time": timestamp,
    Signature: signature,
    "Content-Type": "application/json",
  };
}

export const testMexcKeys = createServerFn({ method: "POST" })
  .validator(
    z.object({
      apiKey: z.string().trim().min(8).max(128),
      secret: z.string().trim().min(8).max(128),
    }),
  )
  .handler(async ({ data }): Promise<MexcResult> => {
    try {
      await refreshEgress();
      const headers = await signHeaders(data.apiKey, data.secret, "");
      const res = await fetch(`${MEXC}/api/v1/private/account/assets`, {
        headers,
        signal: AbortSignal.timeout(12_000),
      });
      const body = (await res.json()) as {
        success?: boolean;
        code?: number;
        message?: string;
        data?: Array<{
          currency?: string;
          availableBalance?: number | string;
          equity?: number | string;
        }>;
      };
      if (!res.ok || body.success === false || (body.code && body.code !== 0)) {
        return {
          ok: false,
          message: explainAccessError(body.message || String(body.code || res.status)),
        };
      }
      const rows = body.data ?? [];
      const usdt = rows.find((r) => r.currency === "USDT") ?? rows[0];
      if (!usdt) return { ok: false, message: "Ключ принят, но фьючерсный баланс пуст." };
      return {
        ok: true,
        currency: usdt.currency || "USDT",
        available: Number(usdt.availableBalance ?? 0),
        equity: Number(usdt.equity ?? usdt.availableBalance ?? 0),
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Сеть недоступна";
      return { ok: false, message };
    }
  });

export const testTelegram = createServerFn({ method: "POST" })
  .validator(
    z.object({
      token: z.string().trim().min(20).max(120),
      chatId: z.string().trim().min(1).max(64),
    }),
  )
  .handler(async ({ data }): Promise<TgResult> => {
    const base = `https://api.telegram.org/bot${data.token}`;
    try {
      const meRes = await fetch(`${base}/getMe`, { signal: AbortSignal.timeout(12_000) });
      const me = (await meRes.json()) as {
        ok?: boolean;
        description?: string;
        result?: { username?: string };
      };
      if (!me.ok || !me.result?.username) {
        return { ok: false, message: me.description || "Токен бота не принят Telegram." };
      }
      const sendRes = await fetch(`${base}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: data.chatId,
          text: "Коридор на связи. Ключ Telegram принят, тестовое сообщение доставлено.",
        }),
        signal: AbortSignal.timeout(12_000),
      });
      const sent = (await sendRes.json()) as { ok?: boolean; description?: string };
      if (!sent.ok) {
        return {
          ok: false,
          message:
            sent.description ||
            "Бот найден, но сообщение не ушло. Напишите боту /start и проверьте chat id.",
        };
      }
      return { ok: true, username: me.result.username };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Telegram недоступен";
      return { ok: false, message };
    }
  });

export async function loadTickers(): Promise<Ticker[]> {
  const res = await fetch(`${MEXC}/api/v1/contract/ticker`, {
    signal: AbortSignal.timeout(12_000),
  });
  if (!res.ok) throw new Error(`Котировки недоступны (${res.status})`);
  const body = (await res.json()) as { data?: unknown };
  const rows = Array.isArray(body.data) ? body.data : [];
  const parsed: Ticker[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const item = row as Record<string, unknown>;
    const symbol = String(item.symbol ?? "");
    if (!symbol.endsWith("_USDT")) continue;
    const lastPrice = Number(item.lastPrice);
    const amount24 = Number(item.amount24);
    if (!Number.isFinite(lastPrice) || lastPrice <= 0 || lastPrice >= 100_000) continue;
    if (!Number.isFinite(amount24) || amount24 < 8_000_000) continue;
    parsed.push({
      symbol,
      lastPrice,
      amount24,
      high24Price: Number(item.high24Price) || lastPrice,
      lower24Price: Number(item.lower24Price) || lastPrice,
      riseFallRate: Number(item.riseFallRate) || 0,
      bid: Number(item.bid1) || lastPrice,
      ask: Number(item.ask1) || lastPrice,
      fundingRate: Number(item.fundingRate) || 0,
    });
  }
  parsed.sort((a, b) => b.amount24 - a.amount24);
  return parsed.slice(0, 12);
}

const candleCache = new Map<string, Candle[]>();

function parseKline(body: unknown): Candle[] {
  const data = (body as { data?: unknown } | null)?.data;
  const candles: Candle[] = [];
  if (Array.isArray(data)) {
    for (const row of data) {
      const item = row as { high?: number; low?: number; close?: number; vol?: number } | number[];
      const candle = Array.isArray(item)
        ? { high: Number(item[2]), low: Number(item[3]), close: Number(item[4]), vol: Number(item[5] ?? 0) }
        : { high: Number(item.high), low: Number(item.low), close: Number(item.close), vol: Number(item.vol ?? 0) };
      if (candle.close > 0) candles.push(candle);
    }
    return candles;
  }
  const column = data as { high?: unknown[]; low?: unknown[]; close?: unknown[]; vol?: unknown[] } | undefined;
  const high = column?.high;
  const low = column?.low;
  const close = column?.close;
  const vol = column?.vol;
  if (!Array.isArray(high) || !Array.isArray(low) || !Array.isArray(close)) return [];
  for (let i = 0; i < close.length; i += 1) {
    const candle = { high: Number(high[i]), low: Number(low[i]), close: Number(close[i]), vol: Number(vol?.[i] ?? 0) };
    if (candle.close > 0) candles.push(candle);
  }
  return candles;
}

export async function loadCandles(symbol: string): Promise<Candle[]> {
  const start = Math.floor((Date.now() - 10 * 24 * 60 * 60 * 1000) / 1000);
  const urls = [
    `${MEXC}/api/v1/contract/kline/${symbol}?interval=Min15`,
    `${MEXC}/api/v1/contract/kline/${symbol}?interval=Min15&start=${start}`,
    `${MEXC}/api/v1/contract/kline/${symbol}?interval=Min5`,
  ];
  for (const url of urls) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(12_000) });
      if (!res.ok) continue;
      const candles = parseKline(await res.json());
      if (candles.length >= 30) {
        candleCache.set(symbol, candles);
        return candles;
      }
    } catch {
      /* следующий адрес */
    }
  }
  return candleCache.get(symbol) ?? [];
}

export const fetchMarket = createServerFn({ method: "GET" }).handler(async () => loadTickers());
