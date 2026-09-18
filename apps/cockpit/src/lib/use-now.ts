"use client";

import { useEffect, useState } from "react";

/**
 * Client wall clock as an ISO string, ticking every `intervalMs`.
 *
 * Undefined during server rendering and until the first client tick, so a
 * relative age is never rendered from a clock the browser has not confirmed
 * (and server/client markup stays identical). This is a display clock only:
 * it must never be used as evidence that data was fetched — that is what
 * `PollState.lastSuccessAt` is for.
 */
export function useNow(intervalMs = 1000): string | undefined {
  const [nowIso, setNowIso] = useState<string | undefined>(undefined);

  useEffect(() => {
    const tick = () => {
      setNowIso(new Date().toISOString());
    };
    tick();
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      window.clearInterval(timer);
    };
  }, [intervalMs]);

  return nowIso;
}
