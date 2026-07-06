// A bull ↔ bear sentiment meter. The news score (−1…+1) is placed on a
// bearish→neutral→bullish track so tone is spatial, not just a coloured word.
export function SentimentMeter({
  score,
  label,
}: {
  score: number;
  label: string;
}) {
  const clamped = Math.max(-1, Math.min(1, score));
  const pos = ((clamped + 1) / 2) * 100; // 0…100%

  return (
    <div
      className="smeter"
      role="img"
      aria-label={`News sentiment: ${label}, score ${score.toFixed(
        2,
      )} on a scale from minus one (bearish) to plus one (bullish)`}
    >
      <div className="smeter-track">
        <span className="smeter-marker" style={{ left: `${pos}%` }} />
      </div>
      <div className="smeter-scale">
        <span>Bearish</span>
        <span>Neutral</span>
        <span>Bullish</span>
      </div>
    </div>
  );
}
