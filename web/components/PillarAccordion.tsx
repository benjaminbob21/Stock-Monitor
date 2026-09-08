import type { PillarScore } from "@/lib/types";
import { prettyFeature } from "@/lib/ui";
import { useState } from "react";

const PILLAR_ORDER = ["Momentum", "Technical", "Quality", "Value", "Sentiment"];

const FORMAT: Record<string, (v: number) => string> = {
  mom_12_1: (v) => `${(v * 100).toFixed(1)}%`,
  mom_6_1: (v) => `${(v * 100).toFixed(1)}%`,
  vol_3m: (v) => `${(v * 100).toFixed(1)}%`,
  rsi_14: (v) => v.toFixed(0),
  trend_200: (v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`,
  roe: (v) => `${(v * 100).toFixed(1)}%`,
  debt_ratio: (v) => `${(v * 100).toFixed(1)}%`,
  profit_margin: (v) => `${(v * 100).toFixed(1)}%`,
  earnings_yield: (v) => `${(v * 100).toFixed(2)}%`,
  fcf_yield: (v) => `${(v * 100).toFixed(2)}%`,
  sentiment: (v) => v.toFixed(2),
};

function fmtVal(feature: string, value: number): string {
  if (!Number.isFinite(value)) return "n/a";
  return FORMAT[feature]?.(value) ?? value.toFixed(3);
}

export function PillarAccordion({
  pillars,
}: {
  pillars: Record<string, PillarScore>;
}) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div className="pillars">
      {PILLAR_ORDER.map((name) => {
        const p = pillars[name];
        if (!p) return null;
        const isOpen = open === name;
        const barWidth = Math.max(0, Math.min(100, p.score));

        return (
          <div className="pillar" key={name}>
            <button
              className="pillar-header"
              onClick={() => setOpen(isOpen ? null : name)}
              aria-expanded={isOpen}
              type="button"
            >
              <span className="pillar-toggle">{isOpen ? "▼" : "▶"}</span>
              <span className="pillar-name">{name}</span>
              <span className="pillar-score">{p.score}</span>
              <span className="pillar-bar-track">
                <span
                  className="pillar-bar-fill"
                  style={{
                    width: `${barWidth}%`,
                    background:
                      p.score >= 60
                        ? "var(--green, #22c55e)"
                        : p.score >= 40
                          ? "var(--gray, #9ca3af)"
                          : "var(--red, #ef4444)",
                  }}
                />
              </span>
            </button>
            {isOpen && (
              <div className="pillar-details">
                {p.features.map((d) => (
                  <div className="pillar-feat" key={d.feature}>
                    <span
                      className={`pillar-dir ${d.direction === "+" ? "pos" : "neg"}`}
                    >
                      {d.direction === "+" ? "▲" : "▼"}
                    </span>
                    <span className="pillar-feat-name">
                      {prettyFeature(d.feature)}
                    </span>
                    <span className="pillar-feat-val">
                      {fmtVal(d.feature, d.value)}
                    </span>
                    <span
                      className={`pillar-feat-shap ${d.direction === "+" ? "pos" : "neg"}`}
                    >
                      {d.shap >= 0 ? "+" : ""}
                      {d.shap.toFixed(3)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
