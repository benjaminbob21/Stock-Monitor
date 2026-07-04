"use client";

import { useCallback, useEffect, useState } from "react";
import { ConvictionCard } from "@/components/ConvictionCard";
import { NewsPanel } from "@/components/NewsPanel";
import { OpportunitiesList } from "@/components/OpportunitiesList";
import { PositionCard } from "@/components/PositionCard";
import type {
  ApiError,
  NewsResponse,
  OpportunitiesResponse,
  Opportunity,
  PositionsResponse,
  PositionView,
  Recommendation,
  RecommendationsResponse,
  ScoreResponse,
} from "@/lib/types";

type Tab = "opportunities" | "recommendations" | "tracked";

export default function Home() {
  const [tab, setTab] = useState<Tab>("opportunities");

  const [ticker, setTicker] = useState("");
  const [data, setData] = useState<ScoreResponse | null>(null);
  const [news, setNews] = useState<NewsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [scannedAt, setScannedAt] = useState<string | null>(null);
  const [oppNote, setOppNote] = useState<string | null>(null);

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

  useEffect(() => {
    loadOpportunities();
    loadRecommendations();
    loadPositions();
  }, [loadOpportunities, loadRecommendations, loadPositions]);

  const lookup = useCallback(async (symbol: string) => {
    const clean = symbol.trim().toUpperCase();
    if (!clean) return;
    setTab("opportunities");
    setTicker(clean);
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
    <main className="container">
      <div className="header">
        <h1>Stock-Monitor</h1>
        <p>
          Ranks the market vs the S&amp;P — you execute every trade. No
          auto-trading.
        </p>
      </div>

      <nav className="tabs">
        {(["opportunities", "recommendations", "tracked"] as Tab[]).map((t) => (
          <button
            key={t}
            className={`tab ${tab === t ? "active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t === "opportunities"
              ? "Opportunities"
              : t === "recommendations"
                ? "Recommendations"
                : "Tracked"}
          </button>
        ))}
      </nav>

      {tab === "opportunities" && (
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

          {error && <div className="status error">{error}</div>}
          {data && <ConvictionCard data={data} />}
          {news && <NewsPanel news={news} />}

          <div className="opps-header">
            <h2>Ranked opportunities</h2>
            <span className="opps-meta">
              {scannedAt
                ? `scanned ${new Date(scannedAt).toLocaleString()}`
                : "not scanned yet"}
            </span>
          </div>
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
            <span className="opps-meta">only shown when the model is sure</span>
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
            Snapshots today&apos;s price + the model&apos;s call. Then tracks it
            and gives you two reads — a crisp signal and an expert view — so you
            decide.
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
    </main>
  );
}
