import { a as object, i as number, n as discriminatedUnion, o as string, r as literal, t as _enum } from "../_libs/zod.mjs";
import { t as createServerFn } from "./ssr.mjs";
import { t as createServerRpc } from "./createServerRpc-A6pJPYTF.mjs";
//#region node_modules/.nitro/vite/services/ssr/assets/engine.functions-gx1LL_T2.js
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
async function engine() {
	return import("./engine.server-SdAdnuEr.mjs");
}
var engineSnapshot_createServerFn_handler = createServerRpc({
	id: "9791e6ba83aaf141257154bd5b1c8febde0471d95aac2caf385e60fafd2c1494",
	name: "engineSnapshot",
	filename: "src/lib/engine.functions.ts"
}, (opts) => engineSnapshot.__executeServer(opts));
var engineSnapshot = createServerFn({ method: "GET" }).handler(engineSnapshot_createServerFn_handler, async () => {
	return (await engine()).snapshot();
});
var engineCommand_createServerFn_handler = createServerRpc({
	id: "ebc58a7e621858163b99bdbc0220b0e5e4daf13552a6a0c53a21e03abcdb353a",
	name: "engineCommand",
	filename: "src/lib/engine.functions.ts"
}, (opts) => engineCommand.__executeServer(opts));
var engineCommand = createServerFn({ method: "POST" }).validator(commandSchema).handler(engineCommand_createServerFn_handler, async ({ data }) => {
	const mod = await engine();
	mod.ensureEngine();
	if (data.type === "apply") return mod.configure(data.settings);
	if (data.type === "start") return mod.commandStart();
	if (data.type === "stop") return mod.commandStop();
	if (data.type === "reset") return mod.commandReset();
	if (data.type === "buy") return mod.commandBuy();
	if (data.type === "rent") return mod.commandRent(data.name);
	return mod.commandRole(data.role);
});
//#endregion
export { engineCommand_createServerFn_handler, engineSnapshot_createServerFn_handler };
