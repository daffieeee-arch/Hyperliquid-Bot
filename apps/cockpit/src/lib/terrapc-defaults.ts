/**
 * Current TerraPC WSL2 72h retain ids. Operator docs / picker hint only.
 * Auto-detect still prefers a live retain (claim + fresh parts, no health JSON)
 * and never binds a stopped directory as RUNNING.
 */
export const TERRAPC_SHARED_RETAIN_RUN_ID = "20260905t232635z-live-retained";
export const TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID = "20260906t101559z-live-retained";
/** Post-hotfix Binance retain. STOPPED after DATA-1F #58; do not prefer. */
export const TERRAPC_BINANCE_STOPPED_RETAIN_RUN_ID = "20260905t235830z-live-retained";

export const TERRAPC_ACTIVE_RETAIN_RUN_IDS = {
  hl: TERRAPC_SHARED_RETAIN_RUN_ID,
  binance: TERRAPC_BINANCE_ACTIVE_RETAIN_RUN_ID,
  bitvavo: TERRAPC_SHARED_RETAIN_RUN_ID,
  kraken: TERRAPC_SHARED_RETAIN_RUN_ID,
} as const;

export type TerrapcVenueId = keyof typeof TERRAPC_ACTIVE_RETAIN_RUN_IDS;

export function isTerrapcActiveRetain(venueId: TerrapcVenueId, runId: string): boolean {
  return TERRAPC_ACTIVE_RETAIN_RUN_IDS[venueId] === runId;
}
