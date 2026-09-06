import { captureBindingSourceLabel, presentCopiedText } from "../lib/display";
import type { CaptureRunProvenance } from "../lib/types";

export function RunProvenance({ provenance }: { provenance: CaptureRunProvenance }) {
  return (
    <section className="run-provenance" aria-label="Capture run provenance">
      <div className="run-provenance-head">
        <h3>Run provenance</h3>
        <p className="panel-kicker">
          Bound reconstructable run_id per venue · later starts shorten comparable overlap · not PnL
        </p>
      </div>
      {provenance.rows.length === 0 ? (
        <p className="run-provenance-empty">{provenance.overlap_note}</p>
      ) : (
        <table className="run-provenance-table">
          <thead>
            <tr>
              <th>Venue</th>
              <th>Series</th>
              <th>Run id</th>
              <th>Bind</th>
              <th>Started (from run_id)</th>
            </tr>
          </thead>
          <tbody>
            {provenance.rows.map((row) => (
              <tr key={row.id}>
                <td>{row.chip}</td>
                <td>{row.series}</td>
                <td>{row.run_id}</td>
                <td>{captureBindingSourceLabel(row.binding_source)}</td>
                <td>{presentCopiedText(row.started_at_utc)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="run-provenance-note">{provenance.overlap_note}</p>
      {provenance.shared_run_ids.length > 0 ? (
        <p className="run-provenance-note">
          Shared run_id {provenance.shared_run_ids.join(", ")}. Distinct ids{" "}
          {provenance.distinct_run_ids.join(", ") || "n/a"}.
        </p>
      ) : provenance.distinct_run_ids.length > 1 ? (
        <p className="run-provenance-note">
          Venues are bound to different run_ids ({provenance.distinct_run_ids.join(", ")}). Do not
          treat them as one aligned series.
        </p>
      ) : null}
    </section>
  );
}
