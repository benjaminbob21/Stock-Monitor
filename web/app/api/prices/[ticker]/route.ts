import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

// Adjusted daily OHLCV bars for the candlestick chart. The backend caches bars
// per (ticker, days) to shield the price provider's free-tier rate limit, so this
// proxy stays a thin passthrough.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;
  const days = new URL(request.url).searchParams.get("days");
  const query = days ? `?days=${encodeURIComponent(days)}` : "";

  try {
    const upstream = await backendFetch(
      `/prices/${encodeURIComponent(ticker)}${query}`,
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
