"use client";

import { useCallback, useEffect, useState } from "react";
import { BottomNav, type Tab } from "@/components/BottomNav";
import { OpportunitiesList } from "@/components/OpportunitiesList";
import { PositionCard } from "@/components/PositionCard";
import { ServiceWorkerRegister } from "@/components/ServiceWorkerRegister";
import { StockDetailSheet } from "@/components/StockDetailSheet";
import type {
  ApiError,
  NewsResponse,
  OpportunitiesResponse,
  Opportunity,
  PositionsResponse,
  PositionView,
  Recommendation,
  RecommendationsResponse,
  ScanStatus,
  ScoreResponse,
} from "@/lib/types";

const TAB_TITLES: Record<Tab, string> = {
  opportunities: "Ranked opportunities",
  recommendations: "High-confidence buys",
  tracked: "Portfolio",
  search: "Search",
};

export default function Home() {
  const [tab, setTab] = useState<Tab>("opportunities");

  const [ticker, setTicker] = useState("");
  const [data, setData] = useState<ScoreResponse | null>(null);
  const [news, setNews] = useState<NewsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [detailTicker, setDetailTicker] = useState("");

  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [scannedAt, setScannedAt] = useState<string | null>(null);
  const [oppNote, setOppNote] = useState<string | null>(null);

  const [scanning, setScanning] = useState(false);
  const [scanNote, setScanNote] = useState<string | null>(null);

  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [recNote, setRecNote] = useState<string | null>(null);

  const [positions, setPositions] = useState<PositionView[]>([]);
  const [addTicker, setAddTicker] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [posNote, setPosNote] = useState<string | null>(null);

  const loadOpportunities = useCallback(async () => {
    try {
      const res = await fetch("/api/opportunities?limit=40");
      const body = (await res.json()) as OpportunitiesResponse;
      setOpps(body.opportunities ?? []);
      setScannedAt(body.scanned_at ?? null);
      setOppNote(body.note ?? null);
    } catch {
      setOppNote("could not reach the scoring service");
    }
  }, []);

  const loadRecommendations = useCallback(async () => {
    try {
      const res = await fetch("/api/recommendations");
      const body = (await res.json()) as RecommendationsResponse;
      setRecs(body.recommendations ?? []);
      setRecNote(body.note ?? null);
    } catch {
      setRecNote("could not reach the scoring service");
    }
  }, []);

  const loadPositions = useCallback(async () => {
    try {
      const res = await fetch("/api/positions");
      const body = (await res.json()) as PositionsResponse;
      setPositions(body.positions ?? []);
      setPosNote(
        (body.positions ?? []).length === 0
          ? "No tracked positions yet."
          : null,
      );
    } catch {
      setPosNote("could not reach the scoring service");
    }
  }, []);

  const runScan = useCallback(async () => {
    setScanning(true);
    setScanNote("Refreshing — scoring the whole universe with the latest data…");
    try {
      const res = await fetch("/api/scan", { method: "POST" });
      const body = (await res.json()) as ScanStatus & ApiError;
      if (!res.ok) {
        setScanNote(body.detail ?? "could not start a refresh");
        setScanning(false);
        return;
      }
      // Poll until the backend reports the scan has finished (cap ~15 min).
      const started = Date.now();
      while (Date.now() - started < 15 * 60 * 1000) {
        await new Promise((r) => setTimeout(r, 3000));
        const sres = await fetch("/api/scan");
        const status = (await sres.json()) as ScanStatus;
        if (!status.running) {
          setScanNote(
            status.last_error
              ? `Refresh failed: ${status.last_error}`
              : `Updated${
                  status.last_count ? ` — ${status.last_count} names scored` : ""
                }.`,
          );
          break;
        }
      }
      await Promise.all([
        loadOpportunities(),
        loadRecommendations(),
        loadPositions(),
      ]);
    } catch {
      setScanNote("could not reach the scoring service");
    } finally {
      setScanning(false);
    }
  }, [loadOpportunities, loadRecommendations, loadPositions]);

  useEffect(() => {
    loadOpportunities();
    loadRecommendations();
    loadPositions();
  }, [loadOpportunities, loadRecommendations, loadPositions]);

  // Open the full-screen analysis sheet for a ticker (from any list or search).
  const lookup = useCallback(async (symbol: string) => {
    const clean = symbol.trim().toUpperCase();
    if (!clean) return;
    setDetailTicker(clean);
    setSheetOpen(true);
    setLoading(true);
    setError(null);
    setData(null);
    setNews(null);
    try {
      const res = await fetch(`/api/score/${encodeURIComponent(clean)}`);
      const body = (await res.json()) as ScoreResponse | ApiError;
      if (!res.ok) {
        setError((body as ApiError).detail ?? `request failed (${res.status})`);
      } else {
        setData(body as ScoreResponse);
        try {
          const nres = await fetch(`/api/news/${encodeURIComponent(clean)}`);
          if (nres.ok) setNews((await nres.json()) as NewsResponse);
        } catch {
          /* news is optional */
        }
      }
    } catch {
      setError("could not reach the scoring service");
    } finally {
      setLoading(false);
    }
  }, []);

  const closeSheet = useCallback(() => {
    setSheetOpen(false);
  }, []);

  // Make the phone's back gesture / button close the analysis sheet instead of
  // leaving the app, and lock the underlying list so it keeps its scroll spot.
  useEffect(() => {
    if (!sheetOpen) return;
    document.body.classList.add("sheet-open");
    window.history.pushState({ sheet: true }, "");
    const onPop = () => setSheetOpen(false);
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      document.body.classList.remove("sheet-open");
      if (window.history.state?.sheet) window.history.back();
    };
  }, [sheetOpen]);

  const addPosition = useCallback(async () => {
    const clean = addTicker.trim().toUpperCase();
    if (!clean) return;
    setAddBusy(true);
    setPosNote(null);
    try {
      const res = await fetch(
        `/api/positions?ticker=${encodeURIComponent(clean)}`,
        {
          method: "POST",
        },
      );
      if (!res.ok) {
        const body = (await res.json()) as ApiError;
        setPosNote(body.detail ?? `could not add ${clean}`);
      } else {
        setAddTicker("");
        await loadPositions();
      }
    } catch {
      setPosNote("could not reach the scoring service");
    } finally {
      setAddBusy(false);
    }
  }, [addTicker, loadPositions]);

  const sellPosition = useCallback(
    async (id: string) => {
      try {
        await fetch(`/api/positions/${encodeURIComponent(id)}/sell`, {
          method: "POST",
        });
        await loadPositions();
      } catch {
        setPosNote("could not reach the scoring service");
      }
    },
    [loadPositions],
  );

  return (
    <div className="app-shell">
      <ServiceWorkerRegister />

      <header className="topbar">
        <div className="topbar-brand">
          <span className="topbar-title">Stock-Monitor</span>
          <span className="topbar-sub">{TAB_TITLES[tab]}</span>
        </div>
      </header>

      <main className="tabpanel">
        {tab === "opportunities" && (
          <>
            <div className="opps-header">
              <h2>Ranked opportunities</h2>
              <button
                type="button"
                className="refresh-btn"
                onClick={runScan}
                disabled={scanning}
              >
                {scanning ? "Refreshing…" : "Refresh"}
              </button>
            </div>
            <p className="opps-meta">
              {scannedAt
                ? `scanned ${new Date(scannedAt).toLocaleString()}`
                : "not scanned yet"}
            </p>
            {scanNote && <div className="status">{scanNote}</div>}
            {oppNote && <div className="status">{oppNote}</div>}
            {opps.length > 0 && (
              <OpportunitiesList items={opps} onPick={lookup} />
            )}
          </>
        )}

        {tab === "recommendations" && (
          <>
            <div className="opps-header">
              <h2>High-confidence buys</h2>
              <span className="opps-meta">only when the model is sure</span>
            </div>
            {recNote && <div className="status">{recNote}</div>}
            <div className="reclist">
              {recs.map((r) => (
                <button
                  key={r.ticker}
                  className="reccard"
                  onClick={() => lookup(r.ticker)}
                >
                  <div className="recrow">
                    <span className="oppticker">{r.ticker}</span>
                    <span className="oppscore" style={{ color: "var(--green)" }}>
                      {r.capped_conviction}
                      <small>/100</small>
                    </span>
                    <span className="opprec" style={{ color: "var(--green)" }}>
                      {r.recommendation}
                    </span>
                  </div>
                  <p className="recwhy">{r.rationale}</p>
                </button>
              ))}
            </div>
          </>
        )}

        {tab === "tracked" && (
          <>
            <form
              className="searchbar"
              onSubmit={(e) => {
                e.preventDefault();
                addPosition();
              }}
            >
              <input
                value={addTicker}
                onChange={(e) => setAddTicker(e.target.value)}
                placeholder="Add a stock you bought (e.g. NVDA)"
                aria-label="Ticker to track"
              />
              <button type="submit" disabled={addBusy || !addTicker.trim()}>
                {addBusy ? "Adding…" : "Add"}
              </button>
            </form>
            <p className="hint">
              Snapshots today&apos;s price + the model&apos;s call, then tracks
              it — a crisp signal and an expert view so you decide.
            </p>

            <div className="opps-header">
              <h2>Open positions</h2>
            </div>
            {posNote && <div className="status">{posNote}</div>}
            <div className="reclist">
              {positions
                .filter((p) => p.status === "open")
                .map((p) => (
                  <PositionCard
                    key={p.id}
                    p={p}
                    onSell={sellPosition}
                    onLookup={lookup}
                  />
                ))}
            </div>

            {positions.some((p) => p.status === "sold") && (
              <>
                <div className="opps-header">
                  <h2>Sold</h2>
                  <span className="opps-meta">how the call played out</span>
                </div>
                <div className="reclist">
                  {positions
                    .filter((p) => p.status === "sold")
                    .map((p) => (
                      <PositionCard
                        key={p.id}
                        p={p}
                        onSell={sellPosition}
                        onLookup={lookup}
                      />
                    ))}
                </div>
              </>
            )}
          </>
        )}

        {tab === "search" && (
          <>
            <form
              className="searchbar"
              onSubmit={(e) => {
                e.preventDefault();
                lookup(ticker);
              }}
            >
              <input
                value={ticker}
                onChange={(e) => setTicker(e.target.value)}
                placeholder="Look up any ticker (e.g. AAPL)"
                aria-label="Ticker symbol"
              />
              <button type="submit" disabled={loading || !ticker.trim()}>
                {loading ? "Scoring…" : "Score"}
              </button>
            </form>
            <p className="hint">
              Score any symbol on demand — you&apos;ll get the full conviction
              breakdown and live news sentiment.
            </p>
          </>
        )}
      </main>

      <BottomNav tab={tab} onChange={setTab} />

      {sheetOpen && (
        <StockDetailSheet
          ticker={detailTicker}
          data={data}
          news={news}
          loading={loading}
          error={error}
          onClose={closeSheet}
        />
      )}
    </div>
  );
}
