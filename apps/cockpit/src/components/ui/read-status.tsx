"use client";

import { Badge } from "./badge";
import { describePollRead, readAgeLabel, type PollState } from "../../lib/poll-state";
import { localClockLabel } from "../../lib/time-display";
import { useNow } from "../../lib/use-now";

/**
 * Read-vs-source freshness for one polled panel.
 *
 * Shows when the backend was last read successfully (Europe/Amsterdam, CET/CEST)
 * and, separately, the
 * timestamp the source data itself carries. The relative age ticks every
 * second but is always measured from the last *successful* read: a ticking
 * refresh clock never appears here on its own.
 */
export function ReadStatus({
  state,
  sourceLabel = "source",
  sourceIso,
  compact = false,
}: {
  state: PollState<unknown>;
  /** What the source timestamp represents, e.g. "newest part", "last event". */
  sourceLabel?: string;
  sourceIso?: string;
  compact?: boolean;
}) {
  const read = describePollRead(state);
  const nowIso = useNow();
  const age = readAgeLabel(state, nowIso);
  return (
    <span className="row" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
      <Badge tone={read.tone} title={read.detail}>
        {read.label}
      </Badge>
      {age === undefined ? null : (
        <span className="eyebrow read-age" aria-live="off">
          {age}
        </span>
      )}
      {compact || sourceIso === undefined ? null : (
        <span className="eyebrow" title={`${sourceLabel}: ${sourceIso}`}>
          {sourceLabel} {localClockLabel(sourceIso)}
        </span>
      )}
    </span>
  );
}
