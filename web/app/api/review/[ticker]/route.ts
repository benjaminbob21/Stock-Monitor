import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;
  try {
    const upstream = await backendFetch(`/review/${encodeURIComponent(ticker)}`, {
      method: "POST",
    });
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
