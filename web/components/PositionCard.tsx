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

// Calendar-aware holding length between two dates, broken into whole
// years / months / days (so "how long have I held this" needs no mental math).
// Zero-valued leading units are dropped; a same-day span reads "0d".
function holdingDuration(from: string, to: Date): string {
  const start = new Date(from);
  if (Number.isNaN(start.getTime())) return "—";

  let years = to.getFullYear() - start.getFullYear();
  let months = to.getMonth() - start.getMonth();
  let days = to.getDate() - start.getDate();

  if (days < 0) {
    months -= 1;
    // Number of days in the month just before `to`.
    days += new Date(to.getFullYear(), to.getMonth(), 0).getDate();
  }
  if (months < 0) {
    years -= 1;
    months += 12;
  }

  const parts: string[] = [];
  if (years > 0) parts.push(`${years}y`);
  if (months > 0) parts.push(`${months}mo`);
  if (days > 0) parts.push(`${days}d`);
  return parts.length ? parts.join(" ") : "0d";
}

// Diverging profit/loss bar: grows right (green) when up, left (red) when down,
// from a centre "break-even" axis. Saturates at ±25% so small moves stay
// readable. Colour is backed by the signed % text, never colour alone.
function PLBar({ pct }: { pct: number }) {
  const up = pct >= 0;
  const width = Math.min(Math.abs(pct) / 0.25, 1) * 50;
  return (
    <div className="plbar" aria-hidden="true">
      <span className="plbar-zero" />
      <span
        className={`plbar-fill ${up ? "pos" : "neg"}`}
        style={{ width: `${width}%` }}
      />
    </div>
  );
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
          <span className="poslabel">
            price{" "}
            {p.price_is_live && !sold && (
              <span className="pos-live" title="Live price (updates when you refresh)">
                <span className="pos-live-dot" aria-hidden />
                live
              </span>
            )}
          </span>
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
        {(p.quantity ?? 1) !== 1 && (
          <div>
            <span className="poslabel">position</span>
            <span className="posval">
              {p.quantity} sh · {money(p.market_value)}
              {p.pnl_dollar !== undefined && (
                <em style={{ color: p.pnl_dollar >= 0 ? "var(--green)" : "var(--red)" }}>
                  {" "}
                  {p.pnl_dollar >= 0 ? "+" : "−"}${Math.abs(p.pnl_dollar).toFixed(2)}
                </em>
              )}
            </span>
          </div>
        )}
      </div>

      {p.price_change_pct !== null && p.price_change_pct !== undefined && (
        <PLBar pct={p.price_change_pct} />
      )}

      <p className="posexpert">{p.expert_view}</p>
      <div className="posmeta">
        added {p.added_at ? new Date(p.added_at).toLocaleDateString() : "—"}
        {sold && p.sold_at
          ? ` · sold ${new Date(p.sold_at).toLocaleDateString()}`
          : ""}
        {p.added_at
          ? ` · held ${holdingDuration(
              p.added_at,
              sold && p.sold_at ? new Date(p.sold_at) : new Date(),
            )}`
          : ""}
      </div>
    </div>
  );
}
