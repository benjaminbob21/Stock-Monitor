import { useState } from "react";

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
  onDelete,
  onLookup,
  onBuyMore,
}: {
  p: PositionView;
  onSell: (id: string) => void;
  onDelete?: (id: string) => void;
  onLookup: (ticker: string) => void;
  onBuyMore?: (id: string, params: { shares?: number; dollars?: number; note?: string }) => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [buyOpen, setBuyOpen] = useState(false);
  const [buyMode, setBuyMode] = useState<"dollars" | "shares">("dollars");
  const [buyAmount, setBuyAmount] = useState("");
  const [buyNote, setBuyNote] = useState("");
  const [buyError, setBuyError] = useState<string | null>(null);
  const color = SIGNAL_COLORS[p.signal] ?? "var(--gray)";
  const sold = p.status === "sold";
  const priceColor =
    (p.price_change_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)";

  const submitBuy = () => {
    const raw = Number(buyAmount);
    if (!Number.isFinite(raw) || raw <= 0) {
      setBuyError("Enter a positive amount");
      return;
    }
    const shares =
      buyMode === "shares"
        ? raw
        : undefined;
    const dollars =
      buyMode === "dollars"
        ? raw
        : undefined;
    setBuyError(null);
    onBuyMore?.(p.id, {
      shares,
      dollars,
      note: buyNote.trim() || undefined,
    });
    setBuyOpen(false);
    setBuyAmount("");
    setBuyNote("");
  };

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
        {(!sold || onDelete) && (
          <div className="posmenu-wrap">
            <button
              type="button"
              className="sellbtn posmenu-btn"
              onClick={() => setMenuOpen((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label={`${p.ticker} actions`}
            >
              ⋯
            </button>
            {menuOpen && (
              <div className="posmenu" role="menu">
                {!sold && (
                  <>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        setBuyOpen(true);
                      }}
                    >
                      Buy more…
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        onSell(p.id);
                      }}
                    >
                      Mark sold
                    </button>
                  </>
                )}
                {onDelete &&
                  (confirmDelete ? (
                    <>
                      <button
                        type="button"
                        role="menuitem"
                        className="posmenu-danger"
                        onClick={() => {
                          setMenuOpen(false);
                          onDelete(p.id);
                        }}
                      >
                        Confirm delete
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => setConfirmDelete(false)}
                      >
                        Keep
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      role="menuitem"
                      className="posmenu-danger"
                      onClick={() => setConfirmDelete(true)}
                    >
                      Delete…
                    </button>
                  ))}
              </div>
            )}
          </div>
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
            <span className="poslabel">
              position
              {p.has_multiple_lots && (
                <span
                  className="pos-live"
                  title={`Blended entry across ${p.lots?.length ?? 0} buys`}
                >
                  {" "}
                  · {p.lots?.length ?? 0} buys
                </span>
              )}
            </span>
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

      {p.has_multiple_lots && !sold && (
        <details className="poslots">
          <summary>buy history ({p.lots?.length ?? 0})</summary>
          <ul>
            {(p.lots ?? []).map((lot, i) => (
              <li key={lot.id}>
                <strong>#{i + 1}</strong> {lot.quantity} sh @ {money(lot.price)}
                {lot.bought_at
                  ? ` · ${new Date(lot.bought_at).toLocaleDateString()}`
                  : ""}
                {lot.pnl_dollar !== undefined && lot.pnl_dollar !== null && (
                  <em style={{ color: lot.pnl_dollar >= 0 ? "var(--green)" : "var(--red)" }}>
                    {" "}
                    {lot.pnl_dollar >= 0 ? "+" : "−"}${Math.abs(lot.pnl_dollar).toFixed(2)}
                  </em>
                )}
                {lot.note ? <span className="poslabel"> · {lot.note}</span> : null}
              </li>
            ))}
          </ul>
        </details>
      )}

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

      {buyOpen && (
        <div
          className="buymore-pop"
          role="dialog"
          aria-label={`Buy more ${p.ticker}`}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="buymore-head">
            <strong>Buy more {p.ticker}</strong>
            <button
              type="button"
              className="posmenu-btn"
              onClick={() => setBuyOpen(false)}
              aria-label="close"
            >
              ✕
            </button>
          </div>
          <div className="buymore-modes">
            <button
              type="button"
              className={buyMode === "dollars" ? "active" : ""}
              onClick={() => setBuyMode("dollars")}
            >
              $
            </button>
            <button
              type="button"
              className={buyMode === "shares" ? "active" : ""}
              onClick={() => setBuyMode("shares")}
            >
              shares
            </button>
          </div>
          <input
            className="buymore-input"
            type="number"
            min="0"
            step="any"
            autoFocus
            placeholder={
              buyMode === "dollars"
                ? "Amount in $ (e.g. 500)"
                : `Shares at ${money(p.current_price ?? p.entry_price)}`
            }
            value={buyAmount}
            onChange={(e) => {
              setBuyAmount(e.target.value);
              setBuyError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitBuy();
            }}
          />
          <input
            className="buymore-input"
            type="text"
            placeholder="Note (optional — e.g. 'bought the dip')"
            value={buyNote}
            onChange={(e) => setBuyNote(e.target.value)}
          />
          {buyMode === "dollars" && Number(buyAmount) > 0 && (
            <div className="poslabel">
              ≈ {(Number(buyAmount) / (p.current_price ?? p.entry_price)).toFixed(4)} sh @{" "}
              {money(p.current_price ?? p.entry_price)}
            </div>
          )}
          {buyError && <div className="buymore-error">{buyError}</div>}
          <button type="button" className="sellbtn" onClick={submitBuy}>
            Record buy
          </button>
          <div className="poslabel">
            Entry price becomes the average across all buys.
          </div>
        </div>
      )}
    </div>
  );
}
