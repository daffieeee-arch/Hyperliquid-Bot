"use client";

import { Badge } from "./badge";
import { dataOriginMeta, type DataOrigin } from "../../lib/data-origin";

/**
 * Says whether a panel shows a live capture, a historical run or the demo
 * fixture. Rendered next to freshness, never instead of it.
 */
export function OriginBadge({
  origin,
  detail,
  live = false,
}: {
  origin: DataOrigin;
  /** Extra context appended to the hover text, e.g. per-venue counts. */
  detail?: string;
  live?: boolean;
}) {
  const meta = dataOriginMeta(origin);
  return (
    <Badge
      tone={meta.tone}
      dot={origin === "live"}
      live={origin === "live" && live}
      title={detail === undefined ? meta.meaning : `${meta.meaning} ${detail}`}
    >
      {meta.label}
    </Badge>
  );
}
