// A radial conviction gauge — the model's 0–100 score rendered as an SVG ring
// so strength is legible at a glance, not just a number.
export function ConvictionRing({
  value,
  color,
  caption,
}: {
  value: number;
  color: string;
  caption?: string;
}) {
  const size = 118;
  const stroke = 11;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value));
  const offset = circumference * (1 - pct / 100);

  return (
    <div
      className="ring"
      role="img"
      aria-label={`Conviction ${pct} out of 100${caption ? `, ${caption}` : ""}`}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle
          className="ring-track"
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
          fill="none"
        />
        <circle
          className="ring-value"
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
          fill="none"
          stroke={color}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className="ring-center">
        <span className="ring-num" style={{ color }}>
          {pct}
        </span>
        <span className="ring-max">/100</span>
      </div>
    </div>
  );
}
