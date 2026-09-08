import type { ScoreHistoryPoint } from "@/lib/types";

export function ConvictionSparkline({
  history,
}: {
  history: ScoreHistoryPoint[];
}) {
  if (!history || history.length < 2) return null;

  const W = 80,
    H = 30,
    PAD = 4;
  const scores = history.map((h) => h.conviction);
  const min = Math.min(...scores) - 5;
  const max = Math.max(...scores) + 5;
  const range = max - min || 1;

  const points = scores.map((s, i) => {
    const x = PAD + (i / (scores.length - 1)) * (W - 2 * PAD);
    const y = H - PAD - ((s - min) / range) * (H - 2 * PAD);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const last = scores[scores.length - 1];
  const first = scores[0];
  const color = last >= first ? "var(--green, #22c55e)" : "var(--red, #ef4444)";

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      className="sparkline"
      aria-label={`Conviction 30d trend: ${first} to ${last}`}
    >
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle
        cx={parseFloat(points[points.length - 1].split(",")[0])}
        cy={parseFloat(points[points.length - 1].split(",")[1])}
        r={2.5}
        fill={color}
      />
    </svg>
  );
}
