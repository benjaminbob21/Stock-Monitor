import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id) {
    return NextResponse.json({ detail: "basket id is required" }, { status: 400 });
  }
  try {
    const upstream = await backendFetch(`/baskets/${encodeURIComponent(id)}/close`, {
      method: "POST",
    });
    const body = await upstream.json().catch(() => ({}));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
