// Shared presentation helpers for the visualization layer.
// Keeps the market colour + direction language in one place so every chart,
// gauge and pill reads consistently (and stays WCAG-friendly by never relying
// on colour alone — a direction glyph always accompanies it).

const REC_COLORS: Record<string, string> = {
  "consider buying": "var(--green)",
  "lean buy / watch": "var(--teal)",
  "hold / neutral": "var(--gray)",
  "lean trim / watch": "var(--orange)",
  "consider trimming / avoid": "var(--red)",
};

export type Tone = "bull" | "bear" | "neutral";

export function recColor(recommendation: string): string {
  return REC_COLORS[recommendation] ?? "var(--gray)";
}

/** Map a recommendation string to a bull / bear / neutral stance. */
export function recTone(recommendation: string): Tone {
  const r = recommendation.toLowerCase();
  if (r.includes("buy")) return "bull";
  if (r.includes("trim") || r.includes("sell") || r.includes("avoid")) {
    return "bear";
  }
  return "neutral";
}

/** A direction glyph that reinforces colour for accessibility. */
export function toneCaret(tone: Tone): string {
  return tone === "bull" ? "▲" : tone === "bear" ? "▼" : "▬";
}

/** Human-readable feature name for SHAP driver labels. */
export function prettyFeature(feature: string): string {
  return feature.replace(/_/g, " ");
}

/** Parse backend timestamps, which are UTC even when the offset is omitted. */
export function parseBackendDate(value: string): Date {
  return new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);
}
