import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

// POST /api/news-collect?days=7 -> trigger an on-demand news collect+archive on the
// backend (in-process, so DuckDB stays single-owner). Idempotent: re-runs skip days
// already stored. Returns immediately with {status: "started"|"already_running"|...}.
export async function POST(request: Request) {
  const days = new URL(request.url).searchParams.get("days") ?? "7";
  try {
    const upstream = await backendFetch(
      `/news/collect?days=${encodeURIComponent(days)}`,
      { method: "POST" },
    );
    const body = await upstream
      .json()
      .catch(() => ({ detail: "invalid response from backend" }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}

// GET /api/news-collect -> poll collection status
// ({running, last_finished, last_archived, progress, ...}).
export async function GET() {
  try {
    const upstream = await backendFetch("/news/collect/status");
    const body = await upstream
      .json()
      .catch(() => ({ detail: "invalid response from backend" }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
