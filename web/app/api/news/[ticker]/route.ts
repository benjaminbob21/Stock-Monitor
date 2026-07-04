import { NextResponse } from "next/server";

const API_URL = process.env.STOCK_MONITOR_API_URL ?? "http://127.0.0.1:8137";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;
  try {
    const upstream = await fetch(
      `${API_URL}/news/${encodeURIComponent(ticker)}`,
      { cache: "no-store" },
    );
    const body = await upstream.json().catch(() => ({ items: [] }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
