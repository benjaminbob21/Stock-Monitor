"use client";

import type { Scorecard, SignalStatus } from "@/lib/types";

const VERDICT_STYLES: Record<
  Scorecard["verdict"],
  { color: string; emoji: string }
> = {
  confirmed: { color: "var(--green)", emoji: "🟢" },
  no_edge: { color: "var(--red)", emoji: "🔴" },
  building: { color: "#f5c451", emoji: "🟡" },
};

const STATUS_STYLES: Record<SignalStatus, { color: string; emoji: string }> = {
  pass: { color: "var(--green)", emoji: "🟢" },
  fail: { color: "var(--red)", emoji: "🔴" },
  pending: { color: "#f5c451", emoji: "🟡" },
};

function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

function SignalRow({
  title,
  status,
  message,
  children,
}: {
  title: string;
  status: SignalStatus;
  message: string;
  children?: React.ReactNode;
}) {
  const s = STATUS_STYLES[status];
  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        padding: "10px 12px",
        borderRadius: 12,
        background: "var(--panel-2)",
        border: "1px solid var(--border)",
      }}
    >
      <span style={{ fontSize: 16, lineHeight: "22px" }}>{s.emoji}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 8,
          }}
        >
          <strong style={{ color: "var(--text-strong)", fontSize: 13 }}>
            {title}
          </strong>
          <span
            style={{
              color: s.color,
              fontSize: 11,
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: 0.4,
            }}
          >
            {status === "pass"
              ? "beating market"
              : status === "fail"
                ? "not yet"
                : "gathering"}
          </span>
        </div>
        <p style={{ margin: "3px 0 0", color: "var(--muted)", fontSize: 12.5 }}>
          {message}
        </p>
        {children}
      </div>
    </div>
  );
}

export function ScorecardCard({ data }: { data: Scorecard }) {
  const v = VERDICT_STYLES[data.verdict];
  const paper = data.paper;
  const needed = data.thresholds.min_closed_picks;

  return (
    <section
      aria-label="Edge scorecard"
      style={{
        margin: "0 0 14px",
        padding: 14,
        borderRadius: 16,
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderLeft: `4px solid ${v.color}`,
      }}
    >
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <span style={{ fontSize: 22 }}>{v.emoji}</span>
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <strong style={{ color: "var(--text-strong)", fontSize: 15 }}>
              {data.verdict_label}
            </strong>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>
              edge check
            </span>
          </div>
          <p
            style={{ margin: "2px 0 0", color: "var(--text)", fontSize: 12.5 }}
          >
            {data.message}
          </p>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          marginTop: 12,
        }}
      >
        <SignalRow
          title="Backtest (past 5 yrs vs S&P 500)"
          status={data.backtest.status}
          message={data.backtest.message}
        />
        <SignalRow
          title="Paper picks (live, no money)"
          status={paper.status}
          message={paper.message}
        >
          <div
            style={{
              marginTop: 8,
              height: 6,
              borderRadius: 4,
              background: "var(--panel-3)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${Math.round(paper.progress * 100)}%`,
                height: "100%",
                background: STATUS_STYLES[paper.status].color,
              }}
            />
          </div>
          <p style={{ margin: "4px 0 0", color: "var(--muted)", fontSize: 11 }}>
            {paper.closed}/{needed} matured · {paper.open} in flight ·{" "}
            {paper.hit_rate !== null
              ? `${pct(paper.hit_rate)} beat SPY`
              : "no results yet"}
          </p>
        </SignalRow>
      </div>

      <p style={{ margin: "10px 2px 0", color: "var(--muted)", fontSize: 11 }}>
        Green needs both: beat SPY in the backtest and on {needed}+ live picks
        (win rate ≥ {pct(data.thresholds.min_hit_rate)}). Until then, treat
        picks as a second opinion — not a buy button.
      </p>
    </section>
  );
}
