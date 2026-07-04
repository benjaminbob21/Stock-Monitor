import type { ScoreResponse } from "@/lib/types";

const REC_COLORS: Record<string, string> = {
  "consider buying": "var(--green)",
  "lean buy / watch": "var(--teal)",
  "hold / neutral": "var(--gray)",
  "lean trim / watch": "var(--orange)",
  "consider trimming / avoid": "var(--red)",
};

function recColor(recommendation: string): string {
  return REC_COLORS[recommendation] ?? "var(--gray)";
}

export function ConvictionCard({ data }: { data: ScoreResponse }) {
  const color = recColor(data.recommendation);

  return (
    <div className="card">
      <div className="card-top">
        <div>
          <p className="ticker">{data.ticker}</p>
          <p className="asof">as of {data.as_of}</p>
        </div>
        <div className="score-block">
          <div className="score" style={{ color }}>
            {data.conviction}
            <span>/100</span>
          </div>
          <span
            className="rec"
            style={{ color, background: `${color}22`, border: `1px solid ${color}55` }}
          >
            {data.recommendation}
          </span>
        </div>
      </div>

      <p className="section-label">Top drivers (SHAP)</p>
      {data.drivers.map((d) => (
        <div className="driver" key={d.feature}>
          <span className={`dir ${d.direction === "+" ? "pos" : "neg"}`}>
            {d.direction}
          </span>
          <span className="feat">{d.feature}</span>
          <span className="num">
            value {d.value.toFixed(4)} · shap {d.shap.toFixed(3)}
          </span>
        </div>
      ))}

      <p className="section-label">Risk flags</p>
      <div className="flags">
        {data.risk_flags.length === 0 ? (
          <span className="flag none">no risk flags</span>
        ) : (
          data.risk_flags.map((f) => (
            <span className="flag" key={f}>
              {f}
            </span>
          ))
        )}
      </div>

      <div className="meta">
        <span>
          fundamentals known-on:{" "}
          {data.fundamentals_known_on ?? "n/a (no PIT fundamentals)"}
        </span>
        <span>
          {data.calibrated ? "calibrated" : "uncalibrated"} · {data.model_version}
        </span>
      </div>

      <p className="disclaimer">{data.disclaimer}</p>
    </div>
  );
}
