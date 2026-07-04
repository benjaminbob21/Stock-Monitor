import type { PositionView } from "@/lib/types";

const SIGNAL_COLORS: Record<string, string> = {
  hold: "var(--green)",
  "consider trimming / watch": "var(--orange)",
  "consider selling": "var(--red)",
  sold: "var(--gray)",
  unavailable: "var(--gray)",
};

function pct(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  return `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
}

function money(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `$${x.toFixed(2)}`;
}

export function PositionCard({
  p,
  onSell,
  onLookup,
}: {
  p: PositionView;
  onSell: (id: string) => void;
  onLookup: (ticker: string) => void;
}) {
  const color = SIGNAL_COLORS[p.signal] ?? "var(--gray)";
  const sold = p.status === "sold";
  const priceColor =
    (p.price_change_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)";

  return (
    <div
      className="poscard"
      style={{ borderColor: sold ? "var(--border)" : `${color}55` }}
    >
      <div className="posrow">
        <button
          className="oppticker linklike"
          onClick={() => onLookup(p.ticker)}
        >
          {p.ticker}
        </button>
        <span
          className="possignal"
          style={{
            color,
            background: `${color}22`,
            border: `1px solid ${color}55`,
          }}
        >
          {p.signal}
        </span>
        {!sold && (
          <button className="sellbtn" onClick={() => onSell(p.id)}>
            Mark sold
          </button>
        )}
      </div>

      <div className="posgrid">
        <div>
          <span className="poslabel">price</span>
          <span className="posval">
            {money(p.entry_price)} → {money(p.current_price)}{" "}
            <em style={{ color: priceColor }}>{pct(p.price_change_pct)}</em>
          </span>
        </div>
        <div>
          <span className="poslabel">conviction</span>
          <span className="posval">
            {p.entry_conviction} → {p.current_conviction ?? "—"}
            {p.conviction_change !== undefined && (
              <em
                style={{
                  color:
                    p.conviction_change >= 0 ? "var(--green)" : "var(--red)",
                }}
              >
                {" "}
                {p.conviction_change >= 0 ? "+" : ""}
                {p.conviction_change}
              </em>
            )}
          </span>
        </div>
        {sold && (
          <div>
            <span className="poslabel">since you sold</span>
            <span className="posval">
              sold {money(p.sold_price)} · now {money(p.current_price)}{" "}
              <em>{pct(p.since_sold_pct)}</em>
            </span>
          </div>
        )}
      </div>

      <p className="posexpert">{p.expert_view}</p>
      <div className="posmeta">
        added {p.added_at ? new Date(p.added_at).toLocaleDateString() : "—"}
        {sold && p.sold_at
          ? ` · sold ${new Date(p.sold_at).toLocaleDateString()}`
          : ""}
      </div>
    </div>
  );
}
