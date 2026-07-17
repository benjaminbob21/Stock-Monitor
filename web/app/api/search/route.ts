import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const q = searchParams.get("q") ?? "";
  const limit = searchParams.get("limit") ?? "15";
  try {
    const upstream = await backendFetch(
      `/search?q=${encodeURIComponent(q)}&limit=${encodeURIComponent(limit)}`,
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
