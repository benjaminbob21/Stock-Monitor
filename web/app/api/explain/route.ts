import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

// Proxy for the AI plain-English narrative. Forwards the score payload the client
// already has (drivers + recommendation) so the backend never re-scores.
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    body = {};
  }
  try {
    const upstream = await backendFetch("/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await upstream.json().catch(() => ({
      detail: "invalid response from backend",
    }));
    return NextResponse.json(data, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
