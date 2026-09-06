"use client";

import { Line, LineChart, ResponsiveContainer } from "recharts";

import { usePublicBtcPerpCandles } from "../lib/use-public-candles";

export function PublicCloseSparkline() {
  const state = usePublicBtcPerpCandles();
  if (state.status !== "ready" || state.snapshot.candles.length < 2) {
    return null;
  }
  const data = state.snapshot.candles.map((candle) => ({ close: candle.close }));

  return (
    <div className="kpi-sparkline" aria-label="Public HL BTC-PERP close sparkline">
      <ResponsiveContainer width="100%" height={36}>
        <LineChart data={data} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
          <Line
            type="monotone"
            dataKey="close"
            stroke="var(--paper)"
            strokeWidth={1.25}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
      <p className="note">
        KPI sparkline · public candle closes only · not research truth · not Paper PnL
      </p>
    </div>
  );
}
