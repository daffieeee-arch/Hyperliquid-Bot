"use client";

import { usePathname, useRouter } from "next/navigation";

import type { VenueCaptureQuery } from "../lib/paths";
import type { VenueCaptureCatalog, VenueCaptureChip } from "../lib/types";
import { venueCapturePickerHref } from "../lib/venue-capture-poll";

function selectedRunId(
  catalog: VenueCaptureCatalog,
  query: VenueCaptureQuery,
  bound: VenueCaptureChip | undefined,
): string {
  return query[catalog.query_key] ?? bound?.run_id ?? "";
}

export function RunPicker({
  query,
  catalog,
  venues,
}: {
  query: VenueCaptureQuery;
  catalog: VenueCaptureCatalog[];
  venues: VenueCaptureChip[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const hasCandidates = catalog.some((entry) => entry.candidates.length > 0);
  if (!hasCandidates) {
    return null;
  }

  return (
    <form className="run-picker" aria-label="Capture run picker">
      {catalog.map((entry) => {
        const bound = venues.find((venue) => venue.id === entry.id);
        const selected = selectedRunId(entry, query, bound);
        return (
          <label key={entry.id} className="run-picker-field">
            <span>
              {entry.chip} · {entry.series}
            </span>
            <select
              value={
                entry.candidates.some((candidate) => candidate.run_id === selected) ? selected : ""
              }
              onChange={(event) => {
                router.replace(
                  venueCapturePickerHref(pathname, query, entry.query_key, event.target.value),
                );
              }}
            >
              <option value="">
                {entry.candidates.some((candidate) => candidate.live)
                  ? "Auto (freshest live retain)"
                  : "Auto (no live retain)"}
              </option>
              {entry.candidates.map((candidate) => (
                <option key={candidate.run_id} value={candidate.run_id}>
                  {candidate.run_id}
                  {candidate.live ? " · live" : candidate.has_health ? " · stopped" : ""}
                </option>
              ))}
            </select>
          </label>
        );
      })}
    </form>
  );
}
