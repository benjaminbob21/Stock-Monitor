import type { Driver } from "@/lib/types";
import { explainDrivers } from "@/lib/drivers";

// Plain-English companion to the SHAP bars: one sentence per driver that says what
// the term means and which way it pushed the score — so the rationale is readable
// without knowing finance jargon. (The flowing AI narrative lives in PlainSummaryCard.)
export function DriverExplainer({ drivers }: { drivers: Driver[] }) {
  if (!drivers || drivers.length === 0) return null;
  const items = explainDrivers(drivers);

  return (
    <div className="dexplain">
      <p className="dexplain-title">
        <span className="dexplain-gist">In plain English</span>
        what each driver means
      </p>
      <ul className="dexplain-list">
        {items.map((it) => (
          <li className="dexplain-item" key={it.feature}>
            <span
              className={`dexplain-dir ${it.pos ? "pos" : "neg"}`}
              aria-hidden="true"
            >
              {it.pos ? "▲" : "▼"}
            </span>
            <span className="dexplain-text">
              <b>{it.label}</b> — {it.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
