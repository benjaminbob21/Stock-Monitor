import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const budget = searchParams.get("budget");
  const tickers = searchParams.get("tickers");
  const qs = new URLSearchParams();
  if (budget) qs.set("budget", budget);
  if (tickers) qs.set("tickers", tickers);

  try {
    const upstream = await backendFetch(`/allocation${qs.size ? `?${qs}` : ""}`);
    const body = await upstream.json().catch(() => ({}));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
