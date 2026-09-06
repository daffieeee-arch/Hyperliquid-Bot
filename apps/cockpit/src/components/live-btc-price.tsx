"use client";

import { formatGroupedNumber } from "../lib/display";
import { usePublicBtcPerp } from "../lib/use-public-price";

export function LiveBtcPrice() {
  const state = usePublicBtcPerp();

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
            Public /info only · not Paper PnL · {state.price.fetched_at}
          </p>
        </>
      ) : null}
    </article>
  );
}
