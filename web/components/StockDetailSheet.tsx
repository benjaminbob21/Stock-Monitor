"use client";

import { useState } from "react";

import { AnalystCard } from "@/components/AnalystCard";
import { ConvictionCard } from "@/components/ConvictionCard";
import { DcfCard } from "@/components/DcfCard";
import { NewsPanel } from "@/components/NewsPanel";
import { PlainSummaryCard } from "@/components/PlainSummaryCard";
import { PriceChart } from "@/components/PriceChart";
import type {
  AnalystResponse,
  ExplainResponse,
  NewsResponse,
  ScoreResponse,
} from "@/lib/types";

export function StockDetailSheet({
  ticker,
  data,
  news,
  analyst,
  explain,
  explainLoading = false,
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
  explain: ExplainResponse | null;
  explainLoading?: boolean;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onAdd?: (quantity: number) => void;
  adding?: boolean;
  tracked?: boolean;
}) {
  const [qtyOpen, setQtyOpen] = useState(false);
  const [qtyMode, setQtyMode] = useState<"shares" | "dollars">("shares");
  const [qtyValue, setQtyValue] = useState("");

  const price = data?.price ?? null;
  const parsedQty = Number(qtyValue);
  const shares =
    qtyMode === "shares"
      ? parsedQty
      : price != null && Number.isFinite(parsedQty) && parsedQty > 0
        ? parsedQty / price
        : NaN;
  const qtyValid = Number.isFinite(shares) && shares > 0;
  const qtyPreview =
    qtyValid && price != null
      ? qtyMode === "shares"
        ? `≈ $${(shares * price).toFixed(2)} at $${price.toFixed(2)}/sh`
        : `≈ ${shares.toFixed(shares < 10 ? 3 : 1)} shares at $${price.toFixed(2)}/sh`
      : null;

  const confirmAdd = () => {
    if (!qtyValid || !onAdd) return;
    setQtyOpen(false);
    setQtyValue("");
    onAdd(Number(shares.toFixed(6)));
  };
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
          <div className="sheet-add-wrap">
            {qtyOpen && (
              <div className="qty-pop" role="group" aria-label="Choose amount to track">
                <div className="qty-modes">
                  <button
                    type="button"
                    className={qtyMode === "shares" ? "on" : ""}
                    onClick={() => setQtyMode("shares")}
                  >
                    Shares
                  </button>
                  <button
                    type="button"
                    className={qtyMode === "dollars" ? "on" : ""}
                    onClick={() => setQtyMode("dollars")}
                  >
                    Dollars
                  </button>
                </div>
                <input
                  className="qty-input"
                  inputMode="decimal"
                  autoFocus
                  placeholder={qtyMode === "shares" ? "e.g. 5" : "e.g. 500"}
                  value={qtyValue}
                  onChange={(e) => setQtyValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") confirmAdd();
                    if (e.key === "Escape") setQtyOpen(false);
                  }}
                  aria-label={qtyMode === "shares" ? "Number of shares" : "Dollar amount"}
                />
                {qtyPreview && <div className="qty-preview">{qtyPreview}</div>}
                <button
                  type="button"
                  className="qty-confirm"
                  onClick={confirmAdd}
                  disabled={!qtyValid || adding}
                >
                  {adding ? "Adding…" : "Track it"}
                </button>
              </div>
            )}
            <button
              type="button"
              className={`sheet-add ${tracked ? "tracked" : ""}`}
              onClick={() => (tracked ? undefined : setQtyOpen((v) => !v))}
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
          </div>
        ) : (
          <span className="sheet-spacer" aria-hidden />
        )}
      </header>

      <div className="sheet-body">
        {loading && <div className="status">Scoring {ticker}…</div>}
        {error && <div className="status error">{error}</div>}
        {data && <ConvictionCard data={data} />}
        {data && (
          <PlainSummaryCard
            summary={explain?.summary ?? null}
            loading={explainLoading}
            drivers={data.drivers}
          />
        )}
        {data && ticker && (
          <PriceChart
            ticker={ticker}
            livePrice={data.price_is_live ? data.price : undefined}
          />
        )}
        {data && ticker && <DcfCard ticker={ticker} />}
        {data && <AnalystCard analyst={analyst} />}
        {news && <NewsPanel news={news} />}
      </div>
    </div>
  );
}
