import { NextResponse } from "next/server";

import { API_URL, backendHeaders } from "@/lib/backend";

export async function GET() {
  try {
    const upstream = await fetch(`${API_URL}/positions`, {
      cache: "no-store",
      headers: backendHeaders(),
    });
    const body = await upstream.json().catch(() => ({ positions: [] }));
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
  const ticker = searchParams.get("ticker");
  if (!ticker) {
    return NextResponse.json({ detail: "ticker is required" }, { status: 400 });
  }
  try {
    const upstream = await fetch(
      `${API_URL}/positions/${encodeURIComponent(ticker)}`,
      { method: "POST", cache: "no-store", headers: backendHeaders() },
    );
    const body = await upstream
      .json()
      .catch(() => ({ detail: "bad response" }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
