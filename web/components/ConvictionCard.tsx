import type { ScoreResponse, ScoreHistoryPoint, SimilarResponse } from "@/lib/types";
import { ConvictionRing } from "@/components/ConvictionRing";
import { ConvictionSparkline } from "@/components/ConvictionSparkline";
import { PillarRadar } from "@/components/PillarRadar";
import { PillarAccordion } from "@/components/PillarAccordion";
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

export function ConvictionCard({
  data,
  history,
  similar,
}: {
  data: ScoreResponse;
  history?: ScoreHistoryPoint[];
  similar?: SimilarResponse | null;
}) {
  const color = recColor(data.recommendation);
  const hasPillars =
    data.pillar_scores && Object.keys(data.pillar_scores).length > 0;

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
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {history && history.length >= 2 && (
            <ConvictionSparkline history={history} />
          )}
          <ConvictionRing
            value={data.conviction}
            color={color}
            caption={data.recommendation}
          />
        </div>
      </div>

      {data.cap_applied && data.raw_conviction !== undefined && (
        <p className="cap-callout">
          ⚠️ Model scored <b>{data.raw_conviction}</b> → capped to{" "}
          <b>{data.conviction}</b>
          {data.cap_reason ? ` (${data.cap_reason.replace(/_/g, " ")})` : ""}
        </p>
      )}

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

      {hasPillars ? (
        <>
          <p className="section-label">5-Pillar Score Breakdown</p>
          <div
            style={{
              display: "flex",
              gap: 16,
              flexWrap: "wrap",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 12,
            }}
          >
            <PillarRadar pillars={data.pillar_scores!} />
            <div style={{ flex: 1, minWidth: 220 }}>
              <PillarAccordion pillars={data.pillar_scores!} />
            </div>
          </div>
        </>
      ) : (
        <>
          <p className="section-label">Top drivers (SHAP)</p>
          <DriverBars drivers={data.drivers} />
          <DriverExplainer drivers={data.drivers} />
        </>
      )}

      {similar?.similar && (
        <p className="analogs-line">
          📊 <b>{Math.round(similar.similar.base_rate * similar.similar.k)}/{similar.similar.k}</b> similar
          past setups beat SPY ({(similar.similar.base_rate * 100).toFixed(0)}% historical hit rate)
        </p>
      )}

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
          {data.calibration_mode === "rank"
            ? "rank-based"
            : data.calibrated
              ? "calibrated"
              : "uncalibrated"}{" "}
          · {data.model_version}
        </span>
      </div>

      <p className="disclaimer">{data.disclaimer}</p>
    </div>
  );
}
