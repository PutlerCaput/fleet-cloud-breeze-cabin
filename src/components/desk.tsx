import { useEffect, useState } from "react";
import { Activity, KeyRound, Radio } from "lucide-react";
import { engineCommand, engineSnapshot } from "@/lib/engine.functions";
import { loadSavedDesk, saveDesk, useDesk } from "@/lib/desk-store";
import { EquityChart } from "@/components/equity-chart";

const card =
  "rounded-3xl border border-white/6 bg-surface/80 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.05),0_24px_50px_-36px_rgba(0,0,0,0.9)]";

function Field({
  label,
  value,
  onChange,
  placeholder,
  secret,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  secret?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.16em] text-muted">{label}</span>
      <span className="flex overflow-hidden rounded-xl border border-line bg-bg transition focus-within:border-accent focus-within:shadow-[0_0_0_3px_rgba(214,162,74,0.16)]">
        <input
          className="min-w-0 flex-1 bg-transparent px-3 py-3 text-sm text-fg outline-none placeholder:text-muted"
          type={secret && !open ? "password" : "text"}
          autoComplete="off"
          spellCheck={false}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
        {secret ? (
          <button type="button" className="px-3 text-xs uppercase tracking-wide text-muted hover:text-fg" onClick={() => setOpen((v) => !v)}>
            {open ? "Скрыть" : "Показать"}
          </button>
        ) : null}
      </span>
    </label>
  );
}

export function Desk() {
  const keys = useDesk((s) => s.keys);
  const settings = useDesk((s) => s.settings);
  const running = useDesk((s) => s.running);
  const symbol = useDesk((s) => s.symbol);
  const price = useDesk((s) => s.price);
  const equity = useDesk((s) => s.equity);
  const trades = useDesk((s) => s.trades);
  const wins = useDesk((s) => s.wins);
  const position = useDesk((s) => s.position);
  const levels = useDesk((s) => s.levels);
  const series = useDesk((s) => s.series);
  const logs = useDesk((s) => s.logs);
  const mexcStatus = useDesk((s) => s.mexcStatus);
  const tgStatus = useDesk((s) => s.tgStatus);
  const setKeys = useDesk((s) => s.setKeys);
  const setSettings = useDesk((s) => s.setSettings);
  const pushLog = useDesk((s) => s.pushLog);
  const ingestServer = useDesk((s) => s.ingestServer);
  const forgetKeys = useDesk((s) => s.forgetKeys);
  const setTgStatus = useDesk((s) => s.setTgStatus);
  const setMexcStatus = useDesk((s) => s.setMexcStatus);
  const [busy, setBusy] = useState<"tg" | "buy" | "save" | null>(null);
  const [beat, setBeat] = useState(0);
  const [ticks, setTicks] = useState(0);
  const [mode, setMode] = useState<"paper" | "live">("paper");
  const [gate, setGate] = useState("Ключ ещё не сохранён.");
  const [rub, setRub] = useState(0);
  const [grokSync, setGrokSync] = useState("");
  const [keySaved, setKeySaved] = useState(false);
  const [chartOn, setChartOn] = useState(false);
  const [more, setMore] = useState(false);

  useEffect(() => {
    loadSavedDesk();
    setChartOn(true);
    const saved = useDesk.getState().keys;
    if (saved.mexcKey.trim().length >= 8 && saved.mexcSecret.trim().length >= 8) {
      void engineCommand({ data: { type: "arm", apiKey: saved.mexcKey, secret: saved.mexcSecret } }).then((snap) => {
        useDesk.getState().ingestServer(snap);
      });
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const pull = async () => {
      try {
        const snap = await engineSnapshot();
        if (cancelled) return;
        setBeat(snap.beat);
        setTicks(snap.ticks);
        setMode(snap.mode);
        setGate(snap.gate);
        setRub(snap.rub);
        setGrokSync(snap.grokSync || "");
        setKeySaved(Boolean(snap.keySaved));
        ingestServer(snap);
      } catch (error) {
        if (!cancelled) pushLog("err", error instanceof Error ? error.message : "Сервер сетки не ответил");
      }
    };
    void pull();
    const timer = window.setInterval(() => void pull(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [ingestServer, pushLog]);

  async function onQuickBuy() {
    setBusy("buy");
    try {
      ingestServer(await engineCommand({ data: { type: "buy" } }));
    } catch (error) {
      pushLog("err", error instanceof Error ? error.message : "Покупка не прошла");
    } finally {
      setBusy(null);
    }
  }

  async function onApply() {
    saveDesk();
    try {
      ingestServer(await engineCommand({ data: { type: "apply", settings: useDesk.getState().settings } }));
      pushLog("ok", "Настройки применены.");
    } catch (error) {
      pushLog("err", error instanceof Error ? error.message : "Сервер не принял настройки");
    }
  }

  async function onSaveKeys() {
    const apiKey = keys.mexcKey.trim();
    const secret = keys.mexcSecret.trim();
    if (apiKey.length < 8 || secret.length < 8) {
      setMexcStatus("Вставьте API Key и Secret, затем нажмите «Сохранить».");
      return;
    }
    setBusy("save");
    saveDesk();
    try {
      const snap = await engineCommand({ data: { type: "save", apiKey, secret } });
      ingestServer(snap);
      setMode(snap.mode);
      setGate(snap.gate);
      setKeySaved(Boolean(snap.keySaved));
      const text = snap.keySaved ? snap.gate : "Сервер не записал ключ.";
      setMexcStatus(text);
      pushLog(snap.mode === "live" ? "ok" : "err", text);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Не удалось сохранить ключ";
      setMexcStatus(message);
      pushLog("err", message);
    } finally {
      setBusy(null);
    }
  }

  async function onTestTelegram() {
    setBusy("tg");
    try {
      const result = await engineCommand({ data: { type: "notify", token: keys.tgToken, chat: keys.tgChat } });
      ingestServer(result);
      const text = result.logs[0]?.text || "Telegram проверен";
      setTgStatus(text);
      pushLog(text.startsWith("Telegram принят") ? "ok" : "err", text);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Не удалось проверить Telegram";
      setTgStatus(message);
      pushLog("err", message);
    } finally {
      setBusy(null);
    }
  }

  const status = keySaved ? `Ключ на месте. ${gate}` : mexcStatus && mexcStatus !== "Ключ не проверен" ? mexcStatus : gate;
  const winrate = trades > 0 ? Math.round((wins / trades) * 100) : 0;
  const goalPct = rub > 0 ? Math.min(100, ((equity * rub) / 300_000) * 100) : 0;
  const live = mode === "live";

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
      <header className={`${card} flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between`}>
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-semibold tracking-tight">Коридор</h1>
            <span className="inline-flex items-center gap-2 rounded-full border border-line bg-bg/70 px-3 py-1.5 font-mono text-xs text-muted">
              <span className={`size-2 rounded-full ${live ? "bg-up" : running ? "bg-accent" : "bg-muted"}`} />
              {live ? "Торгует" : keySaved ? "Ключ на месте" : running ? "Ждёт ключ" : "Пауза"}
              {beat > 0 ? ` · ${ticks}` : ""}
            </span>
          </div>
          <p className="mt-3 max-w-lg text-sm leading-6 text-muted">{status}</p>
          {grokSync ? <p className="mt-1 text-xs text-muted">{grokSync}</p> : null}
        </div>
        <div className="w-full sm:max-w-xs">
          <p className="font-mono text-3xl tracking-tight">{equity.toFixed(2)} <span className="text-base text-muted">USDT</span></p>
          <p className="text-sm text-muted">{rub > 0 ? `${Math.round(equity * rub).toLocaleString("ru-RU")} ₽` : "жду курс"}</p>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-raised">
            <div className="h-full rounded-full bg-accent" style={{ width: `${goalPct}%` }} />
          </div>
          <p className="mt-1.5 text-xs text-muted">{rub > 0 ? `до 300 000 ₽ · ${goalPct.toFixed(1)}%` : "нет курса"}</p>
        </div>
      </header>

      <section className="mt-3 grid gap-3 sm:grid-cols-3">
        <Metric label="Пара" value={symbol ? symbol.replace("_", "/") : "—"} hint={price ? price.toPrecision(5) : "жду цену"} />
        <Metric label="Сделки" value={String(trades)} hint={`винрейт ${winrate}%`} />
        <Metric label="Позиция" value={position > 0 ? "лонг" : "нет"} hint={`${levels.filter((level) => !level.filled).length} уровней в сетке`} />
      </section>

      <section className="mt-3 grid gap-3 lg:grid-cols-[1.1fr_0.9fr]">
        <article className={card}>
          <div className="mb-4 flex items-center gap-2">
            <KeyRound className="size-4 text-accent" />
            <h2 className="text-base font-medium">Ключ MEXC</h2>
          </div>
          <div className="grid gap-3">
            <Field label="API Key" value={keys.mexcKey} placeholder={keySaved ? "Ключ уже на сервере" : "mx0..."} secret onChange={(mexcKey) => setKeys({ mexcKey })} />
            <Field label="Secret" value={keys.mexcSecret} placeholder={keySaved ? "Секрет уже на сервере" : "Секрет API"} secret onChange={(mexcSecret) => setKeys({ mexcSecret })} />
          </div>
          <p className={`mt-3 text-sm ${keySaved ? "text-up" : "text-muted"}`}>
            {keySaved ? "Ключ записан на сервере и не пропадёт после обновления страницы." : "Нажмите «Сохранить». Пока этой надписи нет, ключ не записан."}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" disabled={busy !== null} onClick={() => void onSaveKeys()} className="min-h-11 rounded-xl bg-accent px-4 text-sm font-medium text-accent-fg disabled:opacity-40">
              {busy === "save" ? "Сохраняю…" : "Сохранить"}
            </button>
            <button type="button" onClick={() => void engineCommand({ data: { type: running ? "stop" : "start" } }).then(ingestServer)} className={`min-h-11 rounded-xl px-4 text-sm font-medium ${running ? "bg-down text-bg" : "border border-line"}`}>
              {running ? "Стоп" : "Пуск"}
            </button>
            <button type="button" disabled={busy !== null} onClick={() => void onQuickBuy()} className="min-h-11 rounded-xl border border-line px-4 text-sm disabled:opacity-40">
              {busy === "buy" ? "…" : "Купить"}
            </button>
            <button
              type="button"
              onClick={() => {
                forgetKeys();
                setKeySaved(false);
                setMexcStatus("Стираю ключ с сервера…");
                void engineCommand({ data: { type: "forget" } }).then((snap) => {
                  ingestServer(snap);
                  setKeySaved(Boolean(snap.keySaved));
                  setGate(snap.gate);
                  setMexcStatus(snap.gate);
                });
              }}
              className="min-h-11 px-2 text-sm text-muted"
            >
              Стереть
            </button>
          </div>
        </article>

        <article className={card}>
          <h2 className="mb-4 text-base font-medium">Сетка</h2>
          <div className="grid grid-cols-2 gap-3">
            <Num label="Шаг, %" value={settings.stepPct} min={0} max={5} step={0.1} onChange={(stepPct) => setSettings({ stepPct })} />
            <Num label="Коридор, %" value={settings.rangePct} min={3} max={40} step={0.5} onChange={(rangePct) => setSettings({ rangePct })} />
            <Num label="Покупки" value={settings.levels} min={3} max={20} step={1} onChange={(levels) => setSettings({ levels })} />
            <Num label="Стоп дня, %" value={settings.maxDdPct} min={1} max={30} step={0.5} onChange={(maxDdPct) => setSettings({ maxDdPct })} />
          </div>
          {more ? (
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Num label="Продажи" value={settings.sellLevels} min={1} max={12} step={1} onChange={(sellLevels) => setSettings({ sellLevels })} />
              <Num label="Депозит" value={settings.deposit} min={10} max={1000000} step={10} onChange={(deposit) => setSettings({ deposit })} />
            </div>
          ) : null}
          <div className="mt-4 flex gap-2">
            <button type="button" onClick={() => void onApply()} className="min-h-11 rounded-xl bg-accent px-4 text-sm font-medium text-accent-fg">
              Применить
            </button>
            <button type="button" onClick={() => setMore((v) => !v)} className="min-h-11 px-2 text-sm text-muted">
              {more ? "Скрыть" : "Ещё"}
            </button>
          </div>
        </article>
      </section>

      <details className={`mt-3 ${card}`}>
        <summary className="cursor-pointer text-sm text-muted">Telegram, если нужен отчёт</summary>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field label="Bot token" value={keys.tgToken} placeholder="123456:ABC..." secret onChange={(tgToken) => setKeys({ tgToken })} />
          <Field label="Chat ID" value={keys.tgChat} placeholder="id чата" onChange={(tgChat) => setKeys({ tgChat })} />
        </div>
        <p className="mt-3 text-sm text-muted">{tgStatus}</p>
        <button type="button" disabled={busy !== null || keys.tgToken.length < 20 || keys.tgChat.length < 1} onClick={() => void onTestTelegram()} className="mt-3 min-h-11 rounded-xl border border-line px-4 text-sm disabled:opacity-40">
          {busy === "tg" ? "Отправка…" : "Проверить"}
        </button>
      </details>

      <section className="mt-3 grid gap-3 lg:grid-cols-[1.4fr_0.8fr]">
        <article className={card}>
          <div className="mb-2 flex items-center gap-2">
            <Activity className="size-4 text-accent" />
            <h2 className="text-base font-medium">Капитал</h2>
          </div>
          <div className="h-52">{chartOn ? <EquityChart data={series} /> : <div className="h-full rounded-xl bg-raised" />}</div>
        </article>
        <article className={card}>
          <div className="mb-3 flex items-center gap-2">
            <Radio className="size-4 text-accent" />
            <h2 className="text-base font-medium">Уровни</h2>
          </div>
          {levels.length === 0 ? (
            <p className="text-sm text-muted">Появятся после первого тика.</p>
          ) : (
            <ul className="max-h-52 space-y-1.5 overflow-auto font-mono text-xs">
              {[...levels]
                .sort((a, b) => b.price - a.price)
                .map((level) => (
                  <li key={`${level.side}-${level.price}`} className="flex items-center justify-between gap-3 rounded-lg bg-bg/70 px-3 py-2">
                    <span className={level.side === "buy" ? "text-up" : "text-down"}>{level.side === "buy" ? "BUY" : "SELL"}</span>
                    <span>{level.price.toPrecision(5)}</span>
                    <span className="text-muted">{level.side === "buy" ? ((level.vol ?? 0) > 0 ? level.vol : "—") : "закрыть"}</span>
                  </li>
                ))}
            </ul>
          )}
        </article>
      </section>

      <section className={`mt-3 ${card}`}>
        <h2 className="text-base font-medium">Журнал</h2>
        <ul className="mt-3 max-h-48 space-y-1 overflow-auto">
          {logs.slice(0, 12).map((line) => (
            <li key={line.local ? `local-${line.id}` : `server-${line.ts}-${line.id}`} className="flex gap-3 text-sm">
              <time className="shrink-0 font-mono text-xs text-muted">
                {line.ts ? new Date(line.ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "—"}
              </time>
              <span className={line.tone === "ok" ? "text-up" : line.tone === "err" ? "text-down" : line.tone === "warn" ? "text-accent" : "text-fg"}>{line.text}</span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function Num({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.14em] text-muted">{label}</span>
      <input
        type="number"
        inputMode="decimal"
        min={min}
        max={max}
        step={step}
        value={Number.isFinite(value) ? value : min}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (!Number.isFinite(next)) return;
          onChange(Math.min(max, Math.max(min, next)));
        }}
        className="w-full rounded-xl border border-line bg-bg px-3 py-3 font-mono text-sm text-fg outline-none"
      />
    </label>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <article className="rounded-2xl border border-white/6 bg-raised/80 px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-muted">{label}</p>
      <p className="mt-1 truncate font-mono text-lg">{value}</p>
      <p className="truncate text-xs text-muted">{hint}</p>
    </article>
  );
}
