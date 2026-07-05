// Shared config for the server-side proxies that talk to the FastAPI backend.
// Keeps the backend URL + shared secret off the client and out of every route.
//
// STOCK_MONITOR_API_URL may be a single URL OR a comma-separated list of URLs
// (e.g. two laptops running the backend). backendFetch() tries them in order and
// fails over to the next when one is unreachable — so Vercel automatically reaches
// whichever machine is currently running. Run only ONE backend at a time: it is a
// single DuckDB owner, so two live schedulers would duplicate alerts/data.

/** Parsed list of backend base URLs (trailing slashes stripped). */
export function backendBases(): string[] {
  const raw = process.env.STOCK_MONITOR_API_URL ?? "http://127.0.0.1:8137";
  return raw
    .split(",")
    .map((s) => s.trim().replace(/\/+$/, ""))
    .filter(Boolean);
}

// First configured base — used in "backend unreachable" messages.
export const API_URL = backendBases()[0] ?? "http://127.0.0.1:8137";

// Forwarded to the backend as `X-API-Key` when a shared secret is configured.
// Empty locally (backend auth disabled); set on Vercel to match the backend's
// API_SHARED_SECRET when it is exposed publicly.
export function backendHeaders(): Record<string, string> {
  const key = process.env.STOCK_MONITOR_API_KEY;
  return key ? { "x-api-key": key } : {};
}

/**
 * Fetch `path` from the backend, trying each configured base URL in order.
 * Falls over to the next base on a network error or a 5xx response, so a
 * Vercel request reaches whichever laptop is currently up. Throws only when
 * every base fails (the caller's catch turns that into a 502).
 */
export async function backendFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const bases = backendBases();
  let lastError: unknown;
  for (const base of bases) {
    try {
      const res = await fetch(`${base}${path}`, {
        cache: "no-store",
        ...init,
        headers: { ...backendHeaders(), ...(init?.headers ?? {}) },
      });
      // A 5xx likely means that machine is unhealthy — try the next one.
      if (res.status >= 500 && bases.length > 1) {
        lastError = new Error(`backend ${base} returned ${res.status}`);
        continue;
      }
      return res;
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError ?? new Error("no backend URL configured");
}
