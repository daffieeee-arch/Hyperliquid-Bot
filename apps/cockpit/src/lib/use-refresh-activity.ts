"use client";

import { createContext, useContext } from "react";

import type { FetchSettlement } from "./refresh-activity";

/** Marks a request as settled; safe to call once, later calls are ignored. */
export type EndFetch = (settlement: FetchSettlement) => void;

export type RefreshActivityTracker = {
  /** Register one in-flight request keyed on `token`; returns its settle callback. */
  beginFetch: (token: number) => EndFetch;
};

/**
 * Optional so hooks keep working outside `CockpitRefreshProvider` (tests,
 * isolated stories): without a provider nothing is tracked.
 */
export const RefreshActivityContext = createContext<RefreshActivityTracker | null>(null);

export function useRefreshActivityTracker(): RefreshActivityTracker | null {
  return useContext(RefreshActivityContext);
}
