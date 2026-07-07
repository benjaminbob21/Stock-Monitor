"use client";

import { AnalystCard } from "@/components/AnalystCard";
import { ConvictionCard } from "@/components/ConvictionCard";
import { NewsPanel } from "@/components/NewsPanel";
import { PriceChart } from "@/components/PriceChart";
import type { AnalystResponse, NewsResponse, ScoreResponse } from "@/lib/types";

export function StockDetailSheet({
  ticker,
  data,
  news,
  analyst,
  loading,
  error,
  onClose,
  onAdd,
  adding = false,
  tracked = false,
}: {
  ticker: string;
  data: ScoreResponse | null;
  news: NewsResponse | null;
  analyst: AnalystResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onAdd?: () => void;
  adding?: boolean;
  tracked?: boolean;
}) {
  return (
    <div
      className="sheet"
      role="dialog"
      aria-modal="true"
      aria-label={`${ticker} analysis`}
    >
      <header className="sheet-bar">
        <button
          type="button"
          className="sheet-back"
          onClick={onClose}
          aria-label="Back to list"
        >
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.1}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m15 18-6-6 6-6" />
          </svg>
        </button>
        <span className="sheet-title">{ticker || "Analysis"}</span>
        {onAdd && data ? (
          <button
            type="button"
            className={`sheet-add ${tracked ? "tracked" : ""}`}
            onClick={onAdd}
            disabled={adding || tracked}
            aria-label={
              tracked
                ? `${ticker} is already in your portfolio`
                : `Add ${ticker} to your portfolio`
            }
          >
            {tracked ? (
              <>
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2.6}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M20 6 9 17l-5-5" />
                </svg>
                Tracked
              </>
            ) : adding ? (
              "Adding…"
            ) : (
              <>
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2.6}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M12 5v14M5 12h14" />
                </svg>
                Add
              </>
            )}
          </button>
        ) : (
          <span className="sheet-spacer" aria-hidden />
        )}
      </header>

      <div className="sheet-body">
        {loading && <div className="status">Scoring {ticker}…</div>}
        {error && <div className="status error">{error}</div>}
        {data && <ConvictionCard data={data} />}
        {data && ticker && <PriceChart ticker={ticker} />}
        {data && <AnalystCard analyst={analyst} />}
        {news && <NewsPanel news={news} />}
      </div>
    </div>
  );
}
