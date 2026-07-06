import type { Driver } from "@/lib/types";
import { prettyFeature } from "@/lib/ui";

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
                {Number.isFinite(d.value) ? d.value.toFixed(3) : "n/a"}
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
