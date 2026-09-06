"use client";

import { CandlestickSeries, ColorType, createChart, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

import { PUBLIC_CANDLE_SOURCE } from "../lib/public-candles";
import { usePublicBtcPerpCandles } from "../lib/use-public-candles";

export function MidChart() {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const state = usePublicBtcPerpCandles();

  useEffect(() => {
    const host = hostRef.current;
    if (host === null || state.status !== "ready" || state.snapshot.candles.length === 0) {
      return;
    }
    const styles = getComputedStyle(document.documentElement);
    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: {
          type: ColorType.Solid,
          color: styles.getPropertyValue("--panel").trim() || "#101318",
        },
        textColor: styles.getPropertyValue("--muted").trim() || "#8b98a5",
        fontFamily: "var(--font-mono), ui-monospace, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: styles.getPropertyValue("--line").trim() || "#222830" },
        horzLines: { color: styles.getPropertyValue("--line").trim() || "#222830" },
      },
      rightPriceScale: { borderColor: styles.getPropertyValue("--line").trim() || "#222830" },
      timeScale: {
        borderColor: styles.getPropertyValue("--line").trim() || "#222830",
        timeVisible: true,
      },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: styles.getPropertyValue("--up").trim() || "#3dcc8c",
      downColor: styles.getPropertyValue("--down").trim() || "#ef6b73",
      borderVisible: false,
      wickUpColor: styles.getPropertyValue("--up").trim() || "#3dcc8c",
      wickDownColor: styles.getPropertyValue("--down").trim() || "#ef6b73",
    });
    series.setData(
      state.snapshot.candles.map((candle) => ({
        time: candle.time as UTCTimestamp,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      })),
    );
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
    };
  }, [state]);

  return (
    <div className="mid-chart" aria-label="Public HL BTC-PERP candles">
      {state.status === "loading" ? <p className="note">Fetching public candleSnapshot…</p> : null}
      {state.status === "error" ? (
        <p className="error">UNAVAILABLE · {state.message} · candles are not invented</p>
      ) : null}
      {state.status === "ready" && state.snapshot.candles.length === 0 ? (
        <p className="error">UNAVAILABLE · public candleSnapshot returned no rows</p>
      ) : null}
      {state.status === "ready" && state.snapshot.candles.length > 0 ? (
        <>
          <div ref={hostRef} className="mid-chart-canvas" />
          <p className="note">
            {state.snapshot.interval} · {PUBLIC_CANDLE_SOURCE} · public mid ≠ research · not Paper
            PnL · {state.snapshot.fetched_at}
          </p>
        </>
      ) : (
        <div ref={hostRef} className="mid-chart-canvas mid-chart-empty" hidden />
      )}
    </div>
  );
}
