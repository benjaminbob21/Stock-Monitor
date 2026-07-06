import type { AnalystResponse } from "@/lib/types";

const OPINION_COLORS: Record<string, string> = {
  BUY: "var(--green)",
  HOLD: "var(--gray)",
  SELL: "var(--red)",
};

export function AnalystCard({ analyst }: { analyst: AnalystResponse | null }) {
  // Disabled (no key / turned off) or the call degraded — say nothing loud.
  if (!analyst || !analyst.opinion) {
    if (analyst?.note) {
      return (
        <div className="card analyst">
          <p className="analyst-head">
            <span className="analyst-badge muted">AI second opinion</span>
          </p>
          <p className="analyst-note">{analyst.note}</p>
        </div>
      );
    }
    return null;
  }

  const op = analyst.opinion;
  const color = OPINION_COLORS[op.opinion] ?? "var(--gray)";

  return (
    <div className="card analyst">
      <p className="analyst-head">
        <span className="analyst-badge">AI second opinion</span>
        <span
          className="analyst-verdict"
          style={{ color, background: `${color}22`, border: `1px solid ${color}55` }}
        >
          {op.opinion}
        </span>
        <span className="analyst-conf">{op.confidence} confidence</span>
        <span className={`analyst-agree ${op.agrees_with_model ? "agree" : "disagree"}`}>
          {op.agrees_with_model ? "agrees with model" : "pushes back on model"}
        </span>
      </p>

      {op.rationale && <p className="analyst-rationale">{op.rationale}</p>}

      {op.key_risks.length > 0 && (
        <>
          <p className="section-label">Risks it sees</p>
          <div className="flags">
            {op.key_risks.map((r) => (
              <span className="flag" key={r}>
                {r}
              </span>
            ))}
          </div>
        </>
      )}

      <p className="analyst-meta">{op.model}</p>
      <p className="disclaimer">{op.disclaimer}</p>
    </div>
  );
}
