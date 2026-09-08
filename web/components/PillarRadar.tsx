import type { PillarScore } from "@/lib/types";

const PILLAR_ORDER = ["Momentum", "Technical", "Quality", "Value", "Sentiment"];
const CX = 110,
  CY = 110,
  R = 75;

function polarToXY(angle: number, radius: number): [number, number] {
  const rad = ((angle - 90) * Math.PI) / 180;
  return [CX + radius * Math.cos(rad), CY + radius * Math.sin(rad)];
}

export function PillarRadar({
  pillars,
}: {
  pillars: Record<string, PillarScore>;
}) {
  const n = PILLAR_ORDER.length;
  const step = 360 / n;

  // Background grid rings at 25%, 50%, 75%, 100%
  const rings = [0.25, 0.5, 0.75, 1.0];

  // Data polygon points
  const dataPoints = PILLAR_ORDER.map((name, i) => {
    const score = pillars[name]?.score ?? 50;
    const frac = Math.max(0, Math.min(100, score)) / 100;
    return polarToXY(i * step, R * frac);
  });
  const dataPath =
    dataPoints.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x},${y}`).join(" ") +
    "Z";

  return (
    <svg
      viewBox="0 0 220 220"
      className="pillar-radar"
      width={220}
      height={220}
      aria-label="5-Pillar Score Radar Chart"
    >
      {/* Grid rings */}
      {rings.map((frac) => {
        const pts = Array.from({ length: n }, (_, i) =>
          polarToXY(i * step, R * frac)
        );
        const path =
          pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x},${y}`).join(" ") +
          "Z";
        return (
          <path
            key={frac}
            d={path}
            fill="none"
            stroke="var(--border, #444)"
            strokeWidth={0.75}
            strokeDasharray={frac === 0.5 ? "2 2" : undefined}
            opacity={0.5}
          />
        );
      })}

      {/* Axis lines */}
      {PILLAR_ORDER.map((_, i) => {
        const [x, y] = polarToXY(i * step, R);
        return (
          <line
            key={i}
            x1={CX}
            y1={CY}
            x2={x}
            y2={y}
            stroke="var(--border, #444)"
            strokeWidth={0.75}
            opacity={0.4}
          />
        );
      })}

      {/* Data fill */}
      <path
        d={dataPath}
        fill="var(--green, #22c55e)"
        fillOpacity={0.2}
        stroke="var(--green, #22c55e)"
        strokeWidth={1.75}
      />

      {/* Data points */}
      {dataPoints.map(([x, y], i) => (
        <circle
          key={i}
          cx={x}
          cy={y}
          r={3}
          fill="var(--green, #22c55e)"
        />
      ))}

      {/* Labels */}
      {PILLAR_ORDER.map((name, i) => {
        const [x, y] = polarToXY(i * step, R + 18);
        const score = pillars[name]?.score ?? 50;
        return (
          <text
            key={name}
            x={x}
            y={y}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize={9}
            fontWeight={600}
            fill="var(--text, #fff)"
          >
            {name} {score}
          </text>
        );
      })}
    </svg>
  );
}
