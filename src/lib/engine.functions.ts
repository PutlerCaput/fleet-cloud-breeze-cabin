import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { DEFAULT_SETTINGS, type Settings } from "@/lib/desk-store";

const settingsSchema = z.object({
  leverage: z.number().min(1).max(20),
  marginPct: z.number().min(1).max(100),
  levels: z.number().min(3).max(20),
  sellLevels: z.number().min(1).max(12),
  deposit: z.number().min(10).max(1_000_000),
  maxDdPct: z.number().min(1).max(30),
  maxPrice: z.number().min(0.0001).max(100_000),
  stepPct: z.number().min(0).max(5),
  rangePct: z.number().min(3).max(40),
  geo: z.number().min(1).max(1.5),
  volumeK: z.number().min(1).max(2),
});

const commandSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("apply"), settings: settingsSchema }),
  z.object({ type: z.literal("start") }),
  z.object({ type: z.literal("stop") }),
  z.object({ type: z.literal("forget") }),
  z.object({ type: z.literal("buy") }),
  z.object({ type: z.literal("rent"), name: z.string().max(32) }),
  z.object({ type: z.literal("role"), role: z.enum(["owner", "renter"]) }),
  z.object({
    type: z.literal("save"),
    apiKey: z.string().trim().min(8).max(512),
    secret: z.string().trim().min(8).max(512),
  }),
  z.object({
    type: z.literal("arm"),
    apiKey: z.string().trim().min(8).max(512),
    secret: z.string().trim().min(8).max(512),
  }),
  z.object({
    type: z.literal("notify"),
    token: z.string().trim().min(20).max(120),
    chat: z.string().trim().min(1).max(64),
  }),
]);

async function engine() {
  return import("./engine.server");
}

export const engineSnapshot = createServerFn({ method: "GET" }).handler(async () => {
  const mod = await engine();
  return mod.snapshot();
});

export const engineCommand = createServerFn({ method: "POST" })
  .validator(commandSchema)
  .handler(async ({ data }) => {
    const mod = await engine();
    mod.ensureEngine();
    if (data.type === "apply") return mod.configure(data.settings as Settings);
    if (data.type === "start") return mod.commandStart();
    if (data.type === "stop") return mod.commandStop();
    if (data.type === "forget") return mod.commandForgetKeys();
    if (data.type === "buy") return mod.commandBuy();
    if (data.type === "rent") return mod.commandRent(data.name);
    if (data.type === "save") return mod.commandSaveKeys(data.apiKey, data.secret);
    if (data.type === "arm") return mod.commandArm(data.apiKey, data.secret);
    if (data.type === "notify") return mod.commandNotify(data.token, data.chat);
    return mod.commandRole(data.role);
  });

export const ENGINE_DEFAULTS = DEFAULT_SETTINGS;
