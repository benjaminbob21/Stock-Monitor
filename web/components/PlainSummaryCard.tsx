import type { Driver } from "@/lib/types";
import { driverLead } from "@/lib/drivers";

// The AI narrative — a short, flowing plain-English read on the company that adds
// context around the model's drivers. Falls back to a deterministic one-liner when
// the AI is disabled or the call fails, so the card is never empty.
export function PlainSummaryCard({
  summary,
  loading,
  drivers,
}: {
  summary: string | null;
  loading: boolean;
  drivers: Driver[];
}) {
  const ai = summary && summary.trim().length > 0 ? summary.trim() : null;
  const fallback = ai ? null : driverLead(drivers);

  // Nothing to show and nothing loading — render nothing.
  if (!loading && !ai && !fallback) return null;

  return (
    <div className="card plainsum">
      <p className="plainsum-head">
        <span className={`plainsum-badge ${ai ? "" : "muted"}`}>
          {ai ? "AI summary" : "In brief"}
        </span>
        {ai && <span className="plainsum-model">plain-English read</span>}
      </p>

      {loading && !ai ? (
        <p className="plainsum-body loading">Reading the signals…</p>
      ) : (
        <p className="plainsum-body">{ai ?? fallback}</p>
      )}

      {ai && (
        <p className="disclaimer">
          AI-generated, plain-language read of the model’s drivers — context for
          a human, not advice. The calibrated score remains the signal of
          record.
        </p>
      )}
    </div>
  );
}
