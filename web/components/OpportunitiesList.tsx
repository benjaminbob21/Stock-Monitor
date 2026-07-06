import type { Opportunity } from "@/lib/types";
import { recTone, toneCaret } from "@/lib/ui";

const REC_COLORS: Record<string, string> = {
  "consider buying": "var(--green)",
  "lean buy / watch": "var(--teal)",
  "hold / neutral": "var(--gray)",
  "lean trim / watch": "var(--orange)",
  "consider trimming / avoid": "var(--red)",
};

function recColor(recommendation: string): string {
  return REC_COLORS[recommendation] ?? "var(--gray)";
}

export function OpportunitiesList({
  items,
  onPick,
}: {
  items: Opportunity[];
  onPick?: (ticker: string) => void;
}) {
  return (
    <div className="opplist">
      {items.map((o) => {
        const color = recColor(o.recommendation);
        const tone = recTone(o.recommendation);
        return (
          <button
            key={o.ticker}
            className="opprow"
            onClick={() => onPick?.(o.ticker)}
            title="Look up this ticker"
            aria-label={`${o.ticker}, rank ${o.rank}, conviction ${o.capped_conviction} out of 100, ${o.recommendation}`}
          >
            <span className="opprank">#{o.rank}</span>
            <span className="oppticker">{o.ticker}</span>
            <span className="oppscore" style={{ color }}>
              {o.capped_conviction}
              <small>/100</small>
            </span>
            <span className="opprec" style={{ color }}>
              <span className={`oppcaret ${tone}`} aria-hidden="true">
                {toneCaret(tone)}
              </span>
              {o.recommendation}
            </span>
            <span className="oppflags">
              {o.risk_flags.length === 0 ? (
                <span className="oppflag none">clean</span>
              ) : (
                o.risk_flags.map((f) => (
                  <span className="oppflag" key={f}>
                    {f.replace(/_cap$/, "").replace(/_/g, " ")}
                  </span>
                ))
              )}
            </span>
            <span
              className="oppbar"
              aria-hidden="true"
              style={{
                width: `${Math.max(0, Math.min(100, o.capped_conviction))}%`,
                background: color,
              }}
            />
          </button>
        );
      })}
    </div>
  );
}
