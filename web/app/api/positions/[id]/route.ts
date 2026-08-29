import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const upstream = await backendFetch(`/positions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    const body = await upstream.json().catch(() => ({ detail: "bad response" }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
