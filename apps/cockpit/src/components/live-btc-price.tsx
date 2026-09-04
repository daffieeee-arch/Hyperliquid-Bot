"use client";

import { useEffect, useState } from "react";

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
    <section className="panel">
      <h2>Public BTC-PERP mid</h2>
      {state.status === "loading" ? (
        <p className="meta">Fetching public Hyperliquid /info…</p>
      ) : null}
      {state.status === "error" ? <p className="error">{state.message}</p> : null}
      {state.status === "ready" ? (
        <>
          <p className="value">{state.price.mid}</p>
          <p className="meta">
            {state.price.instrument} · {state.price.source}
          </p>
          <p className="note">
            Public market data only. Browser never signs orders and does not hold keys. This mid is
            not used to invent Paper PnL.
          </p>
          <p className="note">Fetched {state.price.fetched_at}</p>
        </>
      ) : null}
    </section>
  );
}
