import { i as __toESM } from "../_runtime.mjs";
import { b as require_jsx_runtime, q as require_react } from "../_libs/@tanstack/react-router+[...].mjs";
import { a as object, i as number, n as discriminatedUnion, o as string, r as literal, t as _enum } from "../_libs/zod.mjs";
import { t as createServerFn } from "./ssr.mjs";
import { c as testTelegram, i as loadSavedDesk, l as useDesk, n as MEXC_WHITELIST_IP, o as saveDesk, r as createSsrRpc, s as testMexcKeys } from "./whitelist-DREGUXAX.mjs";
import { a as KeyRound, i as Radio, n as Shield, o as Activity, r as Send } from "../_libs/lucide-react.mjs";
import { a as Tooltip, i as ResponsiveContainer, n as YAxis, r as Area, t as AreaChart } from "../_libs/recharts+[...].mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/routes-DuqFO5Sd.js
var import_react = /* @__PURE__ */ __toESM(require_react());
var import_jsx_runtime = require_jsx_runtime();
var settingsSchema = object({
	leverage: number().min(1).max(20),
	marginPct: number().min(1).max(40),
	levels: number().min(3).max(20),
	sellLevels: number().min(1).max(12),
	deposit: number().min(10).max(1e6),
	maxDdPct: number().min(1).max(30),
	maxPrice: number().min(1e-4).max(1e5),
	stepPct: number().min(0).max(5),
	rangePct: number().min(3).max(40),
	geo: number().min(1).max(1.5),
	volumeK: number().min(1).max(2)
});
var commandSchema = discriminatedUnion("type", [
	object({
		type: literal("apply"),
		settings: settingsSchema
	}),
	object({ type: literal("start") }),
	object({ type: literal("stop") }),
	object({ type: literal("reset") }),
	object({ type: literal("buy") }),
	object({
		type: literal("rent"),
		name: string().max(32)
	}),
	object({
		type: literal("role"),
		role: _enum(["owner", "renter"])
	})
]);
var engineSnapshot = createServerFn({ method: "GET" }).handler(createSsrRpc("9791e6ba83aaf141257154bd5b1c8febde0471d95aac2caf385e60fafd2c1494"));
var engineCommand = createServerFn({ method: "POST" }).validator(commandSchema).handler(createSsrRpc("ebc58a7e621858163b99bdbc0220b0e5e4daf13552a6a0c53a21e03abcdb353a"));
function EquityChart({ data }) {
	return /* @__PURE__ */ (0, import_jsx_runtime.jsx)(ResponsiveContainer, {
		width: "100%",
		height: "100%",
		children: /* @__PURE__ */ (0, import_jsx_runtime.jsxs)(AreaChart, {
			data,
			margin: {
				top: 8,
				right: 8,
				left: 0,
				bottom: 0
			},
			children: [
				/* @__PURE__ */ (0, import_jsx_runtime.jsx)(YAxis, {
					hide: true,
					domain: ["dataMin - 2", "dataMax + 2"]
				}),
				/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Tooltip, {
					contentStyle: {
						background: "#181b18",
						border: "1px solid #2e342c",
						borderRadius: 8,
						color: "#e8ebe4",
						fontFamily: "IBM Plex Mono, ui-monospace, monospace",
						fontSize: 12
					},
					formatter: (value) => [`${Number(value).toFixed(2)} USDT`, "Капитал"]
				}),
				/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Area, {
					type: "monotone",
					dataKey: "equity",
					stroke: "#d6a24a",
					fill: "#d6a24a",
					fillOpacity: .16,
					strokeWidth: 2,
					isAnimationActive: false
				})
			]
		})
	});
}
function Field({ label, value, onChange, placeholder, secret }) {
	const [open, setOpen] = (0, import_react.useState)(false);
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("label", {
		className: "block",
		children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
			className: "mb-1.5 block text-sm text-muted",
			children: label
		}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", {
			className: "flex overflow-hidden rounded-md border border-line bg-bg",
			children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("input", {
				className: "min-w-0 flex-1 bg-transparent px-3 py-3 text-sm text-fg outline-none placeholder:text-muted",
				type: secret && !open ? "password" : "text",
				autoComplete: "off",
				spellCheck: false,
				value,
				placeholder,
				onChange: (event) => onChange(event.target.value)
			}), secret ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
				type: "button",
				className: "px-3 text-sm text-muted hover:text-fg",
				onClick: () => setOpen((v) => !v),
				children: open ? "Скрыть" : "Показать"
			}) : null]
		})]
	});
}
function Desk() {
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
	const lessons = useDesk((s) => s.lessons);
	const role = useDesk((s) => s.role);
	const activeRenter = useDesk((s) => s.activeRenter);
	const renters = useDesk((s) => s.renters);
	const forgetKeys = useDesk((s) => s.forgetKeys);
	const setTgStatus = useDesk((s) => s.setTgStatus);
	const setMexcStatus = useDesk((s) => s.setMexcStatus);
	const [renterName, setRenterName] = (0, import_react.useState)("");
	const [busy, setBusy] = (0, import_react.useState)(null);
	const [beat, setBeat] = (0, import_react.useState)(0);
	const [ticks, setTicks] = (0, import_react.useState)(0);
	const [chartOn, setChartOn] = (0, import_react.useState)(false);
	(0, import_react.useEffect)(() => {
		loadSavedDesk();
		setChartOn(true);
	}, []);
	(0, import_react.useEffect)(() => {
		let cancelled = false;
		const pull = async () => {
			try {
				const snap = await engineSnapshot();
				if (cancelled) return;
				setBeat(snap.beat);
				setTicks(snap.ticks);
				ingestServer(snap);
			} catch (error) {
				if (!cancelled) {
					const message = error instanceof Error ? error.message : "Сервер сетки не ответил";
					pushLog("err", message);
				}
			}
		};
		pull();
		const timer = window.setInterval(() => void pull(), 4e3);
		return () => {
			cancelled = true;
			window.clearInterval(timer);
		};
	}, [ingestServer, pushLog]);
	async function onQuickBuy() {
		setBusy("buy");
		try {
			const snap = await engineCommand({ data: { type: "buy" } });
			ingestServer(snap);
		} catch (error) {
			const message = error instanceof Error ? error.message : "Быстрая покупка не прошла";
			pushLog("err", message);
		} finally {
			setBusy(null);
		}
	}
	async function onApply() {
		saveDesk();
		try {
			const snap = await engineCommand({ data: {
				type: "apply",
				settings: useDesk.getState().settings
			} });
			ingestServer(snap);
			pushLog("ok", "Настройки на сервере. Коридор перестроится на следующем тике.");
		} catch (error) {
			const message = error instanceof Error ? error.message : "Сервер не принял настройки";
			pushLog("err", message);
		}
	}
	async function onTestMexc() {
		setBusy("mexc");
		try {
			const result = await testMexcKeys({ data: {
				apiKey: keys.mexcKey,
				secret: keys.mexcSecret
			} });
			if (result.ok) {
				const text = `MEXC принял ключ. ${result.currency}: доступно ${result.available.toFixed(2)}, equity ${result.equity.toFixed(2)}. Ключ стёрт из памяти.`;
				setMexcStatus(text);
				pushLog("ok", text);
			} else {
				setMexcStatus(result.message);
				pushLog("err", result.message);
			}
		} catch (error) {
			const message = error instanceof Error ? error.message : "Не удалось проверить MEXC";
			setMexcStatus(message);
			pushLog("err", message);
		} finally {
			forgetKeys();
			setBusy(null);
		}
	}
	async function onTestTelegram() {
		setBusy("tg");
		try {
			const result = await testTelegram({ data: {
				token: keys.tgToken,
				chatId: keys.tgChat
			} });
			if (result.ok) {
				const text = `Telegram @${result.username}: сообщение отправлено. Токен стёрт из памяти.`;
				setTgStatus(text);
				pushLog("ok", text);
			} else {
				setTgStatus(result.message);
				pushLog("err", result.message);
			}
		} catch (error) {
			const message = error instanceof Error ? error.message : "Не удалось проверить Telegram";
			setTgStatus(message);
			pushLog("err", message);
		} finally {
			forgetKeys();
			setBusy(null);
		}
	}
	const winrate = trades > 0 ? Math.round(wins / trades * 100) : 0;
	const delta = equity - settings.deposit;
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("main", {
		className: "mx-auto min-h-screen max-w-6xl px-4 py-6 sm:px-6 sm:py-8",
		children: [
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("header", {
				className: "flex flex-col gap-4 border-b border-line pb-6 sm:flex-row sm:items-end sm:justify-between",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", { children: [
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "font-mono text-xs tracking-widest text-accent",
						children: "MEXC FUTURES · ISOLATED"
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h1", {
						className: "mt-1 text-3xl font-semibold tracking-tight",
						children: "Коридор"
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "mt-2 max-w-xl text-sm leading-6 text-muted",
						children: "Автономная сетка считается на сервере приложения и не зависит от этой вкладки, пока процесс запущен. Ключи не сохраняются, вывод с MEXC выключен."
					})
				] }), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
					className: "flex items-center gap-2",
					children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", { className: `size-2 rounded-full ${running ? "bg-up" : "bg-muted"}` }), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", {
						className: "font-mono text-sm",
						children: [running ? "Сервер торгует" : "Сервер на паузе", beat > 0 ? ` · тик ${ticks}` : ""]
					})]
				})]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "mt-6 grid gap-4 lg:grid-cols-2",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("article", {
					className: "rounded-md border border-line bg-surface p-4 sm:p-5",
					children: [
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "mb-4 flex items-center gap-2",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)(KeyRound, { className: "size-4 text-accent" }), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
								className: "text-base font-medium",
								children: "Ключи MEXC"
							})]
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "grid gap-3",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Field, {
								label: "API Key",
								value: keys.mexcKey,
								placeholder: "mx0...",
								secret: true,
								onChange: (mexcKey) => setKeys({ mexcKey })
							}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)(Field, {
								label: "Secret",
								value: keys.mexcSecret,
								placeholder: "Секрет фьючерсного API",
								secret: true,
								onChange: (mexcSecret) => setKeys({ mexcSecret })
							})]
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
							className: "mt-3 text-sm leading-6 text-muted",
							children: mexcStatus
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "mt-4 flex flex-wrap gap-2",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								disabled: busy !== null || keys.mexcKey.length < 8 || keys.mexcSecret.length < 8,
								onClick: () => void onTestMexc(),
								className: "min-h-11 rounded-md bg-accent px-4 text-sm font-medium text-accent-fg disabled:opacity-40",
								children: busy === "mexc" ? "Проверка…" : "Проверить MEXC"
							}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								onClick: () => {
									forgetKeys();
									pushLog("ok", "Ключи стёрты из памяти");
								},
								className: "min-h-11 rounded-md border border-line px-4 text-sm",
								children: "Стереть ключи"
							})]
						})
					]
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("article", {
					className: "rounded-md border border-line bg-surface p-4 sm:p-5",
					children: [
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "mb-4 flex items-center gap-2",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Send, { className: "size-4 text-accent" }), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
								className: "text-base font-medium",
								children: "Ключ Telegram"
							})]
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "grid gap-3",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Field, {
								label: "Bot token",
								value: keys.tgToken,
								placeholder: "123456:ABC...",
								secret: true,
								onChange: (tgToken) => setKeys({ tgToken })
							}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)(Field, {
								label: "Chat ID",
								value: keys.tgChat,
								placeholder: "Числовой id чата",
								onChange: (tgChat) => setKeys({ tgChat })
							})]
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
							className: "mt-3 text-sm leading-6 text-muted",
							children: tgStatus
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", {
							className: "mt-4",
							children: /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								disabled: busy !== null || keys.tgToken.length < 20 || keys.tgChat.length < 1,
								onClick: () => void onTestTelegram(),
								className: "min-h-11 rounded-md bg-accent px-4 text-sm font-medium text-accent-fg disabled:opacity-40",
								children: busy === "tg" ? "Отправка…" : "Проверить Telegram"
							})
						})
					]
				})]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "mt-4 rounded-md border border-line bg-surface p-4 sm:p-5",
				children: [
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "mb-4 flex items-center gap-2",
						children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Shield, { className: "size-4 text-accent" }), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
							className: "text-base font-medium",
							children: "Настройки"
						})]
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "grid gap-3 sm:grid-cols-2 lg:grid-cols-4",
						children: [
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Депозит, USDT",
								value: settings.deposit,
								min: 10,
								max: 1e6,
								step: 10,
								onChange: (deposit) => setSettings({ deposit })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Плечо",
								value: settings.leverage,
								min: 1,
								max: 20,
								step: 1,
								onChange: (leverage) => setSettings({ leverage })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Маржа на сетку, %",
								value: settings.marginPct,
								min: 1,
								max: 40,
								step: 1,
								onChange: (marginPct) => setSettings({ marginPct })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Уровни покупки",
								value: settings.levels,
								min: 3,
								max: 20,
								step: 1,
								onChange: (levels) => setSettings({ levels })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Уровни продажи",
								value: settings.sellLevels,
								min: 1,
								max: 12,
								step: 1,
								onChange: (sellLevels) => setSettings({ sellLevels })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Стоп просадки, %",
								value: settings.maxDdPct,
								min: 1,
								max: 30,
								step: .5,
								onChange: (maxDdPct) => setSettings({ maxDdPct })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Макс. цена монеты",
								value: settings.maxPrice,
								min: 1e-4,
								max: 1e5,
								step: .1,
								onChange: (maxPrice) => setSettings({ maxPrice })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Шаг сетки, % (0 = авто)",
								value: settings.stepPct,
								min: 0,
								max: 5,
								step: .1,
								onChange: (stepPct) => setSettings({ stepPct })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Коридор, %",
								value: settings.rangePct,
								min: 3,
								max: 40,
								step: .5,
								onChange: (rangePct) => setSettings({ rangePct })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Геометрия шага",
								value: settings.geo,
								min: 1,
								max: 1.5,
								step: .01,
								onChange: (geo) => setSettings({ geo })
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Num, {
								label: "Объём × на уровень",
								value: settings.volumeK,
								min: 1,
								max: 2,
								step: .05,
								onChange: (volumeK) => setSettings({ volumeK })
							})
						]
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "mt-3 text-sm text-muted",
						children: "Уровни укладываются в коридор. Геометрия больше 1 растягивает дальние ордера, объём растёт от ближнего уровня к дальнему."
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "mt-5 flex flex-wrap gap-2",
						children: [
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								onClick: () => void onApply(),
								className: "min-h-11 rounded-md border border-line px-4 text-sm",
								children: "Применить"
							}),
							running ? null : /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								onClick: () => void engineCommand({ data: { type: "start" } }).then(ingestServer),
								className: "min-h-11 rounded-md bg-accent px-4 text-sm font-medium text-accent-fg",
								children: "Запустить сервер"
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								disabled: !running && position <= 0,
								onClick: () => void engineCommand({ data: { type: "stop" } }).then(ingestServer),
								className: "min-h-11 rounded-md bg-down px-4 text-sm font-medium text-bg disabled:opacity-40",
								children: "Стоп"
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								disabled: busy !== null,
								onClick: () => void onQuickBuy(),
								className: "min-h-11 rounded-md border border-accent px-4 text-sm font-medium text-accent disabled:opacity-40",
								children: busy === "buy" ? "Покупка…" : "Быстрая покупка"
							}),
							/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								onClick: () => void engineCommand({ data: { type: "reset" } }).then(ingestServer),
								className: "min-h-11 rounded-md border border-line px-4 text-sm",
								children: "Сбросить счёт"
							})
						]
					})
				]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "mt-4 grid gap-4 lg:grid-cols-2",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("article", {
					className: "rounded-md border border-line bg-surface p-4 sm:p-5",
					children: [
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "mb-3 flex items-center gap-2",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Shield, { className: "size-4 text-accent" }), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
								className: "text-base font-medium",
								children: "Безопасность"
							})]
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("ul", {
							className: "space-y-2 text-sm leading-6 text-muted",
							children: [
								/* @__PURE__ */ (0, import_jsx_runtime.jsx)("li", { children: "Ключи живут только до проверки и сразу стираются. В память браузера и на сервер они не пишутся." }),
								/* @__PURE__ */ (0, import_jsx_runtime.jsx)("li", { children: "Вывод, перевод и внутренний трансфер с MEXC в роботе отсутствуют." }),
								/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { children: [
									"Самонастроек: ",
									lessons,
									". Слабый винрейт снижает плечо и расширяет коридор."
								] }),
								/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", { children: [
									"Белый IP для ключа MEXC: ",
									MEXC_WHITELIST_IP,
									". Добавьте его в настройках API на сайте биржи."
								] }),
								/* @__PURE__ */ (0, import_jsx_runtime.jsx)("li", { children: "Цикл стартует вместе с процессом сервера, вкладка не нужна. На VPS его держит служба koridor." })
							]
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
							type: "button",
							onClick: () => void engineCommand({ data: { type: running ? "stop" : "start" } }).then(ingestServer),
							className: "mt-4 min-h-11 rounded-md border border-line px-4 text-sm",
							children: running ? "Сервер: торгует" : "Сервер: пауза"
						})
					]
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("article", {
					className: "rounded-md border border-line bg-surface p-4 sm:p-5",
					children: [
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
							className: "text-base font-medium",
							children: "Аренда · 5% с прибыли"
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
							className: "mt-2 text-sm leading-6 text-muted",
							children: "Арендатор торгует своим ключом. Плата считается с плюсовой прибыли и не списывается с биржи."
						}),
						/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "mt-3 flex flex-wrap gap-2",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								onClick: () => void engineCommand({ data: {
									type: "role",
									role: "owner"
								} }).then(ingestServer),
								className: `min-h-11 rounded-md px-4 text-sm ${role === "owner" ? "bg-accent text-accent-fg" : "border border-line"}`,
								children: "Владелец"
							}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								onClick: () => void engineCommand({ data: {
									type: "role",
									role: "renter"
								} }).then(ingestServer),
								className: `min-h-11 rounded-md px-4 text-sm ${role === "renter" ? "bg-accent text-accent-fg" : "border border-line"}`,
								children: "Арендатор"
							})]
						}),
						role === "renter" ? /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
							className: "mt-3 flex flex-col gap-2 sm:flex-row",
							children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("input", {
								value: renterName,
								placeholder: "Имя аренды",
								onChange: (event) => setRenterName(event.target.value),
								className: "min-h-11 flex-1 rounded-md border border-line bg-bg px-3 text-sm text-fg outline-none"
							}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("button", {
								type: "button",
								onClick: () => void engineCommand({ data: {
									type: "rent",
									name: renterName
								} }).then(ingestServer),
								className: "min-h-11 rounded-md bg-accent px-4 text-sm font-medium text-accent-fg",
								children: "Начать аренду"
							})]
						}) : null,
						/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
							className: "mt-3 text-sm text-fg",
							children: activeRenter ? `Сейчас: ${activeRenter}` : "Своя торговля, без арендной платы"
						}),
						renters.length > 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("ul", {
							className: "mt-3 space-y-1 font-mono text-xs text-muted",
							children: renters.map((renter) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", {
								className: "flex justify-between gap-3",
								children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
									className: "text-fg",
									children: renter.name
								}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("span", { children: [
									"прибыль ",
									renter.realized.toFixed(2),
									" · плата ",
									renter.fee.toFixed(2)
								] })]
							}, renter.name))
						}) : null
					]
				})]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "mt-4 grid gap-3 sm:grid-cols-4",
				children: [
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Metric, {
						label: "Капитал",
						value: `${equity.toFixed(2)} USDT`,
						hint: `${delta >= 0 ? "+" : ""}${delta.toFixed(2)}`
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Metric, {
						label: "Пара",
						value: symbol ? symbol.replace("_", "/") : "—",
						hint: price ? price.toPrecision(5) : "ждём тик"
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Metric, {
						label: "Сделки",
						value: String(trades),
						hint: `винрейт ${winrate}%`
					}),
					/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Metric, {
						label: "Позиция",
						value: position > 0 ? "лонг" : "пусто",
						hint: `${levels.filter((level) => !level.filled).length} открытых уровней`
					})
				]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "mt-4 grid gap-4 lg:grid-cols-[1.4fr_0.8fr]",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("article", {
					className: "rounded-md border border-line bg-surface p-4",
					children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "mb-2 flex items-center gap-2",
						children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Activity, { className: "size-4 text-accent" }), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
							className: "text-base font-medium",
							children: "Бумажный капитал"
						})]
					}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", {
						className: "h-56",
						children: chartOn ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)(EquityChart, { data: series }) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "h-full rounded-md bg-raised" })
					})]
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("article", {
					className: "rounded-md border border-line bg-surface p-4",
					children: [/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("div", {
						className: "mb-3 flex items-center gap-2",
						children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)(Radio, { className: "size-4 text-accent" }), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
							className: "text-base font-medium",
							children: "Уровни сетки"
						})]
					}), levels.length === 0 ? /* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
						className: "text-sm text-muted",
						children: "После запуска здесь появятся лимитные уровни вокруг цены."
					}) : /* @__PURE__ */ (0, import_jsx_runtime.jsx)("ul", {
						className: "max-h-56 space-y-1.5 overflow-auto font-mono text-xs",
						children: [...levels].sort((a, b) => b.price - a.price).map((level) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", {
							className: "flex items-center justify-between gap-3",
							children: [
								/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
									className: level.side === "buy" ? "text-up" : "text-down",
									children: level.side === "buy" ? "BUY" : "SELL"
								}),
								/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
									className: "text-fg",
									children: level.price.toPrecision(5)
								}),
								/* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
									className: "text-muted",
									children: level.filled ? "исполнен" : "ждёт"
								})
							]
						}, `${level.side}-${level.price}`))
					})]
				})]
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsxs)("section", {
				className: "mt-4 rounded-md border border-line bg-surface p-4",
				children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("h2", {
					className: "text-base font-medium",
					children: "Журнал"
				}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("ul", {
					className: "mt-3 max-h-72 space-y-2 overflow-auto",
					children: logs.map((line) => /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("li", {
						className: "flex gap-3 text-sm",
						children: [/* @__PURE__ */ (0, import_jsx_runtime.jsx)("time", {
							className: "shrink-0 font-mono text-xs text-muted",
							children: line.ts ? new Date(line.ts).toLocaleTimeString("ru-RU", {
								hour: "2-digit",
								minute: "2-digit",
								second: "2-digit"
							}) : "—"
						}), /* @__PURE__ */ (0, import_jsx_runtime.jsx)("span", {
							className: line.tone === "ok" ? "text-up" : line.tone === "err" ? "text-down" : line.tone === "warn" ? "text-accent" : "text-fg",
							children: line.text
						})]
					}, line.local ? `local-${line.id}` : `server-${line.ts}-${line.id}`))
				})]
			})
		]
	});
}
function Num({ label, value, min, max, step, onChange }) {
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("label", {
		className: "block text-sm text-muted",
		children: [label, /* @__PURE__ */ (0, import_jsx_runtime.jsx)("input", {
			type: "number",
			inputMode: "decimal",
			min,
			max,
			step,
			value: Number.isFinite(value) ? value : min,
			onChange: (event) => {
				const next = Number(event.target.value);
				if (!Number.isFinite(next)) return;
				onChange(Math.min(max, Math.max(min, next)));
			},
			className: "mt-1.5 w-full rounded-md border border-line bg-bg px-3 py-3 font-mono text-sm text-fg outline-none"
		})]
	});
}
function Metric({ label, value, hint }) {
	return /* @__PURE__ */ (0, import_jsx_runtime.jsxs)("article", {
		className: "rounded-md border border-line bg-raised px-4 py-3",
		children: [
			/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
				className: "text-xs tracking-wide text-muted",
				children: label
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
				className: "mt-1 truncate font-mono text-lg",
				children: value
			}),
			/* @__PURE__ */ (0, import_jsx_runtime.jsx)("p", {
				className: "truncate text-xs text-muted",
				children: hint
			})
		]
	});
}
function Home() {
	return /* @__PURE__ */ (0, import_jsx_runtime.jsx)(Desk, {});
}
//#endregion
export { Home as component };
