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
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return v || fallback;
}

export function PriceChart({
  ticker,
  costBasis,
  livePrice,
}: {
  ticker: string;
  costBasis?: number;
  livePrice?: number;
}) {
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
        BaselineSeries,
        HistogramSeries,
        ColorType,
        CrosshairMode,
        LineStyle,
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

      // Baseline = cost basis when tracking a position, otherwise the period's
      // starting price — so the line is green above the reference and red below.
      // A clean "am I up vs what I paid?" read for a buy-and-hold, not candlesticks.
      const baseline = costBasis && costBasis > 0 ? costBasis : bars[0].close;

      const area = chart.addSeries(BaselineSeries, {
        baseValue: { type: "price", price: baseline },
        topLineColor: green,
        topFillColor1: "rgba(52, 224, 161, 0.28)",
        topFillColor2: "rgba(52, 224, 161, 0.02)",
        bottomLineColor: red,
        bottomFillColor1: "rgba(255, 107, 120, 0.04)",
        bottomFillColor2: "rgba(255, 107, 120, 0.28)",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      area.setData(bars.map((b) => ({ time: b.time, value: b.close })));

      // Dashed reference line: cost basis for a holding, else the period open.
      area.createPriceLine({
        price: baseline,
        color: "rgba(159, 171, 196, 0.55)",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: costBasis && costBasis > 0 ? "cost" : "",
      });

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
  }, [bars, costBasis]);

  // Prefer a fresh intraday quote for the headline number when we have one; the
  // chart line itself stays on completed daily bars. Falls back to the last close.
  const headline =
    livePrice && livePrice > 0 ? livePrice : last ? last.close : null;
  // Reference the SAME baseline the chart draws from (cost basis for a tracked
  // position, otherwise the first bar of the selected range). This keeps the header
  // % in lock-step with the green/red fill and dashed line, so a chart that's up over
  // the range always shows a green gain — measured start-of-range → current.
  const baseline =
    costBasis && costBasis > 0
      ? costBasis
      : bars && bars.length > 0
        ? bars[0].close
        : null;
  const change =
    headline !== null && baseline !== null ? headline - baseline : 0;
  const changePct =
    headline !== null && baseline !== null && baseline
      ? (change / baseline) * 100
      : 0;
  const up = change >= 0;
  const isLive = Boolean(livePrice && livePrice > 0);

  return (
    <section className="chart-card" aria-label={`${ticker} price chart`}>
      <div className="chart-head">
        <div className="chart-price">
          {headline !== null ? (
            <>
              <span className="chart-last">${headline.toFixed(2)}</span>
              {isLive ? (
                <span className="chart-live" title="Live price (updates when you refresh)">
                  <span className="chart-live-dot" aria-hidden />
                  live
                </span>
              ) : (
                <span className="chart-live at-close" title="Last completed close">
                  at close
                </span>
              )}
              <span
                className={`chart-chg ${up ? "up" : "down"}`}
                aria-label={`${up ? "up" : "down"} ${Math.abs(changePct).toFixed(2)} percent over ${range.label}`}
              >
                {up ? "▲" : "▼"} {Math.abs(change).toFixed(2)} (
                {Math.abs(changePct).toFixed(2)}%)
              </span>
            </>
          ) : (
            <span className="chart-last muted">—</span>
          )}
        </div>
        <div
          className="chart-ranges"
          role="group"
          aria-label="Chart time range"
        >
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
          style={{
            opacity: loading || error || (bars && bars.length === 0) ? 0 : 1,
          }}
        />
      </div>
    </section>
  );
}
