import type { PillarScore } from "@/lib/types";

const PILLAR_ORDER = ["Momentum", "Technical", "Quality", "Value", "Sentiment"];

export function ScoreWaterfall({
  pillars,
  baseRate = 50,
}: {
  pillars: Record<string, PillarScore>;
  baseRate?: number;
}) {
  const steps: Array<{ name: string; value: number; cumulative: number }> = [];
  let cumulative = baseRate;

  for (const name of PILLAR_ORDER) {
    const p = pillars[name];
    if (!p) continue;
    const contribution = (p.score - 50) / PILLAR_ORDER.length;
    cumulative += contribution;
    steps.push({ name, value: contribution, cumulative });
  }

  const maxAbs = Math.max(...steps.map((s) => Math.abs(s.value)), 5);

  return (
    <div className="waterfall">
      <div className="wf-header">
        <span className="wf-label">Base baseline</span>
        <span className="wf-num">{baseRate}</span>
      </div>
      {steps.map((s) => {
        const pos = s.value >= 0;
        const barWidth = (Math.abs(s.value) / maxAbs) * 40;
        return (
          <div className="wf-row" key={s.name}>
            <span className="wf-label">{s.name}</span>
            <span className="wf-bar-track">
              <span className="wf-zero" />
              <span
                className={`wf-bar-fill ${pos ? "pos" : "neg"}`}
                style={{ width: `${barWidth}%` }}
              />
            </span>
            <span className={`wf-delta ${pos ? "pos" : "neg"}`}>
              {pos ? "+" : ""}
              {s.value.toFixed(1)}
            </span>
          </div>
        );
      })}
      <div className="wf-footer">
        <span className="wf-label">Model conviction</span>
        <span className="wf-num">{Math.round(cumulative)}</span>
      </div>
    </div>
  );
}
