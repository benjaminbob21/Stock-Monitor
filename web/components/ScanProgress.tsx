"use client";

// Circular progress ring for the universe refresh. When we have a real percentage
// (done/total from the backend) it fills to that fraction; before the first tick it
// spins as an indeterminate ring so the button never looks frozen.
export function ScanProgress({
  pct,
  label,
}: {
  pct: number | null;
  label?: string;
}) {
  const R = 22;
  const C = 2 * Math.PI * R;
  const known = pct !== null && pct >= 0;
  const clamped = Math.max(0, Math.min(100, pct ?? 0));
  const offset = C * (1 - clamped / 100);

  return (
    <div className="scanprog" role="status" aria-live="polite">
      <div
        className={`scanprog-ring ${known ? "" : "spin"}`}
        aria-label={
          known ? `refreshing ${clamped} percent complete` : "refreshing"
        }
      >
        <svg viewBox="0 0 56 56" width="56" height="56">
          <circle
            className="scanprog-track"
            cx="28"
            cy="28"
            r={R}
            fill="none"
            strokeWidth="5"
          />
          <circle
            className="scanprog-fill"
            cx="28"
            cy="28"
            r={R}
            fill="none"
            strokeWidth="5"
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={known ? offset : C * 0.7}
            transform="rotate(-90 28 28)"
          />
        </svg>
        <span className="scanprog-pct">{known ? `${clamped}%` : "…"}</span>
      </div>
      {label && <span className="scanprog-label">{label}</span>}
    </div>
  );
}
