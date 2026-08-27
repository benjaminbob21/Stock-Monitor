"use client";

import { useEffect, useRef, useState } from "react";

import type { SymbolMatch } from "@/lib/types";

interface Leg {
  ticker: string;
  name: string;
  pct: number;
}

function money(x: string): string {
  const n = Number(x);
  return Number.isFinite(n) && n > 0
    ? `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
    : "";
}

/**
 * Step-by-step joint portfolio builder:
 * name → budget → pick stocks via search (each gets a % of the budget),
 * with a live allocation meter. Requires the split to total ~100% before
 * creating; offers a one-tap fix that dumps the missing % on the newest leg.
 */
export function BasketBuilder({
  open,
  onClose,
  onCreate,
  busy,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string, budget: number, tickers: string[], pcts: number[]) => void;
  busy: boolean;
}) {
  // "" while picking the first stock; set once the budget is confirmed.
  const [step, setStep] = useState<"name" | "budget" | "stocks">("name");
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const [legs, setLegs] = useState<Leg[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SymbolMatch[]>([]);
  const [searching, setSearching] = useState(false);
  const [pctDraft, setPctDraft] = useState<string | null>(null);
  const [pctLeg, setPctLeg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queryRef = useRef("");

  useEffect(() => {
    if (open) {
      setStep("name");
      setName("");
      setBudget("");
      setLegs([]);
      setQuery("");
      setResults([]);
      setPctDraft("");
      setError(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    queryRef.current = q;
    if (q.length < 2) {
      setResults([]);
      return;
    }
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const res = await fetch(
          `/api/search?q=${encodeURIComponent(q)}&limit=8`,
        );
        const body = await res.json();
        // Ignore stale responses after rapid typing.
        if (queryRef.current === q) setResults(body.results ?? []);
      } catch {
        if (queryRef.current === q) setResults([]);
      } finally {
        if (queryRef.current === q) setSearching(false);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [query, open]);

  const totalPct = legs.reduce((s, l) => s + l.pct, 0);
  const allocated = Math.min(totalPct, 100);

  const addLeg = (match: SymbolMatch) => {
    const ticker = match.ticker.toUpperCase();
    if (legs.some((l) => l.ticker === ticker)) {
      setError(`${ticker} is already in this portfolio`);
      return;
    }
    setError(null);
    setLegs([...legs, { ticker, name: match.name, pct: 0 }]);
    setQuery("");
    setResults([]);
  };

  const removeLeg = (ticker: string) => {
    setLegs(legs.filter((l) => l.ticker !== ticker));
  };

  const setLegPct = (ticker: string, pct: number) => {
    setLegs(legs.map((l) => (l.ticker === ticker ? { ...l, pct } : l)));
  };

  // One-tap equalizer: top up the newest leg so the split hits exactly 100%.
  const canFix = totalPct > 0 && totalPct <= 100;
  const applyFix = () => {
    if (!canFix || legs.length === 0) return;
    const last = legs[legs.length - 1].ticker;
    setLegs(legs.map((l) => (l.ticker === last ? { ...l, pct: l.pct + (100 - totalPct) } : l)));
    setError(null);
  };

  const submit = () => {
    if (!budgetTrimmed() || legs.length === 0) return;
    if (Math.round(totalPct) !== 100) {
      setError(`allocation is ${totalPct}% — must total 100%`);
      return;
    }
    setError(null);
    onCreate(name.trim(), Number(budget), legs.map((l) => l.ticker), legs.map((l) => l.pct));
  };

  const budgetTrimmed = () => {
    const n = Number(budget);
    return Number.isFinite(n) && n > 0 ? budget : "";
  };

  if (!open) return null;

  return (
    <div className="sheet-overlay" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="basketbuilder" onClick={(e) => e.stopPropagation()}>
        <div className="bbhead">
          <h2>New joint portfolio</h2>
          <button className="sellbtn" onClick={onClose}>
            Cancel
          </button>
        </div>

        {step === "name" && (
          <>
            <p className="hint">Name it something you&apos;ll recognise.</p>
            <input
              className="bbin"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && setStep("budget")}
              placeholder="e.g. Tech core, Dividend plays…"
              aria-label="Portfolio name"
            />
            <button
              className="bbnext"
              onClick={() => setStep("budget")}
            >
              Next
            </button>
          </>
        )}

        {step === "budget" && (
          <>
            <p className="hint">
              How much capital goes into this whole portfolio?
              {name ? ` "${name}"` : ""}
            </p>
            <input
              className="bbin"
              autoFocus
              value={budget}
              inputMode="decimal"
              onChange={(e) => setBudget(e.target.value.replace(/[^0-9.]/g, ""))}
              onKeyDown={(e) => e.key === "Enter" && budgetTrimmed() && setStep("stocks")}
              placeholder="Total budget in $"
              aria-label="Total budget"
            />
            {budget && !budgetTrimmed() && (
              <p className="hint" style={{ color: "var(--red)" }}>
                enter an amount above zero
              </p>
            )}
            <button
              className="bbnext"
              disabled={!budgetTrimmed()}
              onClick={() => setStep("stocks")}
            >
              Add stocks
            </button>
          </>
        )}

        {step === "stocks" && (
          <>
            <div className="bbmeter-row">
              <span className="poslabel">allocated</span>
              <span
                className="posval"
                style={{ color: Math.round(totalPct) === 100 ? "var(--green)" : "var(--text)" }}
              >
                {totalPct}% / 100%
              </span>
            </div>
            <div className="bbmeter" role="progressbar" aria-valuenow={totalPct} aria-valuemin={0} aria-valuemax={100}>
              <span style={{ width: `${Math.min(allocated, 100)}%` }} />
            </div>

            <div className="bblegs">
              {legs.map((leg) => (
                <div key={leg.ticker} className="bbleg">
                  <div className="bbleg-id">
                    <strong>{leg.ticker}</strong>
                    <span className="poslabel">{leg.name}</span>
                  </div>
                  <div className="bbleg-pct">
                    <input
                      value={pctLeg === leg.ticker ? pctDraft ?? String(leg.pct) : String(leg.pct)}
                      inputMode="decimal"
                      onFocus={() => {
                        setPctLeg(leg.ticker);
                        setPctDraft(String(leg.pct));
                      }}
                      onChange={(e) => {
                        const v = e.target.value.replace(/[^0-9.]/g, "");
                        setPctDraft(v);
                        const n = Number(v);
                        if (Number.isFinite(n)) setLegPct(leg.ticker, Math.max(0, Math.min(100, n)));
                      }}
                      onBlur={() => {
                        setPctLeg(null);
                        setPctDraft(null);
                      }}
                      aria-label={`${leg.ticker} percentage`}
                    />
                    <span>%</span>
                  </div>
                  <button
                    className="sellbtn bbremove"
                    onClick={() => removeLeg(leg.ticker)}
                    aria-label={`Remove ${leg.ticker}`}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>

            <input
              className="bbin"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={legs.length ? "Add another stock…" : "Search for your first stock…"}
              aria-label="Search stocks to add"
            />
            {searching && <p className="hint">searching…</p>}
            {results.length > 0 && (
              <div className="bbresults">
                {results.map((r) => (
                  <button key={r.ticker} onClick={() => addLeg(r)}>
                    <strong>{r.ticker}</strong> <span>{r.name}</span>
                  </button>
                ))}
              </div>
            )}
            {!searching &&
              query.trim().length >= 2 &&
              results.length === 0 &&
              !legs.some((l) => l.ticker === query.trim().toUpperCase()) && (
                <p className="hint">no matches for “{query.trim()}”</p>
              )}

            {error && (
              <p className="status" style={{ color: "var(--red)" }}>
                {error}
              </p>
            )}

            <div className="bbactions">
              {/* Locked until the split is exactly 100%. */}
              <button
                className="bbnext"
                disabled={
                  busy ||
                  legs.length === 0 ||
                  Number(budgetTrimmed()) <= 0 ||
                  Math.round(totalPct) !== 100
                }
                onClick={submit}
              >
                {busy
                  ? "Creating…"
                  : `Create${name ? ` “${name}”` : ""} · ${money(budget)} · ${legs.length} stock${legs.length === 1 ? "" : "s"}`}
              </button>
              {/* Only appears while under 100%; tops up the newest leg. */}
              {canFix && Math.round(totalPct) < 100 && legs.length > 0 && (
                <button className="sellbtn bbequate" onClick={applyFix}>
                  Equate to 100%
                  <span className="bbequate-hint">
                    +{100 - totalPct}% → {legs[legs.length - 1]?.ticker}
                  </span>
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
