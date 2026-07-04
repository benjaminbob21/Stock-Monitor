// Shared config for the server-side proxies that talk to the FastAPI backend.
// Keeps the backend URL + shared secret off the client and out of every route.
export const API_URL =
  process.env.STOCK_MONITOR_API_URL ?? "http://127.0.0.1:8137";

// Forwarded to the backend as `X-API-Key` when a shared secret is configured.
// Empty locally (backend auth disabled); set on Vercel to match the backend's
// API_SHARED_SECRET when it is exposed publicly.
export function backendHeaders(): Record<string, string> {
  const key = process.env.STOCK_MONITOR_API_KEY;
  return key ? { "x-api-key": key } : {};
}
