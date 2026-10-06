"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { VENUE_CAPTURE_QUERY_KEYS } from "../../lib/venue-capture-poll";

export function ResearchSectionNav({ current }: { current: "capture" | "results" }) {
  const searchParams = useSearchParams();
  const params = new URLSearchParams();
  for (const key of VENUE_CAPTURE_QUERY_KEYS) {
    const value = searchParams.get(key);
    if (value !== null && value.trim() !== "") {
      params.set(key, value.trim());
    }
  }
  const encoded = params.toString();
  const suffix = encoded === "" ? "" : `?${encoded}`;
  return (
    <nav className="seg" aria-label="Research views">
      <Link href={`/research${suffix}`} aria-current={current === "capture" ? "page" : undefined}>
        Capture
      </Link>
      <Link
        href={`/research/results${suffix}`}
        aria-current={current === "results" ? "page" : undefined}
      >
        Hypothesis results
      </Link>
    </nav>
  );
}
