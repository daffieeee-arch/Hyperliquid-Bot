"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";

import { PUBLIC_CANDLE_INTERVAL } from "../lib/public-candles";
import { usePublicBtcPerpCandles } from "../lib/use-public-candles";

/** Sparkline of real public candle closes. Never a PnL curve. */
export function PublicCloseSparkline({ refreshToken = 0 }: { refreshToken?: number }) {
  const state = usePublicBtcPerpCandles(PUBLIC_CANDLE_INTERVAL, refreshToken);
  if (state.status !== "ready" || state.snapshot.candles.length < 2) {
    return null;
  }
  const data = state.snapshot.candles.map((candle) => ({
    close: candle.close,
    time: candle.time,
  }));
  const closes = data.map((point) => point.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);

  return (
    <div aria-label="Public HL BTC-PERP close sparkline">
      <ResponsiveContainer width="100%" height={44} className="sparkline">
        <LineChart data={data} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
          <YAxis hide domain={[min, max]} />
          <Tooltip
            cursor={{ stroke: "var(--line-strong)" }}
            contentStyle={{
              background: "var(--panel-2)",
              border: "1px solid var(--line)",
              borderRadius: "var(--radius)",
              fontSize: "0.7rem",
              fontFamily: "var(--font-mono)",
            }}
            labelFormatter={() => ""}
          />
          <Line
            type="monotone"
            dataKey="close"
            stroke="var(--paper)"
            strokeWidth={1.4}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
      <p className="chart-note">
        {state.snapshot.interval} closes · public candleSnapshot · not research truth · not Paper
        PnL
      </p>
    </div>
  );
}
