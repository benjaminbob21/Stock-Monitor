"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  SkewChangeView,
  SkewLatestResponse,
  SkewQuadrant,
  SkewRecordView,
  SkewSectorSummary,
} from "@/lib/types";

const QUADRANTS: { id: SkewQuadrant; label: string; sub: string; color: string }[] = [
  {
    id: "Contrarian Bid",
    label: "🎯 Contrarian Bid",
    sub: "Down 1M + Calls Bid (Prime Watchlist)",
    color: "var(--green)",
  },
  {
    id: "Chase",
    label: "⚠️ Chase",
    sub: "Up 1M + Calls Bid (Exhaustion Risk)",
    color: "var(--orange)",
  },
  {
    id: "Hedged Rally",
    label: "📈 Hedged Rally",
    sub: "Up 1M + Puts Bid (Tighten Stops)",
    color: "var(--teal)",
  },
  {
    id: "Fear",
    label: "🛡️ Fear",
    sub: "Down 1M + Puts Bid (Protection Heavy)",
    color: "var(--red)",
  },
];

const QUAD_COLOR: Record<SkewQuadrant, string> = {
  "Contrarian Bid": "var(--green)",
  Chase: "var(--orange)",
  "Hedged Rally": "var(--teal)",
  Fear: "var(--red)",
};

type SubTab = "map" | "table" | "sectors" | "changes";

const fmtPct = (v: number, digits = 1) => `${(v * 100).toFixed(digits)}%`;
const signedPct = (v: number, digits = 1) =>
  `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;

export function SkewMap({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [data, setData] = useState<SkewLatestResponse | null>(null);
  const [changes, setChanges] = useState<SkewChangeView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedQuadrant, setSelectedQuadrant] = useState<SkewQuadrant | "ALL">("ALL");
  const [selectedSector, setSelectedSector] = useState<string>("ALL");
  const [activeTab, setActiveTab] = useState<SubTab>("map");
  const [scanBusy, setScanBusy] = useState(false);
  const [scanMsg, setScanMsg] = useState<string | null>(null);

  const fetchSkewData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [latestRes, changesRes] = await Promise.all([
        fetch("/api/skew/latest"),
        fetch("/api/skew/changes?days=7"),
      ]);

      if (!latestRes.ok) {
        throw new Error(`Failed to fetch skew map (${latestRes.status})`);
      }

      const latestData: SkewLatestResponse = await latestRes.json();
      setData(latestData);

      if (changesRes.ok) {
        const changesData = await changesRes.json();
        setChanges(changesData.changes ?? []);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load skew data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSkewData();
  }, []);

  const triggerScan = async () => {
    setScanBusy(true);
    setScanMsg("Options skew scan running in background...");
    try {
      const res = await fetch("/api/skew/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier: "core", force: true }),
      });
      if (!res.ok) throw new Error("Failed to trigger scan");
      setTimeout(() => {
        fetchSkewData();
        setScanBusy(false);
        setScanMsg("Scan completed! Fresh options skew data loaded.");
      }, 4000);
    } catch (err: unknown) {
      setScanBusy(false);
      setScanMsg(`Scan failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const records = useMemo(() => data?.records ?? [], [data]);
  const sectors = useMemo(() => data?.sectors ?? [], [data]);

  const filteredRecords = useMemo(
    () =>
      records.filter((r) => {
        if (selectedQuadrant !== "ALL" && r.quadrant !== selectedQuadrant) return false;
        if (selectedSector !== "ALL" && r.sector !== selectedSector) return false;
        return true;
      }),
    [records, selectedQuadrant, selectedSector],
  );

  if (loading && !data) {
    return (
      <div className="skew-state">
        <div className="skew-spinner" />
        <div>Loading Options Skew Map…</div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="skew-state">
        <div className="skew-error">Error: {error}</div>
        <button
          type="button"
          className="refresh-btn"
          onClick={triggerScan}
          disabled={scanBusy}
        >
          {scanBusy ? "Running Scan…" : "⚡ Run First Skew Scan"}
        </button>
      </div>
    );
  }

  const counts = data?.counts ?? {
    "Contrarian Bid": 0,
    Chase: 0,
    "Hedged Rally": 0,
    Fear: 0,
  };

  return (
    <div className="skew">
      {/* Header & controls */}
      <div className="skew-head">
        <div className="skew-head-info">
          <h2>
            <span>Options Skew Map</span>
            <span className="skew-asof">{data?.date ? `As of ${data.date}` : "No date"}</span>
          </h2>
          <p>OTM Put IV vs Call IV (25-Delta) mapped against 1-Month Return.</p>
          <p className="skew-desktop-hint">💡 This view is best on a larger screen (desktop/monitor).</p>
        </div>
        <button
          type="button"
          className="refresh-btn"
          onClick={triggerScan}
          disabled={scanBusy}
        >
          {scanBusy ? "Scanning…" : "⚡ Run Skew Scan"}
        </button>
      </div>

      {scanMsg && (
        <div className="skew-scanmsg">
          <span>{scanMsg}</span>
          <button type="button" onClick={() => setScanMsg(null)} aria-label="Dismiss">
            ✕
          </button>
        </div>
      )}

      {/* Quadrant quick-filter cards */}
      <div className="skew-quads">
        {QUADRANTS.map((q) => {
          const selected = selectedQuadrant === q.id;
          return (
            <button
              key={q.id}
              type="button"
              className={`skew-quad ${selected ? "selected" : ""}`}
              style={{ "--q-color": q.color } as React.CSSProperties}
              onClick={() => setSelectedQuadrant(selected ? "ALL" : q.id)}
            >
              <div className="skew-quad-top">
                <span className="skew-quad-name">{q.label}</span>
                <span className="skew-quad-count">{counts[q.id] ?? 0}</span>
              </div>
              <p className="skew-quad-sub">{q.sub}</p>
            </button>
          );
        })}
      </div>

      {/* Sub-tabs */}
      <div className="skew-tabs" role="tablist">
        {(
          [
            ["map", "🗺️ 2D Quadrant Map"],
            ["table", `📋 Watchlist (${filteredRecords.length})`],
            ["sectors", `🌐 Sectors (${sectors.length})`],
            ["changes", `⚡ WoW Shifts (${changes.length})`],
          ] as [SubTab, string][]
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={activeTab === id}
            className={`skew-tab ${activeTab === id ? "active" : ""}`}
            onClick={() => setActiveTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* 1. 2D QUADRANT MAP */}
      {activeTab === "map" && (
        <div className="skew-plot">
          <div className="skew-plot-label tl" style={{ "--q-color": "var(--red)" } as React.CSSProperties}>
            🛡️ FEAR (Down + Puts Bid)
          </div>
          <div className="skew-plot-label tr" style={{ "--q-color": "var(--teal)" } as React.CSSProperties}>
            📈 HEDGED RALLY (Up + Puts Bid)
          </div>
          <div className="skew-plot-label bl" style={{ "--q-color": "var(--green)" } as React.CSSProperties}>
            🎯 CONTRARIAN BID (Down + Calls Bid)
          </div>
          <div className="skew-plot-label br" style={{ "--q-color": "var(--orange)" } as React.CSSProperties}>
            ⚠️ CHASE (Up + Calls Bid)
          </div>

          <div className="skew-axis-v" />
          <div className="skew-axis-h" />

          <div className="skew-axis-note" style={{ top: "calc(50% - 18px)", right: 10 }}>
            + 1M Return →
          </div>
          <div className="skew-axis-note" style={{ top: "calc(50% - 18px)", left: 10 }}>
            ← − 1M Return
          </div>
          <div className="skew-axis-note" style={{ top: 12, left: "calc(50% + 8px)" }}>
            ↑ Puts Bid (Skew &gt; 0)
          </div>
          <div className="skew-axis-note" style={{ bottom: 12, left: "calc(50% + 8px)" }}>
            ↓ Calls Bid (Skew &lt; 0)
          </div>

          {filteredRecords.map((r) => {
            // Map ret_1m from [-0.25, +0.25] to [5%, 95%]
            const clampX = Math.max(-0.25, Math.min(0.25, r.ret_1m));
            const leftPct = ((clampX + 0.25) / 0.5) * 90 + 5;

            // Map normalized_skew from [-0.5, +0.5] to [95%, 5%] (inverted Y)
            const clampY = Math.max(-0.5, Math.min(0.5, r.normalized_skew));
            const topPct = ((0.5 - clampY) / 1.0) * 90 + 5;

            return (
              <button
                key={r.ticker}
                type="button"
                className={`skew-dot ${topPct > 70 ? "flip-tip" : ""}`}
                style={
                  {
                    left: `${leftPct}%`,
                    top: `${topPct}%`,
                    "--q-color": QUAD_COLOR[r.quadrant],
                  } as React.CSSProperties
                }
                onClick={() => onSelectTicker(r.ticker)}
                aria-label={`${r.ticker} — ${r.quadrant}`}
              >
                <span className="skew-dot-core" />
                <span className="skew-dot-ticker">{r.ticker}</span>
                <span className="skew-tip">
                  <span className="skew-tip-head">
                    <span>{r.ticker}</span>
                    <span>${r.spot.toFixed(2)}</span>
                  </span>
                  <span className="skew-tip-row">
                    1M Return:{" "}
                    <span className={r.ret_1m >= 0 ? "up" : "down"}>{fmtPct(r.ret_1m)}</span>
                  </span>
                  <span className="skew-tip-row">
                    Norm Skew: <span className="mono">{r.normalized_skew.toFixed(2)}</span>
                  </span>
                  <span className="skew-tip-row">Quadrant: {r.quadrant}</span>
                  <span className="skew-tip-row">ATM IV: {fmtPct(r.atm_iv)}</span>
                </span>
              </button>
            );
          })}
          {filteredRecords.length === 0 && (
            <div className="skew-empty">No tickers match the current filters.</div>
          )}
        </div>
      )}

      {/* 2. WATCHLIST TABLE */}
      {activeTab === "table" && (
        <>
          <div className="skew-filters">
            <select
              value={selectedSector}
              onChange={(e) => setSelectedSector(e.target.value)}
              className="skew-select"
              aria-label="Filter by sector"
            >
              <option value="ALL">All Sectors</option>
              {sectors.map((s) => (
                <option key={s.sector} value={s.sector}>
                  {s.sector}
                </option>
              ))}
            </select>
          </div>

          <div className="skew-tablewrap">
            <table className="skew-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Sector</th>
                  <th>Spot</th>
                  <th>1M Ret</th>
                  <th>vs SPY</th>
                  <th>ATM IV</th>
                  <th>Norm Skew</th>
                  <th>Quadrant</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {filteredRecords.map((r) => (
                  <tr
                    key={r.ticker}
                    className="rowlink"
                    onClick={() => onSelectTicker(r.ticker)}
                  >
                    <td>
                      <span className="skew-tk">
                        {r.ticker}
                        {r.is_earnings_near && (
                          <span className="skew-er" title={`Earnings on ${r.earnings_date}`}>
                            ER
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="skew-sec">{r.sector}</td>
                    <td className="skew-mono">${r.spot.toFixed(2)}</td>
                    <td className={`skew-mono ${r.ret_1m >= 0 ? "skew-up" : "skew-down"}`}>
                      {fmtPct(r.ret_1m)}
                    </td>
                    <td className={`skew-mono ${r.rel_ret_spy >= 0 ? "skew-up" : "skew-down"}`}>
                      {signedPct(r.rel_ret_spy)}
                    </td>
                    <td className="skew-mono">{fmtPct(r.atm_iv)}</td>
                    <td className="skew-mono skew-teal">{r.normalized_skew.toFixed(2)}</td>
                    <td>
                      <span
                        className="skew-badge"
                        style={{ "--q-color": QUAD_COLOR[r.quadrant] } as React.CSSProperties}
                      >
                        {r.quadrant}
                      </span>
                    </td>
                    <td className="skew-verdict" title={r.verdict}>
                      {r.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* 3. SECTOR AGREEMENT TABLE */}
      {activeTab === "sectors" && (
        <>
          <p className="skew-note">
            PDF Trap #2: Use raw vol points when comparing across sectors. Trap #3: Verify
            sector agreement before trusting a sector signal.
          </p>
          <div className="skew-tablewrap">
            <table className="skew-table">
              <thead>
                <tr>
                  <th>Sector</th>
                  <th>Tickers</th>
                  <th>Avg 1M Ret</th>
                  <th>Raw Skew (Pts)</th>
                  <th>Norm Skew</th>
                  <th>Dominant Lean</th>
                  <th>Agreement</th>
                </tr>
              </thead>
              <tbody>
                {sectors.map((s) => (
                  <tr key={s.sector}>
                    <td className="skew-tk">{s.sector}</td>
                    <td className="skew-sec">{s.ticker_count}</td>
                    <td className={`skew-mono ${s.avg_ret_1m >= 0 ? "skew-up" : "skew-down"}`}>
                      {fmtPct(s.avg_ret_1m)}
                    </td>
                    <td className="skew-mono">{(s.avg_raw_skew * 100).toFixed(1)} pts</td>
                    <td className="skew-mono skew-teal">{s.avg_norm_skew.toFixed(2)}</td>
                    <td className="skew-sec">{s.dominant_lean}</td>
                    <td>
                      <AgreementCell summary={s} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* 4. WEEK-OVER-WEEK SHIFTS */}
      {activeTab === "changes" && (
        <>
          <p className="skew-note">
            PDF Principle: &quot;The level is structural; the change is the signal.&quot; Tracking
            WoW skew expansion/contraction and quadrant flips.
          </p>
          <div className="skew-tablewrap">
            <table className="skew-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Current Skew</th>
                  <th>Prev Skew</th>
                  <th>Skew Delta</th>
                  <th>Quadrant Shift</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {changes.map((c) => (
                  <tr key={c.ticker} className="rowlink" onClick={() => onSelectTicker(c.ticker)}>
                    <td className="skew-tk">{c.ticker}</td>
                    <td className="skew-mono skew-teal">{c.current_norm_skew.toFixed(2)}</td>
                    <td className="skew-mono skew-sec">{c.prev_norm_skew.toFixed(2)}</td>
                    <td
                      className={`skew-mono ${c.skew_change_norm < 0 ? "skew-up" : "skew-down"}`}
                    >
                      {c.skew_change_norm > 0 ? "+" : ""}
                      {c.skew_change_norm.toFixed(2)}
                    </td>
                    <td>
                      {c.quadrant_changed ? (
                        <span className="skew-shift">
                          <span className="was">{c.prev_quadrant}</span>
                          <span className="arrow">→</span>
                          <span className="now">{c.current_quadrant}</span>
                        </span>
                      ) : (
                        <span className="skew-steady">{c.current_quadrant} (steady)</span>
                      )}
                    </td>
                    <td className="skew-verdict" title={c.verdict}>
                      {c.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function AgreementCell({ summary: s }: { summary: SkewSectorSummary }) {
  const color =
    s.agreement >= 0.75 ? "var(--green)" : s.agreement >= 0.5 ? "var(--orange)" : "var(--red)";
  return (
    <div className="skew-agree">
      <div className="skew-agree-track">
        <div
          className="skew-agree-fill"
          style={{ width: `${s.agreement * 100}%`, background: color }}
        />
      </div>
      <span className="skew-mono">{(s.agreement * 100).toFixed(0)}%</span>
    </div>
  );
}
