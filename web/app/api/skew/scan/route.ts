import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    const tier = body.tier ?? "core";
    const force = body.force ?? true;

    const upstream = await backendFetch(`/skew/scan?tier=${encodeURIComponent(tier)}&force=${force}`, {
      method: "POST",
    });
    const resBody = await upstream.json().catch(() => ({
      detail: "invalid response from backend",
    }));
    return NextResponse.json(resBody, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
