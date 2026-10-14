import { readFileSync } from "node:fs";

import type { Settings } from "@/lib/desk-store";

export function xaiKey() {
  const fromEnv = process.env.XAI_API_KEY?.trim();
  if (fromEnv) return fromEnv;
  for (const file of ["secrets.env", ".env"]) {
    try {
      const line = readFileSync(file, "utf8")
        .split("\n")
        .find((row) => row.startsWith("XAI_API_KEY="));
      const value = line?.slice("XAI_API_KEY=".length).trim().replace(/^["']|["']$/g, "") ?? "";
      if (value) {
        process.env.XAI_API_KEY = value;
        return value;
      }
    } catch {
      /* файла может не быть */
    }
  }
  return "";
}

const BANDS = {
  marginPct: [100, 100],
  levels: [10, 14],
  sellLevels: [3, 6],
  maxDdPct: [8, 12],
  stepPct: [0.2, 0.6],
  rangePct: [4, 10],
  geo: [1, 1.08],
  volumeK: [1, 1.15],
  maxPrice: [0.2, 1],
} as const;

type TuneKey = keyof typeof BANDS;

export type TunePatch = Partial<Pick<Settings, TuneKey>>;

function clamp(key: TuneKey, value: number) {
  const [min, max] = BANDS[key];
  const next = Math.min(max, Math.max(min, value));
  return key === "levels" || key === "sellLevels" ? Math.round(next) : Number(next.toFixed(2));
}

export function readTune(raw: string): TunePatch | null {
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  const parsed = JSON.parse(raw.slice(start, end + 1)) as Record<string, unknown>;
  const patch: TunePatch = {};
  for (const key of Object.keys(BANDS) as TuneKey[]) {
    const value = parsed[key];
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    const next = clamp(key, value);
    patch[key] = next;
  }
  return Object.keys(patch).length ? patch : null;
}

const ACTIONS = ["pause60", "pause15m", "shrinkBatch", "keepMode", "widenStep", "skipPair", "raiseCap"] as const;

export type GrokCoach = { patch: TunePatch | null; action: string; note: string };

async function complete(system: string, user: string) {
  const key = xaiKey();
  if (!key) throw new Error("нет ключа xAI");
  const res = await fetch("https://api.x.ai/v1/chat/completions", {
    method: "POST",
    headers: {
      authorization: `Bearer ${key}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: "grok-4.5",
      temperature: 0,
      max_tokens: 220,
      messages: [
        { role: "system", content: system },
        { role: "user", content: user },
      ],
    }),
    signal: AbortSignal.timeout(20_000),
  });
  const raw = await res.text();
  if (!res.ok) {
    let reason = `Grok ${res.status}`;
    try {
      const body = JSON.parse(raw) as { error?: string | { message?: string; code?: string } };
      const err = body.error;
      reason = typeof err === "string" ? err : err?.message || err?.code || reason;
    } catch {
      /* тело не JSON */
    }
    throw new Error(reason);
  }
  const body = JSON.parse(raw) as { choices?: Array<{ message?: { content?: string } }> };
  return body.choices?.[0]?.message?.content ?? "";
}

function readCoach(raw: string): GrokCoach {
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start < 0 || end <= start) return { patch: null, action: "", note: "" };
  const parsed = JSON.parse(raw.slice(start, end + 1)) as Record<string, unknown>;
  const action = typeof parsed.action === "string" && (ACTIONS as readonly string[]).includes(parsed.action) ? parsed.action : "";
  const note = typeof parsed.note === "string" ? parsed.note.slice(0, 140) : "";
  return { patch: readTune(raw), action, note };
}

export async function askGrok(state: {
  symbol: string;
  price: number;
  equity: number;
  trades: number;
  wins: number;
  gate: string;
  settings: Settings;
  notes: string[];
  errors: string[];
}) {
  const text = await complete(
    "Ты учишь сеточного робота на фьючерсах MEXC и чинишь его ошибки 24/7. Цель: закрывать круги и растить счёт к 300000 RUB. Плечо всегда 10, маржу не снижай, стоп не отключай, шорт и вывод не предлагай. Верни один JSON. Пустой {} если менять нечего. Числа: levels, sellLevels, maxDdPct, stepPct, rangePct, geo, volumeK, maxPrice, не больше двух. Если в errors есть свежая ошибка, добавь action из списка: pause60, pause15m, shrinkBatch, keepMode, widenStep, skipPair, raiseCap и короткое note на русском.",
    JSON.stringify({
      symbol: state.symbol,
      price: state.price,
      equity: Number(state.equity.toFixed(2)),
      trades: state.trades,
      wins: state.wins,
      winrate: state.trades > 0 ? Number((state.wins / state.trades).toFixed(2)) : null,
      gate: state.gate,
      settings: state.settings,
      notes: state.notes.slice(0, 5),
      errors: state.errors.slice(0, 4),
    }),
  );
  return readCoach(text);
}
