import { NextResponse } from "next/server";

const API_URL = process.env.STOCK_MONITOR_API_URL ?? "http://127.0.0.1:8137";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const limit = searchParams.get("limit") ?? "20";
  try {
    const upstream = await fetch(
      `${API_URL}/opportunities?limit=${encodeURIComponent(limit)}`,
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
