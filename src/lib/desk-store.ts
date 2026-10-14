import { create } from "zustand";

export type Keys = {
  mexcKey: string;
  mexcSecret: string;
  tgToken: string;
  tgChat: string;
};

export type Settings = {
  leverage: number;
  marginPct: number;
  levels: number;
  sellLevels: number;
  deposit: number;
  maxDdPct: number;
  maxPrice: number;
  stepPct: number;
  rangePct: number;
  geo: number;
  volumeK: number;
};

export const DEFAULT_SETTINGS: Settings = {
  leverage: 10,
  marginPct: 100,
  levels: 12,
  sellLevels: 4,
  deposit: 329.25,
  maxDdPct: 10,
  maxPrice: 1,
  stepPct: 0.25,
  rangePct: 6,
  geo: 1.03,
  volumeK: 1,
};

export type LogLine = {
  id: number;
  ts: number;
  tone: "info" | "ok" | "warn" | "err";
  text: string;
  local?: boolean;
};

export type Point = { t: string; equity: number };

export type Renter = {
  name: string;
  realized: number;
  fee: number;
};

export type Level = {
  price: number;
  side: "buy" | "sell";
  filled: boolean;
  vol: number;
};

export type ServerBook = {
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
  logs: LogLine[];
};

type DeskState = {
  keys: Keys;
  settings: Settings;
  hydrated: boolean;
  serverReady: boolean;
  mexcStatus: string;
  tgStatus: string;
  running: boolean;
  autonomous: boolean;
  rebuild: boolean;
  lessons: number;
  role: "owner" | "renter";
  activeRenter: string;
  renters: Renter[];
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
  logs: LogLine[];
  setKeys: (patch: Partial<Keys>) => void;
  setSettings: (patch: Partial<Settings>) => void;
  setHydrated: (value: boolean) => void;
  setMexcStatus: (value: string) => void;
  setTgStatus: (value: string) => void;
  setAutonomous: (value: boolean) => void;
  setRole: (role: "owner" | "renter") => void;
  beginRental: (name: string) => void;
  forgetKeys: () => void;
  ingestServer: (snap: ServerBook) => void;
  pushLog: (tone: LogLine["tone"], text: string) => void;
  applyTick: (symbol: string, price: number, step: number) => void;
  quickBuy: (symbol: string, price: number) => void;
  start: () => void;
  stop: (reason?: string) => void;
  resetBook: () => void;
  requestRebuild: () => void;
};

const STORAGE_KEY = "koridor-desk-v3";
let logSeq = 1;

function stamp() {
  return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function freshBook(deposit: number) {
  return {
    symbol: "",
    price: 0,
    cash: deposit,
    position: 0,
    avg: 0,
    equity: deposit,
    trades: 0,
    wins: 0,
    levels: [] as Level[],
    series: [{ t: stamp(), equity: deposit }] as Point[],
  };
}

export const useDesk = create<DeskState>((set, get) => ({
  keys: { mexcKey: "", mexcSecret: "", tgToken: "", tgChat: "" },
  settings: { ...DEFAULT_SETTINGS },
  hydrated: false,
  serverReady: false,
  mexcStatus: "Ключ не проверен",
  tgStatus: "Бот не проверен",
  running: true,
  autonomous: true,
  rebuild: false,
  lessons: 0,
  role: "owner",
  activeRenter: "",
  renters: [],
  ...freshBook(DEFAULT_SETTINGS.deposit),
  logs: [
    {
      id: logSeq++,
      ts: 0,
      tone: "info",
      local: true,
      text: "Ключ сохраняется кнопкой «Сохранить». Вывод с MEXC в роботе отсутствует.",
    },
  ],
  setKeys: (patch) => {
    set((s) => ({ keys: { ...s.keys, ...patch } }));
    saveDesk();
  },
  setSettings: (patch) => set((s) => ({ settings: { ...s.settings, ...patch } })),
  setHydrated: (value) => set({ hydrated: value }),
  setMexcStatus: (value) => set({ mexcStatus: value }),
  setTgStatus: (value) => set({ tgStatus: value }),
  setAutonomous: (value) => set({ autonomous: value }),
  setRole: (role) => set({ role, activeRenter: role === "owner" ? "" : get().activeRenter }),
  beginRental: (name) => {
    const clean = name.trim().slice(0, 32);
    if (!clean) return;
    set((s) => ({
      role: "renter",
      activeRenter: clean,
      renters: s.renters.some((r) => r.name === clean) ? s.renters : [...s.renters, { name: clean, realized: 0, fee: 0 }],
    }));
    get().pushLog("info", `Аренда для ${clean}. Ставка 5% с прибыли, без вывода с биржи.`);
    saveDesk();
  },
  forgetKeys: () => {
    set({ keys: { mexcKey: "", mexcSecret: "", tgToken: "", tgChat: "" } });
    saveDesk();
  },
  ingestServer: (snap) =>
    set((s) => ({
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
      settings: s.serverReady ? s.settings : { ...DEFAULT_SETTINGS, ...snap.settings },
      serverReady: true,
      logs: [
        ...snap.logs.map((line) => ({ ...line, local: false })),
        ...s.logs.filter((line) => line.local),
      ].slice(0, 80),
    })),
  pushLog: (tone, text) =>
    set((s) => ({
      logs: [{ id: logSeq++, ts: Date.now(), tone, text, local: true }, ...s.logs].slice(0, 80),
    })),
  start: () => set({ running: true }),
  quickBuy: () => {
    get().pushLog("warn", "Бумажная покупка отключена.");
  },
  stop: (reason) => {
    set({ running: false, autonomous: false, position: 0, avg: 0 });
    if (reason) get().pushLog("warn", reason);
  },
  resetBook: () => set({ ...freshBook(get().settings.deposit), running: false, rebuild: false }),
  requestRebuild: () => set({ rebuild: true }),
  applyTick: () => {
    return;
  },
}));

export function loadSavedDesk() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as { settings?: Settings; keys?: Partial<Keys> };
    if (parsed.settings) useDesk.getState().setSettings({ ...DEFAULT_SETTINGS, ...parsed.settings });
    if (parsed.keys) useDesk.getState().setKeys(parsed.keys);
  } catch {
    /* ignore broken storage */
  }
}

export function saveDesk() {
  try {
    const { settings, keys } = useDesk.getState();
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ settings, keys }));
  } catch {
    /* встроенный браузер может запретить localStorage, ключ всё равно пишется на сервер */
  }
}

export const STARTING_CASH = DEFAULT_SETTINGS.deposit;
