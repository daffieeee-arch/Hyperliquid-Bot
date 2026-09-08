"use client";

import { useEffect, useRef, useState } from "react";

import { initialPollState, pollFailed, pollSucceeded, type PollState } from "./poll-state";

/**
 * Poll one resource on the shared cockpit clock.
 *
 * `key` identifies the request (query string etc.); when it changes the hook
 * re-fetches immediately. `refreshToken` comes from `CockpitRefreshProvider`.
 * A failed attempt keeps the previous data and records the error instead of
 * blanking the panel, so old values stay visible but recognisably degraded.
 */
export function usePoll<T>(
  key: string,
  fetcher: () => Promise<T>,
  initial: T,
  refreshToken = 0,
  now: () => string = () => new Date().toISOString(),
): PollState<T> {
  const [state, setState] = useState<PollState<T>>(() => initialPollState(initial));
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const nowRef = useRef(now);
  nowRef.current = now;

  useEffect(() => {
    let cancelled = false;

    async function refresh(): Promise<void> {
      try {
        const data = await fetcherRef.current();
        if (!cancelled) {
          setState((previous) => pollSucceeded(previous, data, nowRef.current()));
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setState((previous) => pollFailed(previous, error, nowRef.current()));
        }
      }
    }

    void refresh();
    return () => {
      cancelled = true;
    };
  }, [key, refreshToken]);

  return state;
}
