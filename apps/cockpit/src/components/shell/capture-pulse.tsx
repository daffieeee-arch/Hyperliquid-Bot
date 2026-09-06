"use client";

import { RefreshCw } from "lucide-react";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { captureChipDataState, dataStateTone, worstDataState } from "../../lib/data-state";
import type { VenueCaptureQuery } from "../../lib/paths";
import { useVenueCapturePoll } from "../../lib/use-venue-capture";

const PENDING = "__pending__";

function clockLabel(iso: string | null): string {
  if (iso === null) {
    return "—";
  }
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) {
    return "—";
  }
  return new Date(parsed).toISOString().slice(11, 19) + "Z";
}

/**
 * Topbar freshness pulse: bound run count, worst capture state and last tick.
 */
export function CapturePulse({ query }: { query: VenueCaptureQuery }) {
  const { token, lastTickIso, refreshNow, paused, setPaused, intervalMs } = useCockpitRefresh();
  const strip = useVenueCapturePoll(query, { ok: false, error: PENDING }, token);
  const pending = !strip.ok && strip.error === PENDING;

  const summary = strip.ok
    ? (() => {
        const worst = worstDataState(
          strip.strip.venues.map((venue) => captureChipDataState(venue.status)),
        );
        const live = strip.strip.venues.filter((venue) => venue.live).length;
        return {
          tone: dataStateTone(worst),
          label:
            live > 0
              ? `${String(live)}/${String(strip.strip.venues.length)} live`
              : `${String(strip.strip.venues.length)} bound`,
          live: live > 0,
          title: `Worst capture state: ${worst}. Fresh ≤ ${String(strip.strip.fresh_max_s)}s.`,
        };
      })()
    : null;

  return (
    <>
      {pending ? (
        <span className="skeleton" style={{ width: "4.5rem", height: "1.25rem" }} aria-hidden />
      ) : summary === null ? (
        <Badge tone="down" title={strip.ok ? undefined : strip.error}>
          Capture error
        </Badge>
      ) : (
        <Badge tone={summary.tone} dot live={summary.live} title={summary.title}>
          {summary.label}
        </Badge>
      )}
      <span className="eyebrow" title={`Auto-refresh every ${String(intervalMs / 1000)}s`}>
        {clockLabel(lastTickIso)}
      </span>
      <Button
        type="button"
        variant="outline"
        size="icon"
        title={paused ? "Auto-refresh paused — resume" : "Refresh now"}
        aria-label={paused ? "Resume auto refresh" : "Refresh now"}
        onClick={() => {
          if (paused) {
            setPaused(false);
          }
          refreshNow();
        }}
      >
        <RefreshCw aria-hidden="true" />
      </Button>
    </>
  );
}
