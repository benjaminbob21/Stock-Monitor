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

export function DcfCard({ ticker }: { ticker: string }) {
  const [dcf, setDcf] = useState<DcfResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);

  const load = useCallback(async (override?: { growth: string; wacc: string }) => {
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
  }, [ticker]);

  useEffect(() => {
    if (!ticker) return;
    void load();
  }, [ticker, load]);

  const [growth, setGrowth] = useState("");
  const [wacc, setWacc] = useState("");

  return (
    <div className="card dcfcard">
      <button
        type="button"
        className="dcfhead"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="dcftitle">Intrinsic value (DCF)</span>
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
              Market {fmtMoney(dcf.price)} vs DCF {fmtMoney(dcf.value)} ·{" "}
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
                <p className="dcfmeta">
                  FCF (last FY{dcf.inputs.fcf_years ? `, ${dcf.inputs.fcf_years}` : ""}):{" "}
                  ${fmtBn(dcf.inputs.base_fcf)} · growth:{" "}
                  {dcf.inputs.growth_pct != null
                    ? `${(dcf.inputs.growth_pct * 100).toFixed(1)}%`
                    : "—"}{" "}
                  ({dcf.inputs.growth_source ?? "n/a"}) · WACC:{" "}
                  {dcf.inputs.wacc_pct != null
                    ? `${(dcf.inputs.wacc_pct * 100).toFixed(1)}%`
                    : "—"}{" "}
                  · terminal:{" "}
                  {dcf.inputs.terminal_growth_pct != null
                    ? `${(dcf.inputs.terminal_growth_pct * 100).toFixed(1)}%`
                    : "—"}
                </p>
              )}
              {dcf.inputs.net_debt != null && (
                <p className="dcfmeta">
                  Net debt: ${fmtBn(dcf.inputs.net_debt)} ({dcf.inputs.bridge})
                </p>
              )}
              {dcf.pv_explicit != null && dcf.pv_terminal != null && (
                <p className="dcfmeta">
                  PV 5-yr FCF: ${fmtBn(dcf.pv_explicit)} + PV terminal: $
                  {fmtBn(dcf.pv_terminal)}
                  {dcf.terminal_weight != null
                    ? ` (terminal is ${(dcf.terminal_weight * 100).toFixed(0)}% of value)`
                    : ""}
                </p>
              )}
              {dcf.reasons.map((r) => (
                <p className="dcfmeta warn" key={r}>
                  ⚠ {r}
                </p>
              ))}

              <form
                className="dcfform"
                onSubmit={(e) => {
                  e.preventDefault();
                  void load(
                    growth.trim() || wacc.trim()
                      ? { growth: growth.trim() || "0.10", wacc: wacc.trim() || "0.085" }
                      : undefined,
                  );
                }}
              >
                <label>
                  growth
                  <input
                    value={growth}
                    onChange={(e) => setGrowth(e.target.value)}
                    placeholder="e.g. 0.12"
                    inputMode="decimal"
                  />
                </label>
                <label>
                  wacc
                  <input
                    value={wacc}
                    onChange={(e) => setWacc(e.target.value)}
                    placeholder="e.g. 0.085"
                    inputMode="decimal"
                  />
                </label>
                <button type="submit" disabled={loading}>
                  {loading ? "…" : "Re-run"}
                </button>
              </form>
              <p className="dcfmeta muted">
                Growth is the annual FCF growth assumption (blank = auto: revenue
                CAGR, clamped −10%…30%). Leave both blank to use defaults.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
