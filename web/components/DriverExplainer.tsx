import type { Driver } from "@/lib/types";
import { driverLead, explainDrivers } from "@/lib/drivers";

// Plain-English companion to the SHAP bars: a one-line "gist" plus a sentence
// per driver, so the rationale is readable without knowing finance jargon.
export function DriverExplainer({ drivers }: { drivers: Driver[] }) {
  if (!drivers || drivers.length === 0) return null;
  const lead = driverLead(drivers);
  const items = explainDrivers(drivers);

  return (
    <div className="dexplain">
      {lead && (
        <p className="dexplain-lead">
          <span className="dexplain-gist">In plain English</span> {lead}
        </p>
      )}
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
