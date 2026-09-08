"use client";

import { usePathname, useRouter } from "next/navigation";

import { Notice } from "./ui/notice";
import { captureRunOptionLabel } from "../lib/display";
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

/**
 * Explicit run binding per venue.
 *
 * The selection lives in the URL, so every workspace reads the same runs and a
 * chosen binding survives navigation and reload.
 */
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
    return (
      <Notice state="missing" title="No selectable runs">
        No venue exposes a discoverable run directory, so there is nothing to pick. Point{" "}
        <span className="mono">ARTIFACT_ROOT</span> at a retained capture tree to enable explicit
        run binding.
      </Notice>
    );
  }

  return (
    <form className="grid grid-sm-2 grid-lg-4" aria-label="Capture run picker">
      {catalog.map((entry) => {
        const bound = venues.find((venue) => venue.id === entry.id);
        const selected = selectedRunId(entry, query, bound);
        const known = entry.candidates.some((candidate) => candidate.run_id === selected);
        return (
          <label key={entry.id} className="field">
            <span>
              {entry.chip} · {entry.series}
            </span>
            <select
              value={known ? selected : ""}
              disabled={entry.candidates.length === 0}
              onChange={(event) => {
                router.replace(
                  venueCapturePickerHref(pathname, query, entry.query_key, event.target.value),
                );
              }}
            >
              <option value="">
                {entry.candidates.length === 0
                  ? "No discoverable run"
                  : entry.candidates.some((candidate) => candidate.live)
                    ? "Auto (freshest live retain)"
                    : "Auto (no live retain)"}
              </option>
              {entry.candidates.map((candidate) => (
                <option key={candidate.run_id} value={candidate.run_id}>
                  {captureRunOptionLabel(entry.id, candidate)}
                </option>
              ))}
            </select>
          </label>
        );
      })}
    </form>
  );
}
