"use client";

import { useCallback, useEffect, useState } from "react";
import { BottomNav, type Tab } from "@/components/BottomNav";
import { OpportunitiesList } from "@/components/OpportunitiesList";
import { PositionCard } from "@/components/PositionCard";
import { ScanProgress } from "@/components/ScanProgress";
import { ScorecardCard } from "@/components/Scorecard";
import { ServiceWorkerRegister } from "@/components/ServiceWorkerRegister";
import { StockDetailSheet } from "@/components/StockDetailSheet";
import type {
  AnalystResponse,
  ApiError,
  ExplainResponse,
  NewsResponse,
  OpportunitiesResponse,
  Opportunity,
  PositionsResponse,
  PositionView,
  Recommendation,
  RecommendationsResponse,
  Scorecard,
  ScanStatus,
  NewsStatus,
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
  const [analyst, setAnalyst] = useState<AnalystResponse | null>(null);
  const [explain, setExplain] = useState<ExplainResponse | null>(null);
  const [explainLoading, setExplainLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [detailTicker, setDetailTicker] = useState("");

  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [scannedAt, setScannedAt] = useState<string | null>(null);
  const [oppNote, setOppNote] = useState<string | null>(null);

  const [scanning, setScanning] = useState(false);
  const [scanNote, setScanNote] = useState<string | null>(null);
  const [scanPct, setScanPct] = useState<number | null>(null);

  const [newsBusy, setNewsBusy] = useState(false);
  const [newsNote, setNewsNote] = useState<string | null>(null);
  const [newsPct, setNewsPct] = useState<number | null>(null);
  const [newsDaysSince, setNewsDaysSince] = useState<number | null>(null);
  const [newsDate, setNewsDate] = useState<string | null>(null);

  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [recNote, setRecNote] = useState<string | null>(null);

  const [positions, setPositions] = useState<PositionView[]>([]);
  const [addTicker, setAddTicker] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [posNote, setPosNote] = useState<string | null>(null);

  const [scorecard, setScorecard] = useState<Scorecard | null>(null);

  const loadOpportunities = useCallback(async () => {
    try {
      const res = await fetch("/api/opportunities?limit=60");
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

  const loadScorecard = useCallback(async () => {
    try {
      const res = await fetch("/api/scorecard");
      const body = (await res.json()) as Scorecard;
      if (body && body.verdict) setScorecard(body);
    } catch {
      // scorecard is non-critical; leave it hidden if the backend is unreachable
    }
  }, []);

  const loadNewsStatus = useCallback(async () => {
    try {
      const res = await fetch("/api/news-collect");
      const status = (await res.json()) as NewsStatus;
      setNewsDaysSince(status.days_since ?? null);
      setNewsDate(status.last_news_date ?? null);
    } catch {
      // freshness is non-critical; leave it hidden if the backend is unreachable
    }
  }, []);

  const runScan = useCallback(async () => {
    setScanning(true);
    setScanPct(0);
    setScanNote(
      "Refreshing — scoring the whole universe with the latest data…",
    );
    try {
      const res = await fetch("/api/scan", { method: "POST" });
      const body = (await res.json()) as ScanStatus & ApiError;
      if (!res.ok) {
        setScanNote(body.detail ?? "could not start a refresh");
        setScanning(false);
        setScanPct(null);
        return;
      }
      // Poll until the backend reports the scan has finished (cap ~15 min).
      const started = Date.now();
      let finished = false;
      while (Date.now() - started < 15 * 60 * 1000) {
        await new Promise((r) => setTimeout(r, 2000));
        const sres = await fetch("/api/scan");
        const status = (await sres.json()) as ScanStatus;
        const prog = status.progress;
        if (prog && prog.total > 0) {
          setScanPct(Math.round((prog.done / prog.total) * 100));
          setScanNote(
            `Scoring the universe… ${prog.done} of ${prog.total} names`,
          );
        }
        if (!status.running) {
          finished = true;
          setScanPct(100);
          setScanNote(
            status.last_error
              ? `Refresh failed: ${status.last_error}`
              : `Updated${
                  status.last_count
                    ? ` — ${status.last_count} names scored`
                    : ""
                }.`,
          );
          break;
        }
      }
      if (!finished) {
        setScanNote(
          "Still scanning in the background — pull to refresh again in a bit.",
        );
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
      setScanPct(null);
    }
  }, [loadOpportunities, loadRecommendations, loadPositions]);

  const runNewsCollect = useCallback(async () => {
    setNewsBusy(true);
    setNewsPct(0);
    setNewsNote("Updating news — fetching and scoring the last week of headlines…");
    try {
      const res = await fetch("/api/news-collect?days=7", { method: "POST" });
      const body = (await res.json()) as NewsStatus & ApiError;
      if (!res.ok) {
        setNewsNote(body.detail ?? "could not start a news update");
        setNewsBusy(false);
        setNewsPct(null);
        return;
      }
      // Poll until the backend reports the collection has finished (cap ~15 min).
      const started = Date.now();
      let finished = false;
      while (Date.now() - started < 15 * 60 * 1000) {
        await new Promise((r) => setTimeout(r, 2000));
        const sres = await fetch("/api/news-collect");
        const status = (await sres.json()) as NewsStatus;
        const prog = status.progress;
        if (prog && prog.total > 0) {
          setNewsPct(Math.round((prog.done / prog.total) * 100));
          setNewsNote(`Scoring headlines… ${prog.done} of ${prog.total} names`);
        }
        if (!status.running) {
          finished = true;
          setNewsPct(100);
          setNewsNote(
            status.last_error
              ? `News update failed: ${status.last_error}`
              : `News updated${
                  status.last_archived
                    ? ` — ${status.last_archived} headlines archived`
                    : " — already up to date"
                }.`,
          );
          break;
        }
      }
      if (!finished) {
        setNewsNote(
          "Still updating news in the background — check back in a bit.",
        );
      }
    } catch {
      setNewsNote("could not reach the news service");
    } finally {
      setNewsBusy(false);
      setNewsPct(null);
      loadNewsStatus();
    }
  }, [loadNewsStatus]);

  useEffect(() => {
    loadOpportunities();
    loadRecommendations();
    loadPositions();
    loadScorecard();
    loadNewsStatus();
  }, [
    loadOpportunities,
    loadRecommendations,
    loadPositions,
    loadScorecard,
    loadNewsStatus,
  ]);
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
    setAnalyst(null);
    setExplain(null);
    try {
      const res = await fetch(`/api/score/${encodeURIComponent(clean)}`);
      const body = (await res.json()) as ScoreResponse | ApiError;
      if (!res.ok) {
        setError((body as ApiError).detail ?? `request failed (${res.status})`);
      } else {
        setData(body as ScoreResponse);
        const score = body as ScoreResponse;
        try {
          const nres = await fetch(`/api/news/${encodeURIComponent(clean)}`);
          if (nres.ok) setNews((await nres.json()) as NewsResponse);
        } catch {
          /* news is optional */
        }
        // AI plain-English narrative — reuses the score we just fetched (no re-score).
        setExplainLoading(true);
        fetch("/api/explain", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ticker: clean,
            recommendation: score.recommendation,
            conviction: score.conviction,
            drivers: score.drivers,
          }),
        })
          .then((eres) => (eres.ok ? eres.json() : null))
          .then((ebody) => {
            if (ebody) setExplain(ebody as ExplainResponse);
          })
          .catch(() => {
            /* AI narrative is optional */
          })
          .finally(() => setExplainLoading(false));
        try {
          const ares = await fetch(`/api/analyst/${encodeURIComponent(clean)}`);
          if (ares.ok) setAnalyst((await ares.json()) as AnalystResponse);
        } catch {
          /* analyst second opinion is optional */
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

  const addPositionByTicker = useCallback(
    async (symbol: string) => {
      const clean = symbol.trim().toUpperCase();
      if (!clean) return false;
      setAddBusy(true);
      setPosNote(null);
      try {
        const res = await fetch(
          `/api/positions?ticker=${encodeURIComponent(clean)}`,
          { method: "POST" },
        );
        if (!res.ok) {
          const body = (await res.json()) as ApiError;
          setPosNote(body.detail ?? `could not add ${clean}`);
          return false;
        }
        await loadPositions();
        return true;
      } catch {
        setPosNote("could not reach the scoring service");
        return false;
      } finally {
        setAddBusy(false);
      }
    },
    [loadPositions],
  );

  const addPosition = useCallback(async () => {
    const ok = await addPositionByTicker(addTicker);
    if (ok) setAddTicker("");
  }, [addTicker, addPositionByTicker]);

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
              <div className="opps-actions">
                <button
                  type="button"
                  className="refresh-btn"
                  onClick={runNewsCollect}
                  disabled={newsBusy}
                  title="Fetch + score the last week of news and archive it (separate from Refresh)"
                >
                  {newsBusy
                    ? newsPct !== null
                      ? `Updating news ${newsPct}%`
                      : "Updating news…"
                    : "Update news"}
                </button>
                <button
                  type="button"
                  className="refresh-btn"
                  onClick={runScan}
                  disabled={scanning}
                >
                  {scanning
                    ? scanPct !== null
                      ? `Refreshing ${scanPct}%`
                      : "Refreshing…"
                    : "Refresh"}
                </button>
              </div>
            </div>
            <p className="opps-meta">
              {scannedAt
                ? `scanned ${new Date(scannedAt).toLocaleString()}`
                : "not scanned yet"}
            </p>
            {newsDaysSince !== null && (
              <p className="opps-meta">
                news{" "}
                {newsDaysSince <= 0
                  ? "updated today"
                  : newsDaysSince === 1
                    ? "updated yesterday"
                    : `last updated ${newsDaysSince} days ago`}
                {newsDate ? ` (${newsDate})` : ""}
                {newsDaysSince >= 5 ? " — tap “Update news”" : ""}
              </p>
            )}
            {scanning ? (
              <ScanProgress pct={scanPct} label={scanNote ?? undefined} />
            ) : (
              scanNote && (
                <div className="status" role="status" aria-live="polite">
                  {scanNote}
                </div>
              )
            )}
            {newsBusy ? (
              <ScanProgress pct={newsPct} label={newsNote ?? undefined} />
            ) : (
              newsNote && (
                <div className="status" role="status" aria-live="polite">
                  {newsNote}
                </div>
              )
            )}
            {oppNote && <div className="status">{oppNote}</div>}
            {opps.length > 0 && (
              <OpportunitiesList items={opps} onPick={lookup} />
            )}
          </>
        )}

        {tab === "recommendations" && (
          <>
            {scorecard && <ScorecardCard data={scorecard} />}
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
                    <span
                      className="oppscore"
                      style={{ color: "var(--green)" }}
                    >
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
          analyst={analyst}
          explain={explain}
          explainLoading={explainLoading}
          loading={loading}
          error={error}
          onClose={closeSheet}
          onAdd={() => addPositionByTicker(detailTicker)}
          adding={addBusy}
          tracked={positions.some(
            (p) => p.ticker === detailTicker && p.status === "open",
          )}
        />
      )}
    </div>
  );
}
