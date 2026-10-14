export default async function metricsMiddleware(event: { url: URL }, next: () => unknown) {
  if (event.url.pathname !== "/metrics") return next();
  const { metricsText } = await import("../../src/lib/engine.server");
  return new Response(metricsText(), {
    headers: { "content-type": "text/plain; version=0.0.4; charset=utf-8", "cache-control": "no-store" },
  });
}
