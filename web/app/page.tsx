"use client";

import { useCallback, useEffect, useState } from "react";
import { BottomNav, type Tab } from "@/components/BottomNav";
import { OpportunitiesList } from "@/components/OpportunitiesList";
import { PositionCard } from "@/components/PositionCard";
import { BasketCard } from "@/components/BasketCard";
import { BasketBuilder } from "@/components/BasketBuilder";
import { AllocationPanel } from "@/components/AllocationPanel";
import { BriefCard } from "@/components/BriefCard";
import { ScanProgress } from "@/components/ScanProgress";
import { ScorecardCard } from "@/components/Scorecard";
import { ServiceWorkerRegister } from "@/components/ServiceWorkerRegister";
import { SkewMap } from "@/components/SkewMap";
import { StockDetailSheet } from "@/components/StockDetailSheet";
import { parseBackendDate } from "@/lib/ui";
import type {
  AnalystResponse,
  ApiError,
  BasketsResponse,
  BasketView,
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
  SearchResponse,
  SymbolMatch,
} from "@/lib/types";

const TAB_TITLES: Record<Tab, string> = {
  opportunities: "Ranked opportunities",
  recommendations: "High-confidence buys",
  skew: "Options Skew Map",
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

  const [searchResults, setSearchResults] = useState<SymbolMatch[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);

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
  const [addBusy, setAddBusy] = useState(false);
  const [posNote, setPosNote] = useState<string | null>(null);

  // Joint portfolios (baskets): one budget split across stocks by percentage.
  const [baskets, setBaskets] = useState<BasketView[]>([]);
  const [basketBusy, setBasketBusy] = useState(false);
  const [basketNote, setBasketNoteMsg] = useState<string | null>(null);
  const [builderOpen, setBuilderOpen] = useState(false);

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

  const loadBaskets = useCallback(async () => {
    try {
      const res = await fetch("/api/baskets");
      const body = (await res.json()) as BasketsResponse;
      setBaskets(body.baskets ?? []);
    } catch {
      // baskets are non-critical; the tab still shows single positions
    }
  }, []);

  const createBasket = useCallback(
    async (
      name: string,
      budget: number,
      tickers: string[],
      pcts: number[],
    ): Promise<boolean> => {
      setBasketBusy(true);
      try {
        const params = new URLSearchParams({
          ...(name ? { name } : {}),
          budget: String(budget),
          tickers: tickers.join(","),
          pcts: pcts.join(","),
        });
        const res = await fetch(`/api/baskets?${params}`, { method: "POST" });
        if (!res.ok) {
          const body = (await res.json()) as ApiError;
          setBasketNoteMsg(body.detail ?? "could not create portfolio");
          return false;
        }
        setBasketNoteMsg(null);
        await loadBaskets();
        return true;
      } catch {
        setBasketNoteMsg("could not reach the scoring service");
        return false;
      } finally {
        setBasketBusy(false);
      }
    },
    [loadBaskets],
  );

  const closeBasket = useCallback(
    async (id: string) => {
      try {
        await fetch(`/api/baskets/${encodeURIComponent(id)}/close`, {
          method: "POST",
        });
        await loadBaskets();
      } catch {
        // leave card as-is; user can retry
      }
    },
    [loadBaskets],
  );

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
    setNewsNote(
      "Updating news — fetching and scoring the last week of headlines…",
    );
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
    loadBaskets();
    loadScorecard();
    loadNewsStatus();
  }, [
    loadOpportunities,
    loadRecommendations,
    loadPositions,
    loadBaskets,
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

  // Search-as-you-type by company name or ticker (debounced). Lets the user find a
  // stock without knowing its symbol; tapping a result opens the full scoring card.
  useEffect(() => {
    if (tab !== "search") return;
    const q = ticker.trim();
    if (q.length < 2) {
      setSearchResults([]);
      setSearchBusy(false);
      return;
    }
    let active = true;
    setSearchBusy(true);
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
        if (res.ok && active) {
          const body = (await res.json()) as SearchResponse;
          setSearchResults(body.results ?? []);
        }
      } catch {
        /* search is best-effort */
      } finally {
        if (active) setSearchBusy(false);
      }
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [ticker, tab]);

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
    async (symbol: string, quantity: number = 1) => {
      const clean = symbol.trim().toUpperCase();
      if (!clean) return false;
      setAddBusy(true);
      setPosNote(null);
      try {
        const res = await fetch(
          `/api/positions?ticker=${encodeURIComponent(clean)}&quantity=${quantity}`,
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

  const deletePosition = useCallback(
    async (id: string) => {
      try {
        const res = await fetch(`/api/positions/${encodeURIComponent(id)}`, {
          method: "DELETE",
        });
        if (!res.ok) {
          setPosNote("could not delete that position");
          return;
        }
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
      <BasketBuilder
        open={builderOpen}
        onClose={() => setBuilderOpen(false)}
        onCreate={async (name, budget, tickers, pcts) => {
          const ok = await createBasket(name, budget, tickers, pcts);
          if (ok) setBuilderOpen(false);
        }}
        busy={basketBusy}
      />


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
                ? `scanned ${parseBackendDate(scannedAt).toLocaleString()}`
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

        {tab === "skew" && (
          <SkewMap onSelectTicker={lookup} />
        )}

        {tab === "tracked" && (
          <>
            <div className="opps-header">
              <h2>Joint portfolios</h2>
              <button
                className="newbasketbtn"
                onClick={() => setBuilderOpen(true)}
              >
                ＋ New portfolio
              </button>
            </div>
            <p className="hint">
              One budget, split across stocks by percentage. Tap a portfolio to
              see how each stock moves your whole capital. To track a single
              stock you bought, search it in the Search tab and tap{" "}
              <strong>Track</strong> on its page.
            </p>
            {basketNote && <div className="status">{basketNote}</div>}
            {baskets.length > 0 && (
              <div className="reclist">
                {baskets.map((b) => (
                  <BasketCard key={b.id} basket={b} onClose={closeBasket} />
                ))}
              </div>
            )}

            <BriefCard />

            <AllocationPanel budgetHint={baskets.reduce((s, b) => s + (b.total_budget ?? 0), 0)} />

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
                    onDelete={deletePosition}
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
                if (searchResults.length > 0) lookup(searchResults[0].ticker);
                else if (ticker.trim()) lookup(ticker);
              }}
            >
              <input
                value={ticker}
                onChange={(e) => setTicker(e.target.value)}
                placeholder="Search by company or ticker (e.g. Apple or AAPL)"
                aria-label="Company name or ticker"
                autoCapitalize="none"
                autoCorrect="off"
              />
              <button type="submit" disabled={loading || !ticker.trim()}>
                {loading ? "Scoring…" : "Score"}
              </button>
            </form>
            <p className="hint">
              Search by company name or ticker — tap a result for the full
              conviction breakdown and live news sentiment.
            </p>
            {searchBusy && <div className="status">Searching…</div>}
            {searchResults.length > 0 && (
              <div className="searchresults">
                {searchResults.map((m) => (
                  <button
                    key={m.ticker}
                    type="button"
                    className="searchrow"
                    onClick={() => lookup(m.ticker)}
                  >
                    <span className="searchrow-name">{m.name}</span>
                    <span className="searchrow-ticker">{m.ticker}</span>
                  </button>
                ))}
              </div>
            )}
            {!searchBusy &&
              ticker.trim().length >= 2 &&
              searchResults.length === 0 && (
                <div className="status">
                  No matches — try a different name or the exact ticker.
                </div>
              )}
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
          onAdd={(quantity) => {
            void addPositionByTicker(detailTicker, quantity);
          }}
          adding={addBusy}
          tracked={positions.some(
            (p) => p.ticker === detailTicker && p.status === "open",
          )}
        />
      )}
    </div>
  );
}
