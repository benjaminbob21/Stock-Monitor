"use client";

import { useState } from "react";

import type { BasketLeg, BasketView } from "@/lib/types";

function money(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `$${x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function signed(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined) return "—";
  return `${x >= 0 ? "+" : ""}${x.toFixed(digits)}`;
}

const retColor = (x: number | null | undefined): string =>
  (x ?? 0) >= 0 ? "var(--green)" : "var(--red)";

// Same diverging bar idiom as PositionCard: grows right (green) when up,
// left (red) when down, from a centre break-even axis. Saturates at ±25%.
function PLBar({ pct }: { pct: number }) {
  const up = pct >= 0;
  const width = Math.min(Math.abs(pct) / 25, 1) * 50;
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

export function BasketCard({
  basket,
  onClose,
  onBuyLeg,
}: {
  basket: BasketView;
  onClose: (id: string) => void;
  onBuyLeg?: (legId: string, params: { shares?: number; dollars?: number; note?: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [buyLegId, setBuyLegId] = useState<string | null>(null);
  const [buyMode, setBuyMode] = useState<"dollars" | "shares">("dollars");
  const [buyAmount, setBuyAmount] = useState("");
  const [buyError, setBuyError] = useState<string | null>(null);
  const ret = basket.return_pct;
  const bench = basket.benchmark_return_pct;

  const submitLegBuy = (leg: BasketLeg) => {
    const raw = Number(buyAmount);
    if (!Number.isFinite(raw) || raw <= 0) {
      setBuyError("Enter a positive amount");
      return;
    }
    setBuyError(null);
    onBuyLeg?.(leg.id, {
      shares: buyMode === "shares" ? raw : undefined,
      dollars: buyMode === "dollars" ? raw : undefined,
    });
    setBuyLegId(null);
    setBuyAmount("");
  };

  return (
    <div
      className="poscard"
      style={{ borderColor: `${retColor(ret)}55` }}
    >
      <button className="baskethdr" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="linklike">{basket.name}</span>
        <span className="posval" style={{ color: retColor(ret), fontWeight: 800 }}>
          {signed(ret)}%
        </span>
        <span className="poslabel baskettoggle">{open ? "▲ hide stocks" : "▼ per-stock"}</span>
      </button>

      {/* Whole-capital headline */}
      <div className="posgrid">
        <div>
          <span className="poslabel">capital</span>
          <span className="posval">
            {money(basket.total_budget)} → {money(basket.current_value)}
          </span>
        </div>
        <div>
          <span className="poslabel">p&l</span>
          <span className="posval" style={{ color: retColor(basket.pnl), fontWeight: 700 }}>
            {basket.pnl !== null && basket.pnl !== undefined && basket.pnl >= 0 ? "+" : ""}
            {money(basket.pnl)}
          </span>
        </div>
        <div>
          <span className="poslabel">vs SPY same budget</span>
          <span className="posval">
            {bench === null || bench === undefined
              ? "—"
              : `SPY ${signed(bench)}% · excess `}
            <em style={{ color: retColor(basket.excess_vs_spy_pct) }}>
              {bench === null || bench === undefined ? "" : signed(basket.excess_vs_spy_pct)}
            </em>
          </span>
        </div>
        {!basket.complete && (
          <div>
            <span className="poslabel">pricing</span>
            <span className="posval">some legs unpriced</span>
          </div>
        )}
      </div>

      {ret !== null && ret !== undefined && <PLBar pct={ret} />}

      {open && (
        <table className="basketlegs">
          <thead>
            <tr>
              <th>stock</th>
              <th>% split</th>
              <th>entry → now</th>
              <th>leg p&amp;l</th>
              <th>contribution</th>
              {basket.status === "open" && <th aria-label="close action" />}
            </tr>
          </thead>
          <tbody>
            {basket.legs.map((leg: BasketLeg) => (
              <tr key={leg.id} style={{ opacity: leg.status === "sold" ? 0.55 : 1 }}>
                <td className="legticker">
                  {leg.ticker}
                  {(leg.lot_count ?? 0) > 1 && (
                    <span
                      className="poslabel"
                      title={`Blended entry across ${leg.lot_count} buys`}
                    >
                      {" "}
                      ·{leg.lot_count} buys
                    </span>
                  )}
                </td>
                <td>{leg.pct}%</td>
                <td>
                  ${leg.entry_price.toFixed(2)} →{" "}
                  {leg.current_price != null ? `$${Number(leg.current_price).toFixed(2)}` : "—"}{" "}
                  <em style={{ color: retColor(leg.leg_return_pct) }}>
                    {signed(leg.leg_return_pct, 1)}%
                  </em>
                </td>
                <td style={{ color: retColor(leg.pnl) }}>{signed(leg.pnl, 0)}</td>
                <td title="percentage points this stock added to the whole portfolio's move">
                  <strong style={{ color: retColor(leg.contribution_points) }}>
                    {signed(leg.contribution_points)}
                  </strong>{" "}
                  pts
                </td>
                {basket.status === "open" && (
                  <td>
                    {leg.status === "open" && onBuyLeg && (
                      <button
                        type="button"
                        className="sellbtn legbuybtn"
                        onClick={() => {
                          setBuyLegId(buyLegId === leg.id ? null : leg.id);
                          setBuyAmount("");
                          setBuyError(null);
                        }}
                        aria-label={`Buy more ${leg.ticker}`}
                      >
                        + Buy
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {buyLegId && basket.status === "open" && (
        <div className="buymore-pop" role="dialog" aria-label="Buy more of a leg">
          <div className="buymore-head">
            <strong>
              Buy more{" "}
              {basket.legs.find((l) => l.id === buyLegId)?.ticker ?? ""}
            </strong>
            <button
              type="button"
              className="posmenu-btn"
              onClick={() => setBuyLegId(null)}
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
                : "Number of shares"
            }
            value={buyAmount}
            onChange={(e) => {
              setBuyAmount(e.target.value);
              setBuyError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                const leg = basket.legs.find((l) => l.id === buyLegId);
                if (leg) submitLegBuy(leg);
              }
            }}
          />
          {buyError && <div className="buymore-error">{buyError}</div>}
          <button
            type="button"
            className="sellbtn"
            onClick={() => {
              const leg = basket.legs.find((l) => l.id === buyLegId);
              if (leg) submitLegBuy(leg);
            }}
          >
            Record buy
          </button>
          <div className="poslabel">
            Leg entry becomes the average across all buys; portfolio capital grows by
            the cost.
          </div>
        </div>
      )}

      <div className="posmeta">
        created {new Date(basket.created_at).toLocaleDateString()} · {basket.status}
        {basket.status === "open" && (
          <button className="sellbtn basketclosebtn" onClick={() => onClose(basket.id)}>
            Close all
          </button>
        )}
      </div>
    </div>
  );
}
