import { NextResponse } from "next/server";

// Server-side proxy to the FastAPI backend. Keeps the backend URL off the client
// and sidesteps CORS entirely (same-origin fetch from the browser).
const API_URL = process.env.STOCK_MONITOR_API_URL ?? "http://127.0.0.1:8137";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;

  try {
    const upstream = await fetch(
      `${API_URL}/score/${encodeURIComponent(ticker)}`,
      { cache: "no-store" },
    );
    const body = await upstream.json().catch(() => ({
      detail: "invalid response from backend",
    }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
