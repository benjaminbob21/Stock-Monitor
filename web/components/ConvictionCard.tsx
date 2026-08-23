import type { ScoreResponse } from "@/lib/types";
import { ConvictionRing } from "@/components/ConvictionRing";
import { DriverBars } from "@/components/DriverBars";
import { DriverExplainer } from "@/components/DriverExplainer";

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
          {data.name && <p className="company">{data.name}</p>}
          <p className="asof">as of {data.as_of}</p>
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
        <ConvictionRing
          value={data.conviction}
          color={color}
          caption={data.recommendation}
        />
      </div>

      {data.conviction_3m !== null && data.conviction_3m !== undefined && (
        <div className="hzbars">
          <div className="hzrow">
            <span className="hzlabel">12-month</span>
            <span className="hztrack">
              <span
                className="hzfill"
                style={{
                  width: `${Math.max(0, Math.min(100, data.conviction))}%`,
                  background: color,
                }}
              />
            </span>
            <span className="hznum">{data.conviction}</span>
          </div>
          <div className="hzrow">
            <span className="hzlabel">near-term</span>
            <span className="hztrack">
              <span
                className="hzfill"
                style={{
                  width: `${Math.max(0, Math.min(100, data.conviction_3m))}%`,
                  background: recColor(data.recommendation_3m ?? ""),
                }}
              />
            </span>
            <span className="hznum">{data.conviction_3m}</span>
          </div>
        </div>
      )}

      {(data.conviction_3m === null || data.conviction_3m === undefined) &&
        data.near_term_note && (
          <div className="horizon">
            <span>
              <b>near-term</b>{" "}
              {data.recommendation_3m ?? "no clear near-term signal"}
            </span>
            <span className="near-term-note">{data.near_term_note}</span>
          </div>
        )}

      {data.short_event && (
        <div className="horizon" style={{ marginTop: "1rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: "0.75rem" }}>
            <b>event signal · 1–4 weeks</b>
            <strong style={{ color: data.short_event.candidate ? "var(--green)" : "var(--muted)" }}>
              {data.short_event.conviction}/100{data.short_event.candidate ? " · candidate" : ""}
            </strong>
          </div>
          {data.short_event.recent_events.length > 0 ? (
            <ul style={{ margin: "0.5rem 0 0", paddingLeft: "1.1rem" }}>
              {data.short_event.recent_events.slice(0, 3).map((event, index) => (
                <li key={`${event.published_at}-${index}`}>
                  {event.headline} <span className="near-term-note">({event.source})</span>
                </li>
              ))}
            </ul>
          ) : (
            <span className="near-term-note">No recent qualifying events.</span>
          )}
        </div>
      )}

      {data.days_to_earnings !== null &&
        data.days_to_earnings !== undefined && (
          <p
            className="earnings"
            style={{
              color:
                data.days_to_earnings <= 5 ? "var(--orange)" : "var(--muted)",
            }}
          >
            📅 Earnings in {data.days_to_earnings} day
            {data.days_to_earnings === 1 ? "" : "s"}
            {data.days_to_earnings <= 5
              ? " — expect volatility (score capped)"
              : ""}
          </p>
        )}

      <p className="section-label">Top drivers (SHAP)</p>
      <DriverBars drivers={data.drivers} />
      <DriverExplainer drivers={data.drivers} />

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
