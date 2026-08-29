import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function GET() {
  try {
    const upstream = await backendFetch(`/positions`);
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
  const quantityRaw = searchParams.get("quantity");
  const quantity = quantityRaw != null && quantityRaw !== "" ? Number(quantityRaw) : 1;
  if (!Number.isFinite(quantity) || quantity <= 0) {
    return NextResponse.json({ detail: "quantity must be positive" }, { status: 400 });
  }
  try {
    const upstream = await backendFetch(
      `/positions/${encodeURIComponent(ticker)}?quantity=${quantity}`,
      { method: "POST" },
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
