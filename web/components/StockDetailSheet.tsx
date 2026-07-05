"use client";

import { ConvictionCard } from "@/components/ConvictionCard";
import { NewsPanel } from "@/components/NewsPanel";
import type { NewsResponse, ScoreResponse } from "@/lib/types";

export function StockDetailSheet({
  ticker,
  data,
  news,
  loading,
  error,
  onClose,
}: {
  ticker: string;
  data: ScoreResponse | null;
  news: NewsResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label={`${ticker} analysis`}>
      <header className="sheet-bar">
        <button type="button" className="sheet-back" onClick={onClose} aria-label="Back to list">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 18-6-6 6-6" />
          </svg>
        </button>
        <span className="sheet-title">{ticker || "Analysis"}</span>
        <span className="sheet-spacer" aria-hidden />
      </header>

      <div className="sheet-body">
        {loading && (
          <div className="status">Scoring {ticker}…</div>
        )}
        {error && <div className="status error">{error}</div>}
        {data && <ConvictionCard data={data} />}
        {news && <NewsPanel news={news} />}
      </div>
    </div>
  );
}
