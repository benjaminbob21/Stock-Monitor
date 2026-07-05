import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

// POST /api/scan -> trigger a fresh universe scan on the backend (in-process,
// so DuckDB stays single-owner). Returns immediately with {status: "started"|...}.
export async function POST() {
  try {
    const upstream = await backendFetch("/scan", { method: "POST" });
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

// GET /api/scan -> poll scan status ({running, last_finished, last_count, ...}).
export async function GET() {
  try {
    const upstream = await backendFetch("/scan/status");
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
