"use client";

import { useEffect, useState } from "react";

import { DATA1A_CAPTURE_POLL_MS } from "./data1a-capture-poll";
import { parsePublicCandleSnapshot, type PublicBtcCandleSnapshot } from "./public-candles";

export type PublicCandleState =
  | { status: "loading" }
  | { status: "ready"; snapshot: PublicBtcCandleSnapshot }
  | { status: "error"; message: string };

export function usePublicBtcPerpCandles(): PublicCandleState {
  const [state, setState] = useState<PublicCandleState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const response = await fetch("/api/public-btc-perp-candles", { cache: "no-store" });
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
    const timer = window.setInterval(() => {
      void refresh();
    }, DATA1A_CAPTURE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return state;
}
