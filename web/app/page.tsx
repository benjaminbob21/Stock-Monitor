"use client";

import { useState } from "react";
import { ConvictionCard } from "@/components/ConvictionCard";
import type { ApiError, ScoreResponse } from "@/lib/types";

export default function Home() {
  const [ticker, setTicker] = useState("");
  const [data, setData] = useState<ScoreResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function lookup(event: React.FormEvent) {
    event.preventDefault();
    const symbol = ticker.trim().toUpperCase();
    if (!symbol) return;

    setLoading(true);
    setError(null);
    setData(null);

    try {
      const res = await fetch(`/api/score/${encodeURIComponent(symbol)}`);
      const body = (await res.json()) as ScoreResponse | ApiError;
      if (!res.ok) {
        setError((body as ApiError).detail ?? `request failed (${res.status})`);
      } else {
        setData(body as ScoreResponse);
      }
    } catch {
      setError("could not reach the scoring service");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="container">
      <div className="header">
        <h1>Stock-Monitor</h1>
        <p>Explainable conviction scoring — you execute every trade. No auto-trading.</p>
      </div>

      <form className="searchbar" onSubmit={lookup}>
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="Enter a ticker (e.g. AAPL)"
          aria-label="Ticker symbol"
          autoFocus
        />
        <button type="submit" disabled={loading || !ticker.trim()}>
          {loading ? "Scoring…" : "Score"}
        </button>
      </form>
      <p className="hint">
        Looks up a point-in-time feature row and returns a conviction score with its
        top SHAP drivers and risk flags.
      </p>

      {error && <div className="status error">{error}</div>}
      {data && <ConvictionCard data={data} />}
    </main>
  );
}
