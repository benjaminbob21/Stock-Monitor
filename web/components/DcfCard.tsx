import { useCallback, useEffect, useState } from "react";

import type { DcfResponse } from "@/lib/types";

const fmtBn = (n: number) =>
  Math.abs(n) >= 1e12
    ? `${(n / 1e12).toFixed(2)}T`
    : `${(n / 1e9).toFixed(2)}B`;

const fmtMoney = (n: number) =>
  `$${n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

const fmtPct = (n: number) =>
  `${n > 0 ? "+" : ""}${(n * 100).toFixed(1)}%`;

function parsePctInput(input: string, fallback: number): number {
  const cleaned = input.trim().replace("%", "");
  if (!cleaned) return fallback;
  const num = parseFloat(cleaned);
  if (isNaN(num)) return fallback;
  if (Math.abs(num) > 1.0) {
    return num / 100.0;
  }
  return num;
}

export function DcfCard({ ticker }: { ticker: string }) {
  const [dcf, setDcf] = useState<DcfResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);

  const load = useCallback(
    async (override?: { growth: string; wacc: string }) => {
      setLoading(true);
      setError(null);
      try {
        const qs = override
          ? `?growth=${encodeURIComponent(override.growth)}&wacc=${encodeURIComponent(override.wacc)}`
          : "";
        const res = await fetch(`/api/dcf/${encodeURIComponent(ticker)}${qs}`);
        const body = await res.json();
        if (!res.ok) {
          setError(body.detail ?? "valuation unavailable");
          setDcf(null);
        } else {
          setDcf(body as DcfResponse);
        }
      } catch {
        setError("valuation unavailable");
        setDcf(null);
      } finally {
        setLoading(false);
      }
    },
    [ticker],
  );

  useEffect(() => {
    if (!ticker) return;
    void load();
  }, [ticker, load]);

  const [growth, setGrowth] = useState("");
  const [wacc, setWacc] = useState("");

  const handleRerun = (e: React.FormEvent) => {
    e.preventDefault();
    if (!growth.trim() && !wacc.trim()) {
      void load();
      return;
    }
    const defaultGrowth = dcf?.inputs.growth_pct ?? 0.1;
    const defaultWacc = dcf?.inputs.wacc_pct ?? 0.085;
    const gNum = growth.trim() ? parsePctInput(growth, defaultGrowth) : defaultGrowth;
    const wNum = wacc.trim() ? parsePctInput(wacc, defaultWacc) : defaultWacc;
    void load({ growth: gNum.toString(), wacc: wNum.toString() });
  };

  return (
    <div className="card dcfcard">
      <button
        type="button"
        className="dcfhead"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="dcftitle">Intrinsic Value (DCF Model)</span>
        {loading ? (
          <span className="dcfval muted">…</span>
        ) : dcf?.value != null ? (
          <span className="dcfval">{fmtMoney(dcf.value)}</span>
        ) : (
          <span className="dcfval muted">—</span>
        )}
        <svg
          className={`dcfchev ${open ? "open" : ""}`}
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.1}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {error && <p className="dcferror">{error}</p>}

      {!loading && !error && dcf && (
        <>
          {dcf.value != null && dcf.price != null && dcf.upside_pct != null && (
            <p className="dcfline">
              Market <b>{fmtMoney(dcf.price)}</b> vs DCF <b>{fmtMoney(dcf.value)}</b> ·{" "}
              <span
                className={
                  dcf.upside_pct > 0.15
                    ? "dcfup"
                    : dcf.upside_pct < -0.15
                      ? "dcfdown"
                      : "dcfup muted"
                }
              >
                {fmtPct(dcf.upside_pct)}
              </span>{" "}
              {dcf.verdict ? `· ${dcf.verdict}` : ""}
              {dcf.confidence === "rough" ? " · rough inputs" : ""}
            </p>
          )}

          {dcf.value == null && (
            <p className="dcfline muted">
              Not computable
              {dcf.reasons.length > 0 ? ` — ${dcf.reasons[0]}` : ""}.
            </p>
          )}

          {open && (
            <div className="dcfbody">
              {dcf.inputs.base_fcf != null && (
                <div style={{ margin: "6px 0 10px", fontSize: "13px" }}>
                  <p className="dcfmeta">
                    <b>Baseline FCF:</b> ${fmtBn(dcf.inputs.base_fcf)}{" "}
                    <span className="muted">
                      (SEC 10-K {dcf.inputs.fcf_years ?? "history"})
                    </span>
                  </p>
                  <p className="dcfmeta">
                    <b>5-Yr Growth Rate:</b>{" "}
                    {dcf.inputs.growth_pct != null
                      ? `${(dcf.inputs.growth_pct * 100).toFixed(1)}%`
                      : "—"}{" "}
                    <span className="muted">
                      ({dcf.inputs.growth_source ?? "historical revenue CAGR"})
                    </span>
                  </p>
                  <p className="dcfmeta">
                    <b>Discount Rate (WACC):</b>{" "}
                    {dcf.inputs.wacc_pct != null
                      ? `${(dcf.inputs.wacc_pct * 100).toFixed(1)}%`
                      : "8.5%"}{" "}
                    <span className="muted">
                      · Terminal Growth:{" "}
                      {dcf.inputs.terminal_growth_pct != null
                        ? `${(dcf.inputs.terminal_growth_pct * 100).toFixed(1)}%`
                        : "2.5%"}
                    </span>
                  </p>
                  {dcf.inputs.net_debt != null && (
                    <p className="dcfmeta">
                      <b>Net Debt:</b> ${fmtBn(dcf.inputs.net_debt)}{" "}
                      <span className="muted">({dcf.inputs.bridge})</span>
                    </p>
                  )}
                  {dcf.pv_explicit != null && dcf.pv_terminal != null && (
                    <p className="dcfmeta">
                      <b>Valuation Bridge:</b> 5-Yr Flows PV ${fmtBn(dcf.pv_explicit)} +
                      Perpetuity PV ${fmtBn(dcf.pv_terminal)}
                    </p>
                  )}
                </div>
              )}

              {dcf.reasons.map((r) => (
                <p className="dcfmeta warn" key={r}>
                  ⚠ {r}
                </p>
              ))}

              <form className="dcfform" onSubmit={handleRerun}>
                <label>
                  <span>5-Yr FCF Growth</span>
                  <input
                    value={growth}
                    onChange={(e) => setGrowth(e.target.value)}
                    placeholder={
                      dcf.inputs.growth_pct != null
                        ? `${(dcf.inputs.growth_pct * 100).toFixed(1)}%`
                        : "e.g. 12%"
                    }
                    inputMode="decimal"
                  />
                </label>
                <label>
                  <span>Discount Rate (WACC)</span>
                  <input
                    value={wacc}
                    onChange={(e) => setWacc(e.target.value)}
                    placeholder={
                      dcf.inputs.wacc_pct != null
                        ? `${(dcf.inputs.wacc_pct * 100).toFixed(1)}%`
                        : "e.g. 8.5%"
                    }
                    inputMode="decimal"
                  />
                </label>
                <button type="submit" disabled={loading}>
                  {loading ? "…" : "Re-run"}
                </button>
              </form>

              <p className="dcfmeta muted" style={{ marginTop: 8, fontSize: "12px", lineHeight: "1.4" }}>
                💡 <b>Growth</b> = expected annual Free Cash Flow growth rate (e.g. <code>12%</code> or <code>0.12</code>).
                <br />
                💡 <b>WACC</b> = Weighted Average Cost of Capital (discount rate / hurdle rate, e.g. <code>8.5%</code>).
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
