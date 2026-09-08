"use client";

import { Badge } from "./badge";
import { clockLabel, describePollRead, type PollState } from "../../lib/poll-state";

/**
 * Read-vs-source freshness for one polled panel.
 *
 * Shows when the backend was last read successfully (browser clock) and,
 * separately, the timestamp the source data itself carries. A ticking refresh
 * clock never appears here: only a completed read counts.
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
  return (
    <span className="row" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
      <Badge tone={read.tone} title={read.detail}>
        {read.label}
      </Badge>
      {compact || sourceIso === undefined ? null : (
        <span className="eyebrow" title={`${sourceLabel}: ${sourceIso}`}>
          {sourceLabel} {clockLabel(sourceIso)}
        </span>
      )}
    </span>
  );
}
