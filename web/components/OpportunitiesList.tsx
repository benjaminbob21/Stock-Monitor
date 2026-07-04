import type { Opportunity } from "@/lib/types";

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
        return (
          <button
            key={o.ticker}
            className="opprow"
            onClick={() => onPick?.(o.ticker)}
            title="Look up this ticker"
          >
            <span className="opprank">#{o.rank}</span>
            <span className="oppticker">{o.ticker}</span>
            <span className="oppscore" style={{ color }}>
              {o.capped_conviction}
              <small>/100</small>
            </span>
            <span className="opprec" style={{ color }}>
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
          </button>
        );
      })}
    </div>
  );
}
