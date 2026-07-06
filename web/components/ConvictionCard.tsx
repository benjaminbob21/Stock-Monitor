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
            style={{
              color,
              background: `${color}22`,
              border: `1px solid ${color}55`,
            }}
          >
            {data.recommendation}
          </span>
        </div>
      </div>

      {data.conviction_3m !== null && data.conviction_3m !== undefined && (
        <div className="horizon">
          <span>
            <b>12-month</b> {data.conviction}/100 · {data.recommendation}
          </span>
          <span>
            <b>near-term (3-month)</b> {data.conviction_3m}/100 ·{" "}
            {data.recommendation_3m}
          </span>
        </div>
      )}

      {(data.conviction_3m === null || data.conviction_3m === undefined) &&
        data.near_term_note && (
          <div className="horizon">
            <span>
              <b>near-term</b> {data.recommendation_3m ?? "no clear near-term signal"}
            </span>
            <span className="near-term-note">{data.near_term_note}</span>
          </div>
        )}

      {data.days_to_earnings !== null && data.days_to_earnings !== undefined && (
        <p
          className="earnings"
          style={{
            color: data.days_to_earnings <= 5 ? "var(--orange)" : "var(--muted)",
          }}
        >
          📅 Earnings in {data.days_to_earnings} day
          {data.days_to_earnings === 1 ? "" : "s"}
          {data.days_to_earnings <= 5 ? " — expect volatility (score capped)" : ""}
        </p>
      )}

      <p className="section-label">Top drivers (SHAP)</p>
      {data.drivers.map((d) => (
        <div className="driver" key={d.feature}>
          <span className={`dir ${d.direction === "+" ? "pos" : "neg"}`}>
            {d.direction}
          </span>
          <span className="feat">{d.feature}</span>
          <span className="num">
            value {Number.isFinite(d.value) ? d.value.toFixed(4) : "n/a"} · shap{" "}
            {Number.isFinite(d.shap) ? d.shap.toFixed(3) : "n/a"}
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
          {data.calibrated ? "calibrated" : "uncalibrated"} ·{" "}
          {data.model_version}
        </span>
      </div>

      <p className="disclaimer">{data.disclaimer}</p>
    </div>
  );
}
