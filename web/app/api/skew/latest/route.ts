import { NextResponse } from "next/server";

import { API_URL, backendFetch } from "@/lib/backend";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const quadrant = searchParams.get("quadrant");
  const sector = searchParams.get("sector");

  let path = "/skew/latest";
  const params = new URLSearchParams();
  if (quadrant) params.append("quadrant", quadrant);
  if (sector) params.append("sector", sector);
  const qs = params.toString();
  if (qs) path += `?${qs}`;

  try {
    const upstream = await backendFetch(path);
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
