import { NextResponse } from "next/server";

import { API_URL, backendHeaders } from "@/lib/backend";

export async function GET() {
  try {
    const upstream = await fetch(`${API_URL}/recommendations`, {
      cache: "no-store",
      headers: backendHeaders(),
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
