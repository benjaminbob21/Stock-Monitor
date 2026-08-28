import { useCallback, useEffect, useState } from "react";

import type { BriefResponse } from "@/lib/types";

function money(v: number) {
  return v.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

/**
 * Daily AI-narrated portfolio brief. The numbers come from the deterministic
 * engine; the LLM only narrates them. One backend call per calendar day —
 * refreshes hit the server-side cache.
 */
export function BriefCard() {
  const [data, setData] = useState<BriefResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetch("/api/brief")
      .then(async (res) => {
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail ?? "brief failed");
        return body as BriefResponse;
      })
      .then((body) => {
        setData(body);
        if (body.brief) setOpen(true);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  if (loading) return <div className="card briefcard">Writing today's brief…</div>;
  if (error) return <div className="card briefcard">Brief unavailable — {error}</div>;
  if (!data) return null;

  const ctx = data.context;
  const top = [...ctx.allocations].slice(0, 4);

  return (
    <div className="card briefcard">
      <p className="analyst-head">
        <span className="analyst-badge">Daily brief</span>
        {data.cached && <span className="analyst-conf">cached today</span>}
        <button className="briefclose" onClick={() => setOpen((v) => !v)}>
          {open ? "hide" : "show"}
        </button>
      </p>

      {data.brief ? (
        <p className="brieftext">{data.brief}</p>
      ) : (
        <p className="analyst-note">{data.note ?? "No narration today."}</p>
      )}

      <p className="briefmeta">
        Book {money(ctx.total_value)} · cash {ctx.cash_pct}% · engine numbers, AI
        narration only
      </p>

      {open && top.length > 0 && (
        <div className="briefrows">
          {top.map((a) => (
            <div className="briefrow" key={a.ticker}>
              <span className="briefticker">{a.ticker}</span>
              <span className="briefpct">
                {a.current_pct}% → {a.target_pct}%
              </span>
              <span
                className="briefdelta"
                style={{
                  color: a.delta_pct >= 0 ? "var(--green)" : "var(--red)",
                }}
              >
                {a.delta_pct >= 0 ? "+" : ""}
                {a.delta_pct}
              </span>
            </div>
          ))}
        </div>
      )}

      {data.model && <p className="analyst-meta">{data.model}</p>}
    </div>
  );
}
