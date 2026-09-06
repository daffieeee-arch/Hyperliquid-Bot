"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type UTCTimestamp,
} from "lightweight-charts";
import { Maximize2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "./ui/button";
import { Notice } from "./ui/notice";
import { PUBLIC_CANDLE_SOURCE, type PublicCandleInterval } from "../lib/public-candles";
import { usePublicBtcPerpCandles } from "../lib/use-public-candles";

function readChartTheme() {
  const styles = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string): string =>
    styles.getPropertyValue(name).trim() || fallback;
  return {
    background: read("--panel", "#12151b"),
    text: read("--muted", "#97a3b4"),
    line: read("--line", "#262c36"),
    up: read("--ok", "#3ecf8e"),
    down: read("--down", "#f2555a"),
  };
}

/**
 * Public HL BTC-PERP candles.
 *
 * The chart instance and its series are created once and reused. Refreshes go
 * through `series.setData` with the visible logical range captured and
 * restored around the write, so polling never throws away the operator's zoom
 * or pan. Only the first load — and an explicit interval change — refits.
 */
export function MidChart({
  interval,
  refreshToken = 0,
}: {
  interval: PublicCandleInterval;
  refreshToken?: number;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const fittedRef = useRef(false);
  const [ready, setReady] = useState(false);
  const state = usePublicBtcPerpCandles(interval, refreshToken);

  useEffect(() => {
    const host = hostRef.current;
    if (host === null) {
      return;
    }
    const theme = readChartTheme();
    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: theme.background },
        textColor: theme.text,
        fontFamily: "var(--font-mono), ui-monospace, monospace",
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: theme.line },
        horzLines: { color: theme.line },
      },
      rightPriceScale: { borderColor: theme.line },
      timeScale: { borderColor: theme.line, timeVisible: true },
      crosshair: { mode: 1 },
      handleScroll: true,
      handleScale: true,
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: theme.up,
      downColor: theme.down,
      borderVisible: false,
      wickUpColor: theme.up,
      wickDownColor: theme.down,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    setReady(true);

    // Re-tint without rebuilding when the operator switches theme.
    const observer = new MutationObserver(() => {
      const next = readChartTheme();
      chart.applyOptions({
        layout: {
          background: { type: ColorType.Solid, color: next.background },
          textColor: next.text,
        },
        grid: { vertLines: { color: next.line }, horzLines: { color: next.line } },
        rightPriceScale: { borderColor: next.line },
        timeScale: { borderColor: next.line },
      });
      series.applyOptions({
        upColor: next.up,
        downColor: next.down,
        wickUpColor: next.up,
        wickDownColor: next.down,
      });
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      fittedRef.current = false;
      setReady(false);
    };
  }, []);

  // A different interval is a different series; refit once for the new bars.
  useEffect(() => {
    fittedRef.current = false;
  }, [interval]);

  const candles = useMemo<CandlestickData<UTCTimestamp>[]>(() => {
    if (state.status !== "ready") {
      return [];
    }
    return state.snapshot.candles.map((candle) => ({
      time: candle.time as UTCTimestamp,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    }));
  }, [state]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!ready || chart === null || series === null || candles.length === 0) {
      return;
    }
    const timeScale = chart.timeScale();
    const previous: LogicalRange | null = fittedRef.current
      ? timeScale.getVisibleLogicalRange()
      : null;
    series.setData(candles);
    if (previous === null) {
      timeScale.fitContent();
      fittedRef.current = true;
      return;
    }
    timeScale.setVisibleLogicalRange(previous);
  }, [candles, ready]);

  const resetZoom = useCallback(() => {
    chartRef.current?.timeScale().fitContent();
  }, []);

  return (
    <div className="chart-shell">
      {state.status === "error" ? (
        <Notice state="error" title="Public candles unavailable">
          {state.message} · candles are not invented
        </Notice>
      ) : null}
      {state.status === "ready" && state.snapshot.candles.length === 0 ? (
        <Notice state="missing" title="No candles returned">
          Public candleSnapshot returned no rows for {interval}.
        </Notice>
      ) : null}
      <div
        ref={hostRef}
        className="chart-canvas"
        role="img"
        aria-label={`Public Hyperliquid BTC-PERP ${interval} candles`}
      />
      <div className="row" style={{ marginTop: "0.35rem" }}>
        <p className="chart-note">
          {state.status === "ready"
            ? `${state.snapshot.interval} · ${state.snapshot.candles.length} bars · ${PUBLIC_CANDLE_SOURCE} · public mid ≠ research truth · not Paper PnL · read ${state.snapshot.fetched_at}`
            : state.status === "loading"
              ? "Fetching public candleSnapshot…"
              : "Chart keeps the last good bars until the public route answers again."}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="spacer"
          onClick={resetZoom}
          disabled={!ready}
        >
          <Maximize2 aria-hidden="true" />
          Fit
        </Button>
      </div>
    </div>
  );
}
