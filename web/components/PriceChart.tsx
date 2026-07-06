"use client";

import { useEffect, useRef, useState } from "react";

type Bar = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type Range = { label: string; days: number };

const RANGES: Range[] = [
  { label: "1M", days: 31 },
  { label: "3M", days: 93 },
  { label: "6M", days: 186 },
  { label: "1Y", days: 365 },
];

// Read a CSS custom property off :root so the canvas matches the design tokens.
function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function PriceChart({ ticker }: { ticker: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [range, setRange] = useState<Range>(RANGES[1]);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<Bar | null>(null);

  // Fetch bars whenever the ticker or range changes.
  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/prices/${encodeURIComponent(ticker)}?days=${range.days}`)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body?.detail || `HTTP ${res.status}`);
        return body.bars as Bar[];
      })
      .then((data) => {
        if (cancelled) return;
        setBars(data ?? []);
        setLast(data && data.length ? data[data.length - 1] : null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message || "Could not load price history");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, range]);

  // Build / update the chart once we have bars and a container.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !bars || bars.length === 0) return;

    let disposed = false;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let chart: any;
    let ro: ResizeObserver | undefined;

    import("lightweight-charts").then((LWC) => {
      if (disposed || !containerRef.current) return;
      const {
        createChart,
        CandlestickSeries,
        HistogramSeries,
        ColorType,
        CrosshairMode,
      } = LWC;

      const green = cssVar("--green", "#34e0a1");
      const red = cssVar("--red", "#ff6b78");
      const text = cssVar("--muted", "#9fabc4");
      const line = "rgba(159, 171, 196, 0.12)";

      chart = createChart(containerRef.current, {
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: text,
          fontFamily: "var(--font-inter), system-ui, sans-serif",
          attributionLogo: false,
        },
        grid: {
          vertLines: { color: "transparent" },
          horzLines: { color: line },
        },
        rightPriceScale: { borderColor: line },
        timeScale: { borderColor: line, fixLeftEdge: true, fixRightEdge: true },
        crosshair: { mode: CrosshairMode.Magnet },
        handleScale: { axisPressedMouseMove: false },
        autoSize: true,
      });

      const candles = chart.addSeries(CandlestickSeries, {
        upColor: green,
        downColor: red,
        borderUpColor: green,
        borderDownColor: red,
        wickUpColor: green,
        wickDownColor: red,
        priceLineVisible: false,
      });
      candles.setData(
        bars.map((b) => ({
          time: b.time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        })),
      );

      const volume = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      volume.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      volume.setData(
        bars.map((b, i) => ({
          time: b.time,
          value: b.volume,
          color:
            i > 0 && b.close < bars[i - 1].close
              ? "rgba(255, 107, 120, 0.35)"
              : "rgba(52, 224, 161, 0.35)",
        })),
      );

      chart.timeScale().fitContent();

      ro = new ResizeObserver(() => chart?.timeScale().fitContent());
      ro.observe(containerRef.current);
    });

    return () => {
      disposed = true;
      ro?.disconnect();
      chart?.remove();
    };
  }, [bars]);

  const change =
    last && bars && bars.length > 1
      ? last.close - bars[0].open
      : 0;
  const changePct =
    last && bars && bars.length > 1 && bars[0].open
      ? (change / bars[0].open) * 100
      : 0;
  const up = change >= 0;

  return (
    <section className="chart-card" aria-label={`${ticker} price chart`}>
      <div className="chart-head">
        <div className="chart-price">
          {last ? (
            <>
              <span className="chart-last">${last.close.toFixed(2)}</span>
              <span
                className={`chart-chg ${up ? "up" : "down"}`}
                aria-label={`${up ? "up" : "down"} ${Math.abs(changePct).toFixed(2)} percent over ${range.label}`}
              >
                {up ? "▲" : "▼"} {Math.abs(change).toFixed(2)} ({Math.abs(changePct).toFixed(2)}%)
              </span>
            </>
          ) : (
            <span className="chart-last muted">—</span>
          )}
        </div>
        <div className="chart-ranges" role="group" aria-label="Chart time range">
          {RANGES.map((r) => (
            <button
              key={r.label}
              type="button"
              className={`chart-range ${r.days === range.days ? "active" : ""}`}
              aria-pressed={r.days === range.days}
              onClick={() => setRange(r)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <div className="chart-canvas-wrap">
        {loading && <div className="chart-state">Loading {ticker} chart…</div>}
        {error && !loading && <div className="chart-state error">{error}</div>}
        {!loading && !error && bars && bars.length === 0 && (
          <div className="chart-state">No price history for {ticker}.</div>
        )}
        <div
          ref={containerRef}
          className="chart-canvas"
          style={{ opacity: loading || error || (bars && bars.length === 0) ? 0 : 1 }}
        />
      </div>
    </section>
  );
}
