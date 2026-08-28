import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;
  const { searchParams } = new URL(request.url);
  const qs = new URLSearchParams();
  if (searchParams.get("growth")) qs.set("growth", searchParams.get("growth")!);
  if (searchParams.get("wacc")) qs.set("wacc", searchParams.get("wacc")!);
  if (searchParams.get("terminal_growth"))
    qs.set("terminal_growth", searchParams.get("terminal_growth")!);
  const query = qs.toString() ? `?${qs.toString()}` : "";
  try {
    const upstream = await backendFetch(
      `/dcf/${encodeURIComponent(ticker.toUpperCase())}${query}`,
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
