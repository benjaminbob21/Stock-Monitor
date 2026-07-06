import type { NewsResponse } from "@/lib/types";
import { SentimentMeter } from "@/components/SentimentMeter";

const LABEL_COLORS: Record<string, string> = {
  positive: "var(--green)",
  neutral: "var(--gray)",
  negative: "var(--red)",
};

function sentColor(s: number | null): string {
  if (s === null) return "var(--muted)";
  if (s > 0.15) return "var(--green)";
  if (s < -0.15) return "var(--red)";
  return "var(--muted)";
}

// A words-based sentiment label so meaning never depends on colour alone
// (WCAG 1.4.1) — exposed to screen readers next to each headline.
function sentLabel(s: number | null): string {
  if (s === null) return "sentiment unknown";
  if (s > 0.15) return "positive sentiment";
  if (s < -0.15) return "negative sentiment";
  return "neutral sentiment";
}

export function NewsPanel({ news }: { news: NewsResponse }) {
  const color = LABEL_COLORS[news.label] ?? "var(--gray)";
  return (
    <div className="card">
      <div className="card-top">
        <p className="ticker">News &amp; sentiment</p>
        <div className="score-block">
          <span
            className="rec"
            style={{
              color,
              background: `${color}22`,
              border: `1px solid ${color}55`,
            }}
          >
            {news.label} ({news.score >= 0 ? "+" : ""}
            {news.score.toFixed(2)})
          </span>
        </div>
      </div>

      <SentimentMeter score={news.score} label={news.label} />

      {news.items.length === 0 ? (
        <p className="hint">No recent headlines found.</p>
      ) : (
        <div className="newslist">
          {news.items.slice(0, 8).map((it, i) => (
            <a
              key={i}
              className="newsitem"
              href={it.url || "#"}
              target="_blank"
              rel="noreferrer"
            >
              <span
                className="newsdot"
                style={{ background: sentColor(it.sentiment) }}
                aria-hidden="true"
              />
              <span className="sr-only">{sentLabel(it.sentiment)}. </span>
              <span className="newshead">{it.headline}</span>
              <span className="newssrc">{it.source}</span>
            </a>
          ))}
        </div>
      )}
      <p className="disclaimer">
        Live news sentiment ({news.backend}). Informational overlay + exit
        trigger — not part of the trained score (no historical news to learn
        from).
      </p>
    </div>
  );
}
