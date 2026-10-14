import { ensureEngine } from "../../src/lib/engine.server";

ensureEngine();

export default function engineBootMiddleware(_event: unknown, next: () => unknown) {
  return next();
}
