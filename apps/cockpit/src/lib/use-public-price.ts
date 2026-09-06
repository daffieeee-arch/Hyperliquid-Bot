"use client";

import { useEffect, useState } from "react";

import { DATA1A_CAPTURE_POLL_MS } from "./data1a-capture-poll";
import type { PublicMidState } from "./markets";
import { parsePublicBtcPerpPayload, type PublicBtcPerpPrice } from "./public-price";

export function usePublicBtcPerp(): PublicMidState {
  const [state, setState] = useState<PublicMidState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const response = await fetch("/api/public-btc-perp", { cache: "no-store" });
        const payload: unknown = await response.json();
        if (!response.ok) {
          const message =
            typeof payload === "object" &&
            payload !== null &&
            "error" in payload &&
            typeof payload.error === "string"
              ? payload.error
              : `Public price request failed (${String(response.status)})`;
          throw new Error(message);
        }
        const price: PublicBtcPerpPrice = parsePublicBtcPerpPayload(payload);
        if (!cancelled) {
          setState({ status: "ready", price });
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setState({
            status: "error",
            message: error instanceof Error ? error.message : "Public price request failed.",
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
