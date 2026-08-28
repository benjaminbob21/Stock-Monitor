"use client";

import { useCallback, useEffect, useState } from "react";

interface Alloc {
  ticker: string;
  target_pct: number;
  current_pct: number;
  delta_pct: number;
  conviction: number;
  reasons: string[];
}

interface Plan {
  as_of: string;
  total_value: number;
  allocations: Alloc[];
  cash_pct: number;
  warnings: string[];
  diagnostics?: { book_value?: number; price_errors?: Record<string, string> };
  detail?: string;
}

function money(x: number): string {
  return `$${x.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

/**
 * "How should my capital be split?" — the deterministic engine's target weights
 * for the whole book (or a hypothetical budget), with per-position reasons.
 */
export function AllocationPanel({ budgetHint }: { budgetHint?: number }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/allocation");
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? "allocation failed");
      setPlan(body as Plan);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not reach the scoring service");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, budgetHint]);

  if (busy && !plan) return <div className="status">Working out a split…</div>;
  if (error)
    return (
      <div className="status" style={{ color: "var(--red)" }}>
        {error}
      </div>
    );
  if (!plan || plan.allocations.length === 0) return null;

  return (
    <div className="allocpanel">
      <button className="allochead" onClick={() => setOpen(!open)} type="button">
        <span>🎯 Suggested capital split</span>
        <span className="opps-meta">
          {open ? "hide" : `${plan.allocations.length} names · ${plan.cash_pct}% cash`}
        </span>
      </button>
      {open && (
        <div className="allocbody">
          <p className="hint">
            Target weights from conviction ÷ volatility, capped at 15% per name, cash
            floor when the book is weak. vs current book of{" "}
            {money(plan.diagnostics?.book_value ?? plan.total_value)}.
          </p>
          {plan.warnings.length > 0 && (
            <p className="hint" style={{ color: "var(--amber, var(--text))" }}>
              {plan.warnings.join(" · ")}
            </p>
          )}
          <div className="reclist">
            {plan.allocations.map((a) => (
              <div key={a.ticker} className="bbleg allocrow">
                <div className="bbleg-id">
                  <strong>{a.ticker}</strong>
                  <span className="poslabel">
                    conviction {Math.round(a.conviction)}
                    {a.reasons.length > 0 ? ` · ${a.reasons[0]}` : ""}
                  </span>
                </div>
                <div className="allocpcts">
                  <span className="posval">{a.target_pct}%</span>
                  {Math.abs(a.delta_pct) >= 0.5 && (
                    <span
                      className="poslabel"
                      style={{
                        color:
                          a.delta_pct > 0 ? "var(--green)" : "var(--red)",
                      }}
                    >
                      {a.delta_pct > 0 ? "+" : ""}
                      {a.delta_pct}%
                    </span>
                  )}
                </div>
              </div>
            ))}
            <div className="bbleg allocrow">
              <div className="bbleg-id">
                <strong>Cash</strong>
                <span className="poslabel">dry powder / safety</span>
              </div>
              <span className="posval">{plan.cash_pct}%</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
