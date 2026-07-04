import { NextResponse } from "next/server";

const API_URL = process.env.STOCK_MONITOR_API_URL ?? "http://127.0.0.1:8137";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const upstream = await fetch(
      `${API_URL}/positions/${encodeURIComponent(id)}/sell`,
      { method: "POST", cache: "no-store" },
    );
    const body = await upstream.json().catch(() => ({ detail: "bad response" }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
