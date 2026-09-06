import type { CaptureRunProvenance } from "../lib/types";

export function RunProvenance({ provenance }: { provenance: CaptureRunProvenance }) {
  const shared = provenance.shared_run_ids.join(", ");
  const distinct = provenance.distinct_run_ids.join(", ");

  return (
    <section className="run-provenance" aria-label="Capture run provenance">
      {provenance.rows.length === 0 ? (
        <p className="run-provenance-empty">{provenance.overlap_note}</p>
      ) : (
        <p className="run-provenance-note">
          {provenance.overlap_note}
          {shared !== ""
            ? ` Shared ${shared}. Distinct ${distinct || "n/a"}.`
            : provenance.distinct_run_ids.length > 1
              ? ` Distinct run_ids ${distinct}. Do not treat as one aligned series.`
              : ""}
        </p>
      )}
    </section>
  );
}
