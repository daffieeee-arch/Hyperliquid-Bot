"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export const COCKPIT_REFRESH_INTERVAL_MS = 15_000;

export type CockpitRefreshContextValue = {
  /** Bumped on every tick. Every polled panel keys its fetch on this. */
  token: number;
  /** ISO timestamp of the last tick, or null before the first one. */
  lastTickIso: string | null;
  paused: boolean;
  intervalMs: number;
  refreshNow: () => void;
  setPaused: (paused: boolean) => void;
};

const CockpitRefreshContext = createContext<CockpitRefreshContextValue | null>(null);

/**
 * One clock for the whole cockpit.
 *
 * Panels used to own private intervals, which let slower zones (research in
 * particular) sit on stale numbers while the rest of the page had already
 * moved on. Every polled panel now keys its fetch on this shared token, so a
 * tick — automatic or manual — refreshes the page as one coherent snapshot.
 */
export function CockpitRefreshProvider({
  children,
  intervalMs = COCKPIT_REFRESH_INTERVAL_MS,
}: {
  children: ReactNode;
  intervalMs?: number;
}) {
  const [token, setToken] = useState(0);
  const [lastTickIso, setLastTickIso] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);

  const tick = useCallback(() => {
    setToken((current) => current + 1);
    setLastTickIso(new Date().toISOString());
  }, []);

  useEffect(() => {
    if (paused) {
      return;
    }
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      window.clearInterval(timer);
    };
  }, [intervalMs, paused, tick]);

  const value = useMemo<CockpitRefreshContextValue>(
    () => ({
      token,
      lastTickIso,
      paused,
      intervalMs,
      refreshNow: tick,
      setPaused,
    }),
    [intervalMs, lastTickIso, paused, tick, token],
  );

  return <CockpitRefreshContext.Provider value={value}>{children}</CockpitRefreshContext.Provider>;
}

export function useCockpitRefresh(): CockpitRefreshContextValue {
  const value = useContext(CockpitRefreshContext);
  if (value === null) {
    throw new Error("useCockpitRefresh must be used inside CockpitRefreshProvider.");
  }
  return value;
}
