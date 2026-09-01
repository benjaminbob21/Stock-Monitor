import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const { searchParams } = new URL(request.url);
  const shares = searchParams.get("shares");
  const dollars = searchParams.get("dollars");
  const note = searchParams.get("note");
  if ((shares == null || shares === "") === (dollars == null || dollars === "")) {
    return NextResponse.json(
      { detail: "provide exactly one of shares or dollars" },
      { status: 400 },
    );
  }
  const qs = new URLSearchParams();
  if (shares) qs.set("shares", shares);
  if (dollars) qs.set("dollars", dollars);
  if (note) qs.set("note", note);
  try {
    const upstream = await backendFetch(
      `/baskets/legs/${encodeURIComponent(id)}/buy?${qs.toString()}`,
      { method: "POST" },
    );
    const body = await upstream.json().catch(() => ({ detail: "bad response" }));
    return NextResponse.json(body, { status: upstream.status });
  } catch {
    return NextResponse.json(
      { detail: `backend unreachable — is the API running at ${API_URL}?` },
      { status: 502 },
    );
  }
}
