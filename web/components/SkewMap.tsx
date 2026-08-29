"use client";

import { useEffect, useState } from "react";
import type {
  SkewChangeView,
  SkewLatestResponse,
  SkewQuadrant,
  SkewRecordView,
  SkewSectorSummary,
} from "@/lib/types";

export function SkewMap({ onSelectTicker }: { onSelectTicker: (t: string) => void }) {
  const [data, setData] = useState<SkewLatestResponse | null>(null);
  const [changes, setChanges] = useState<SkewChangeView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedQuadrant, setSelectedQuadrant] = useState<SkewQuadrant | "ALL">("ALL");
  const [selectedSector, setSelectedSector] = useState<string>("ALL");
  const [activeTab, setActiveTab] = useState<"map" | "table" | "sectors" | "changes">("map");
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

  if (loading && !data) {
    return (
      <div className="p-6 text-center text-zinc-400">
        <div className="animate-spin inline-block w-8 h-8 border-2 border-current border-t-transparent rounded-full mb-3" />
        <div>Loading Options Skew Map...</div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="p-6 text-center">
        <div className="text-rose-400 font-medium mb-3">Error: {error}</div>
        <button
          onClick={triggerScan}
          disabled={scanBusy}
          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition"
        >
          {scanBusy ? "Running Scan..." : "Run First Skew Scan"}
        </button>
      </div>
    );
  }

  const records = data?.records ?? [];
  const counts = data?.counts ?? {
    "Contrarian Bid": 0,
    Chase: 0,
    "Hedged Rally": 0,
    Fear: 0,
  };

  const filteredRecords = records.filter((r) => {
    if (selectedQuadrant !== "ALL" && r.quadrant !== selectedQuadrant) return false;
    if (selectedSector !== "ALL" && r.sector !== selectedSector) return false;
    return true;
  });

  const sectors = data?.sectors ?? [];

  return (
    <div className="space-y-6 pb-12">
      {/* Header & Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-zinc-900/60 p-4 rounded-xl border border-zinc-800">
        <div>
          <h2 className="text-xl font-bold text-zinc-100 flex items-center gap-2">
            <span>Options Skew Map</span>
            <span className="text-xs font-normal px-2 py-0.5 bg-zinc-800 text-zinc-400 rounded-full">
              {data?.date ? `As of ${data.date}` : "No date"}
            </span>
          </h2>
          <p className="text-xs text-zinc-400 mt-1">
            OTM Put IV vs Call IV (25-Delta) mapped against 1-Month Return.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={triggerScan}
            disabled={scanBusy}
            className="px-3 py-1.5 bg-emerald-600/90 hover:bg-emerald-500 text-white text-xs font-medium rounded-lg transition disabled:opacity-50"
          >
            {scanBusy ? "Scanning..." : "⚡ Run Skew Scan"}
          </button>
        </div>
      </div>

      {scanMsg && (
        <div className="p-3 bg-zinc-800/80 border border-zinc-700 text-zinc-200 text-xs rounded-lg flex items-center justify-between">
          <span>{scanMsg}</span>
          <button onClick={() => setScanMsg(null)} className="text-zinc-400 hover:text-zinc-200">
            ✕
          </button>
        </div>
      )}

      {/* Quadrant Quick-Filter Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Contrarian Bid */}
        <button
          onClick={() => setSelectedQuadrant(selectedQuadrant === "Contrarian Bid" ? "ALL" : "Contrarian Bid")}
          className={`p-3 rounded-xl border text-left transition ${
            selectedQuadrant === "Contrarian Bid"
              ? "bg-emerald-950/40 border-emerald-500 ring-1 ring-emerald-500"
              : "bg-zinc-900/40 border-zinc-800 hover:border-zinc-700"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-emerald-400">🎯 Contrarian Bid</span>
            <span className="text-lg font-bold text-emerald-300">{counts["Contrarian Bid"]}</span>
          </div>
          <p className="text-[11px] text-zinc-400 mt-1">Down 1M + Calls Bid (Prime Watchlist)</p>
        </button>

        {/* Chase */}
        <button
          onClick={() => setSelectedQuadrant(selectedQuadrant === "Chase" ? "ALL" : "Chase")}
          className={`p-3 rounded-xl border text-left transition ${
            selectedQuadrant === "Chase"
              ? "bg-amber-950/40 border-amber-500 ring-1 ring-amber-500"
              : "bg-zinc-900/40 border-zinc-800 hover:border-zinc-700"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-amber-400">⚠️ Chase</span>
            <span className="text-lg font-bold text-amber-300">{counts["Chase"]}</span>
          </div>
          <p className="text-[11px] text-zinc-400 mt-1">Up 1M + Calls Bid (Exhaustion Risk)</p>
        </button>

        {/* Hedged Rally */}
        <button
          onClick={() => setSelectedQuadrant(selectedQuadrant === "Hedged Rally" ? "ALL" : "Hedged Rally")}
          className={`p-3 rounded-xl border text-left transition ${
            selectedQuadrant === "Hedged Rally"
              ? "bg-cyan-950/40 border-cyan-500 ring-1 ring-cyan-500"
              : "bg-zinc-900/40 border-zinc-800 hover:border-zinc-700"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-cyan-400">📈 Hedged Rally</span>
            <span className="text-lg font-bold text-cyan-300">{counts["Hedged Rally"]}</span>
          </div>
          <p className="text-[11px] text-zinc-400 mt-1">Up 1M + Puts Bid (Tighten Stops)</p>
        </button>

        {/* Fear */}
        <button
          onClick={() => setSelectedQuadrant(selectedQuadrant === "Fear" ? "ALL" : "Fear")}
          className={`p-3 rounded-xl border text-left transition ${
            selectedQuadrant === "Fear"
              ? "bg-rose-950/40 border-rose-500 ring-1 ring-rose-500"
              : "bg-zinc-900/40 border-zinc-800 hover:border-zinc-700"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-rose-400">🛡️ Fear</span>
            <span className="text-lg font-bold text-rose-300">{counts["Fear"]}</span>
          </div>
          <p className="text-[11px] text-zinc-400 mt-1">Down 1M + Puts Bid (Protection Heavy)</p>
        </button>
      </div>

      {/* Sub-Tabs */}
      <div className="flex items-center gap-2 border-b border-zinc-800 pb-2">
        <button
          onClick={() => setActiveTab("map")}
          className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${
            activeTab === "map" ? "bg-zinc-800 text-white" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          🗺️ 2D Quadrant Map
        </button>
        <button
          onClick={() => setActiveTab("table")}
          className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${
            activeTab === "table" ? "bg-zinc-800 text-white" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          📋 Watchlist Table ({filteredRecords.length})
        </button>
        <button
          onClick={() => setActiveTab("sectors")}
          className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${
            activeTab === "sectors" ? "bg-zinc-800 text-white" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          🌐 Sector Agreement ({sectors.length})
        </button>
        <button
          onClick={() => setActiveTab("changes")}
          className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${
            activeTab === "changes" ? "bg-zinc-800 text-white" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          ⚡ WoW Shifts ({changes.length})
        </button>
      </div>

      {/* 1. 2D QUADRANT MAP */}
      {activeTab === "map" && (
        <div className="space-y-4">
          <div className="relative w-full h-[520px] bg-zinc-950 border border-zinc-800 rounded-xl p-4 overflow-hidden">
            {/* Quadrant Background Labels */}
            <div className="absolute top-3 left-3 text-xs font-semibold text-rose-500/60 pointer-events-none">
              🛡️ FEAR (Down + Puts Bid)
            </div>
            <div className="absolute top-3 right-3 text-xs font-semibold text-cyan-500/60 pointer-events-none text-right">
              📈 HEDGED RALLY (Up + Puts Bid)
            </div>
            <div className="absolute bottom-3 left-3 text-xs font-semibold text-emerald-500/70 pointer-events-none">
              🎯 CONTRARIAN BID (Down + Calls Bid)
            </div>
            <div className="absolute bottom-3 right-3 text-xs font-semibold text-amber-500/60 pointer-events-none text-right">
              ⚠️ CHASE (Up + Calls Bid)
            </div>

            {/* Center Axes */}
            <div className="absolute top-0 bottom-0 left-1/2 w-0 border-r border-dashed border-zinc-700 pointer-events-none" />
            <div className="absolute left-0 right-0 top-1/2 h-0 border-b border-dashed border-zinc-700 pointer-events-none" />

            {/* Axis Titles */}
            <div className="absolute top-1/2 right-2 -translate-y-6 text-[10px] text-zinc-500 pointer-events-none">
              + 1M Return →
            </div>
            <div className="absolute top-1/2 left-2 -translate-y-6 text-[10px] text-zinc-500 pointer-events-none">
              ← - 1M Return
            </div>
            <div className="absolute top-2 left-1/2 translate-x-2 text-[10px] text-zinc-500 pointer-events-none">
              ↑ + Puts Bid (Skew &gt; 0)
            </div>
            <div className="absolute bottom-2 left-1/2 translate-x-2 text-[10px] text-zinc-500 pointer-events-none">
              ↓ + Calls Bid (Skew &lt; 0)
            </div>

            {/* Render Data Points */}
            <div className="relative w-full h-full">
              {filteredRecords.map((r) => {
                // Map ret_1m from [-0.25, +0.25] to [5%, 95%]
                const clampX = Math.max(-0.25, Math.min(0.25, r.ret_1m));
                const leftPct = ((clampX + 0.25) / 0.5) * 90 + 5;

                // Map normalized_skew from [-0.5, +0.5] to [95%, 5%] (inverted Y)
                const clampY = Math.max(-0.5, Math.min(0.5, r.normalized_skew));
                const topPct = ((0.5 - clampY) / 1.0) * 90 + 5;

                const colorMap = {
                  "Contrarian Bid": "bg-emerald-400 ring-emerald-500/50 hover:bg-emerald-300",
                  Chase: "bg-amber-400 ring-amber-500/50 hover:bg-amber-300",
                  "Hedged Rally": "bg-cyan-400 ring-cyan-500/50 hover:bg-cyan-300",
                  Fear: "bg-rose-400 ring-rose-500/50 hover:bg-rose-300",
                };

                return (
                  <button
                    key={r.ticker}
                    onClick={() => onSelectTicker(r.ticker)}
                    style={{ left: `${leftPct}%`, top: `${topPct}%` }}
                    className={`absolute -translate-x-1/2 -translate-y-1/2 group flex items-center justify-center`}
                  >
                    <div
                      className={`w-3 h-3 rounded-full ${colorMap[r.quadrant]} ring-4 transition transform group-hover:scale-150`}
                    />
                    <span className="absolute top-3.5 left-1/2 -translate-x-1/2 text-[10px] font-bold text-zinc-300 bg-zinc-900/90 px-1 py-0.5 rounded opacity-80 group-hover:opacity-100 whitespace-nowrap pointer-events-none z-10">
                      {r.ticker}
                    </span>

                    {/* Tooltip on hover */}
                    <div className="hidden group-hover:block absolute bottom-6 left-1/2 -translate-x-1/2 bg-zinc-900 border border-zinc-700 p-2.5 rounded-lg shadow-xl text-left min-w-[200px] z-30 pointer-events-none">
                      <div className="font-bold text-white text-xs flex justify-between">
                        <span>{r.ticker}</span>
                        <span className="text-zinc-400">${r.spot.toFixed(2)}</span>
                      </div>
                      <div className="text-[11px] text-zinc-300 mt-1 space-y-0.5">
                        <div>1M Return: <span className={r.ret_1m >= 0 ? "text-emerald-400" : "text-rose-400"}>{(r.ret_1m * 100).toFixed(1)}%</span></div>
                        <div>Norm Skew: <span className="font-mono text-cyan-300">{r.normalized_skew.toFixed(2)}</span></div>
                        <div>Quadrant: <span className="font-semibold text-zinc-200">{r.quadrant}</span></div>
                        <div>ATM IV: {(r.atm_iv * 100).toFixed(1)}%</div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* 2. WATCHLIST TABLE */}
      {activeTab === "table" && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <select
              value={selectedSector}
              onChange={(e) => setSelectedSector(e.target.value)}
              className="bg-zinc-900 border border-zinc-800 text-zinc-300 text-xs rounded-lg px-3 py-1.5"
            >
              <option value="ALL">All Sectors</option>
              {sectors.map((s) => (
                <option key={s.sector} value={s.sector}>
                  {s.sector}
                </option>
              ))}
            </select>
          </div>

          <div className="overflow-x-auto bg-zinc-900/40 border border-zinc-800 rounded-xl">
            <table className="w-full text-left text-xs">
              <thead className="bg-zinc-900/80 text-zinc-400 border-b border-zinc-800">
                <tr>
                  <th className="p-3">Ticker</th>
                  <th className="p-3">Sector</th>
                  <th className="p-3">Spot</th>
                  <th className="p-3">1M Ret</th>
                  <th className="p-3">vs SPY</th>
                  <th className="p-3">ATM IV</th>
                  <th className="p-3">Norm Skew</th>
                  <th className="p-3">Quadrant</th>
                  <th className="p-3">Verdict</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {filteredRecords.map((r) => (
                  <tr
                    key={r.ticker}
                    onClick={() => onSelectTicker(r.ticker)}
                    className="hover:bg-zinc-800/40 cursor-pointer transition"
                  >
                    <td className="p-3 font-bold text-white flex items-center gap-1.5">
                      <span>{r.ticker}</span>
                      {r.is_earnings_near && (
                        <span className="text-[10px] px-1 bg-amber-950 text-amber-400 border border-amber-800 rounded" title={`Earnings on ${r.earnings_date}`}>
                          ER
                        </span>
                      )}
                    </td>
                    <td className="p-3 text-zinc-400">{r.sector}</td>
                    <td className="p-3 font-mono text-zinc-300">${r.spot.toFixed(2)}</td>
                    <td className={`p-3 font-mono ${r.ret_1m >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {(r.ret_1m * 100).toFixed(1)}%
                    </td>
                    <td className={`p-3 font-mono ${r.rel_ret_spy >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {r.rel_ret_spy >= 0 ? "+" : ""}{(r.rel_ret_spy * 100).toFixed(1)}%
                    </td>
                    <td className="p-3 font-mono text-zinc-300">{(r.atm_iv * 100).toFixed(1)}%</td>
                    <td className="p-3 font-mono text-cyan-300">{r.normalized_skew.toFixed(2)}</td>
                    <td className="p-3">
                      <span
                        className={`px-2 py-0.5 rounded text-[11px] font-medium ${
                          r.quadrant === "Contrarian Bid"
                            ? "bg-emerald-950/60 text-emerald-400 border border-emerald-800"
                            : r.quadrant === "Chase"
                            ? "bg-amber-950/60 text-amber-400 border border-amber-800"
                            : r.quadrant === "Hedged Rally"
                            ? "bg-cyan-950/60 text-cyan-400 border border-cyan-800"
                            : "bg-rose-950/60 text-rose-400 border border-rose-800"
                        }`}
                      >
                        {r.quadrant}
                      </span>
                    </td>
                    <td className="p-3 text-zinc-300 max-w-xs truncate" title={r.verdict}>
                      {r.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 3. SECTOR AGREEMENT GRID */}
      {activeTab === "sectors" && (
        <div className="space-y-3">
          <p className="text-xs text-zinc-400">
            PDF Trap #2: Use raw vol points when comparing across sectors. Trap #3: Verify sector agreement before trusting a sector signal.
          </p>
          <div className="overflow-x-auto bg-zinc-900/40 border border-zinc-800 rounded-xl">
            <table className="w-full text-left text-xs">
              <thead className="bg-zinc-900/80 text-zinc-400 border-b border-zinc-800">
                <tr>
                  <th className="p-3">Sector</th>
                  <th className="p-3">Tickers</th>
                  <th className="p-3">Avg 1M Ret</th>
                  <th className="p-3">Raw Skew (Pts)</th>
                  <th className="p-3">Norm Skew</th>
                  <th className="p-3">Dominant Lean</th>
                  <th className="p-3">Agreement</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {sectors.map((s) => (
                  <tr key={s.sector} className="hover:bg-zinc-800/30">
                    <td className="p-3 font-semibold text-white">{s.sector}</td>
                    <td className="p-3 text-zinc-400">{s.ticker_count}</td>
                    <td className={`p-3 font-mono ${s.avg_ret_1m >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {(s.avg_ret_1m * 100).toFixed(1)}%
                    </td>
                    <td className="p-3 font-mono text-zinc-300">
                      {(s.avg_raw_skew * 100).toFixed(1)} pts
                    </td>
                    <td className="p-3 font-mono text-cyan-300">{s.avg_norm_skew.toFixed(2)}</td>
                    <td className="p-3 font-medium text-zinc-200">{s.dominant_lean}</td>
                    <td className="p-3">
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-2 bg-zinc-800 rounded-full overflow-hidden">
                          <div
                            className={`h-full ${
                              s.agreement >= 0.75
                                ? "bg-emerald-500"
                                : s.agreement >= 0.5
                                ? "bg-amber-500"
                                : "bg-rose-500"
                            }`}
                            style={{ width: `${s.agreement * 100}%` }}
                          />
                        </div>
                        <span className="font-mono text-zinc-300">{(s.agreement * 100).toFixed(0)}%</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 4. WEEK-OVER-WEEK SHIFTS */}
      {activeTab === "changes" && (
        <div className="space-y-3">
          <p className="text-xs text-zinc-400">
            PDF Principle: &quot;The level is structural; the change is the signal.&quot; Tracking WoW skew expansion/contraction and quadrant flips.
          </p>
          <div className="overflow-x-auto bg-zinc-900/40 border border-zinc-800 rounded-xl">
            <table className="w-full text-left text-xs">
              <thead className="bg-zinc-900/80 text-zinc-400 border-b border-zinc-800">
                <tr>
                  <th className="p-3">Ticker</th>
                  <th className="p-3">Current Skew</th>
                  <th className="p-3">Prev Skew</th>
                  <th className="p-3">Skew Delta</th>
                  <th className="p-3">Quadrant Shift</th>
                  <th className="p-3">Verdict</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {changes.map((c) => (
                  <tr
                    key={c.ticker}
                    onClick={() => onSelectTicker(c.ticker)}
                    className="hover:bg-zinc-800/40 cursor-pointer"
                  >
                    <td className="p-3 font-bold text-white">{c.ticker}</td>
                    <td className="p-3 font-mono text-cyan-300">{c.current_norm_skew.toFixed(2)}</td>
                    <td className="p-3 font-mono text-zinc-400">{c.prev_norm_skew.toFixed(2)}</td>
                    <td
                      className={`p-3 font-mono font-bold ${
                        c.skew_change_norm < 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {c.skew_change_norm > 0 ? "+" : ""}{c.skew_change_norm.toFixed(2)}
                    </td>
                    <td className="p-3">
                      {c.quadrant_changed ? (
                        <div className="flex items-center gap-1.5 text-[11px]">
                          <span className="text-zinc-500 line-through">{c.prev_quadrant}</span>
                          <span>→</span>
                          <span className="font-semibold text-emerald-400">{c.current_quadrant}</span>
                        </div>
                      ) : (
                        <span className="text-zinc-400 text-[11px]">{c.current_quadrant} (steady)</span>
                      )}
                    </td>
                    <td className="p-3 text-zinc-300 max-w-xs truncate" title={c.verdict}>
                      {c.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
