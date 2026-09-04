"use client";

import { useEffect, useState } from "react";

import { formatGroupedNumber } from "../lib/display";
import type { PublicBtcPerpPrice } from "../lib/public-price";

type LoadState =
  | { status: "loading" }
  | { status: "ready"; price: PublicBtcPerpPrice }
  | { status: "error"; message: string };

export function LiveBtcPrice() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

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
        if (
          typeof payload !== "object" ||
          payload === null ||
          !("mid" in payload) ||
          typeof payload.mid !== "string"
        ) {
          throw new Error("Public price response did not include a BTC mid string.");
        }
        if (!cancelled) {
          setState({ status: "ready", price: payload as PublicBtcPerpPrice });
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
    }, 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <article className="metric">
      <h2>Public BTC-PERP mid</h2>
      {state.status === "loading" ? (
        <>
          <p className="metric-value tone-neutral">—</p>
          <p className="metric-meta">Fetching public /info…</p>
        </>
      ) : null}
      {state.status === "error" ? (
        <>
          <p className="metric-value tone-warn">UNAVAILABLE</p>
          <p className="metric-note error">{state.message}</p>
        </>
      ) : null}
      {state.status === "ready" ? (
        <>
          <p className="metric-value">
            <span className="live-dot" aria-hidden="true" />
            {formatGroupedNumber(state.price.mid)}
          </p>
          <p className="metric-meta">{state.price.instrument} · unsigned public mid</p>
          <p className="metric-note">
            Credentialless Hyperliquid /info. Not used to invent Paper PnL. Fetched{" "}
            {state.price.fetched_at}
          </p>
        </>
      ) : null}
    </article>
  );
}
