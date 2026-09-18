"use client";

import { useEffect, useRef, useState } from "react";

import { initialPollState, pollFailed, pollSucceeded, type PollState } from "./poll-state";
import { contentChanged } from "./refresh-activity";
import { useRefreshActivityTracker } from "./use-refresh-activity";

/**
 * Poll one resource on the shared cockpit clock.
 *
 * `key` identifies the request (query string etc.); when it changes the hook
 * re-fetches immediately. `refreshToken` comes from `CockpitRefreshProvider`.
 * A failed attempt keeps the previous data and records the error instead of
 * blanking the panel, so old values stay visible but recognisably degraded.
 *
 * Each attempt is registered with the provider's in-flight registry (when
 * present) and settles with whether the content actually changed, so the
 * topbar can report "updated" vs "already current" vs "failed" honestly.
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
  const stateRef = useRef(state);
  stateRef.current = state;
  const tracker = useRefreshActivityTracker();

  useEffect(() => {
    let cancelled = false;
    const end = tracker?.beginFetch(refreshToken);

    async function refresh(): Promise<void> {
      try {
        const data = await fetcherRef.current();
        if (cancelled) {
          return;
        }
        const changed = contentChanged(stateRef.current.data, data);
        setState((previous) => pollSucceeded(previous, data, nowRef.current()));
        end?.({ changed });
      } catch (error: unknown) {
        if (cancelled) {
          return;
        }
        setState((previous) => pollFailed(previous, error, nowRef.current()));
        end?.({ changed: false, error: error instanceof Error ? error.message : String(error) });
      }
    }

    void refresh();
    return () => {
      cancelled = true;
      // A superseded request (key change, unmount, StrictMode re-run) must not
      // leave the registry counting it as in flight.
      end?.({ changed: false });
    };
  }, [key, refreshToken, tracker]);

  return state;
}
