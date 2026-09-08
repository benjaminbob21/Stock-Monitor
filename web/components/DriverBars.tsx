import type { Driver } from "@/lib/types";
import { prettyFeature } from "@/lib/ui";

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

function formatValue(feature: string, value: number): string {
  if (!Number.isFinite(value)) return "n/a";
  const fmt = FORMAT[feature];
  return fmt ? fmt(value) : value.toFixed(3);
}


// SHAP contribution chart — a diverging bar per factor. Bars grow right (green,
// pushing conviction up) or left (red, pulling it down) from a centre axis,
// scaled to the strongest driver so relative weight is obvious. The arrow +
// signed value keep it readable without relying on colour (WCAG 1.4.1).
export function DriverBars({ drivers }: { drivers: Driver[] }) {
  const max = Math.max(
    ...drivers.map((d) => (Number.isFinite(d.shap) ? Math.abs(d.shap) : 0)),
    1e-6,
  );

  return (
    <div className="dbars">
      {drivers.map((d) => {
        // `direction` is derived from the SHAP sign on the backend; read the
        // sign directly so the arrow/bar stay correct regardless of format.
        const pos = Number.isFinite(d.shap) ? d.shap >= 0 : d.direction === "+";
        const mag = Number.isFinite(d.shap) ? Math.abs(d.shap) : 0;
        const width = (mag / max) * 50; // half-track max
        return (
          <div className="dbar" key={d.feature}>
            <div className="dbar-head">
              <span
                className={`dbar-dir ${pos ? "pos" : "neg"}`}
                aria-hidden="true"
              >
                {pos ? "▲" : "▼"}
              </span>
              <span className="dbar-feat">{prettyFeature(d.feature)}</span>
              <span className="dbar-val">
                {formatValue(d.feature, d.value)}
              </span>
              <span className={`dbar-shap ${pos ? "pos" : "neg"}`}>
                {d.shap >= 0 ? "+" : ""}
                {Number.isFinite(d.shap) ? d.shap.toFixed(3) : "n/a"}
              </span>
            </div>
            <div className="dbar-track">
              <span className="dbar-zero" aria-hidden="true" />
              <span
                className={`dbar-fill ${pos ? "pos" : "neg"}`}
                style={{ width: `${width}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
