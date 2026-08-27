import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function GET() {
  try {
    const upstream = await backendFetch(`/baskets`);
    const body = await upstream.json().catch(() => ({ baskets: [] }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}

export async function POST(request: Request) {
  const { searchParams } = new URL(request.url);
  const name = searchParams.get("name");
  const budget = searchParams.get("budget");
  const tickers = searchParams.get("tickers");
  const pcts = searchParams.get("pcts");

  if (!budget || !tickers || !pcts) {
    return NextResponse.json(
      { detail: "budget, tickers and pcts are required" },
      { status: 400 },
    );
  }

  try {
    const upstream = await backendFetch(
      `/baskets?${new URLSearchParams({
        ...(name ? { name } : {}),
        budget,
        tickers,
        pcts,
      })}`,
      { method: "POST" },
    );
    const body = await upstream.json().catch(() => ({}));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
