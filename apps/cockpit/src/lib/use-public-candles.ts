"use client";

import { useEffect, useState } from "react";

import {
  PUBLIC_CANDLE_INTERVAL,
  parsePublicCandleSnapshot,
  type PublicBtcCandleSnapshot,
  type PublicCandleInterval,
} from "./public-candles";

export type PublicCandleState =
  | { status: "loading" }
  | { status: "ready"; snapshot: PublicBtcCandleSnapshot }
  | { status: "error"; message: string };

export function usePublicBtcPerpCandles(
  interval: PublicCandleInterval = PUBLIC_CANDLE_INTERVAL,
  refreshToken = 0,
): PublicCandleState {
  const [state, setState] = useState<PublicCandleState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const response = await fetch(
          `/api/public-btc-perp-candles?interval=${encodeURIComponent(interval)}`,
          { cache: "no-store" },
        );
        const payload: unknown = await response.json();
        if (!response.ok) {
          const message =
            typeof payload === "object" &&
            payload !== null &&
            "error" in payload &&
            typeof payload.error === "string"
              ? payload.error
              : `Public candle request failed (${String(response.status)})`;
          throw new Error(message);
        }
        const snapshot = parsePublicCandleSnapshot(payload);
        if (!cancelled) {
          setState({ status: "ready", snapshot });
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setState({
            status: "error",
            message: error instanceof Error ? error.message : "Public candle request failed.",
          });
        }
      }
    }

    void refresh();
    return () => {
      cancelled = true;
    };
  }, [interval, refreshToken]);

  return state;
}
