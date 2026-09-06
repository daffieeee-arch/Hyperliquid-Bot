"use client";

import { formatGroupedNumber } from "../lib/display";
import type { VenueCaptureQuery } from "../lib/paths";
import { buildRiskView, RISK_UNAVAILABLE, type RiskField } from "../lib/risk";
import type { PaperRunSnapshot, VenueCaptureStripResponse } from "../lib/types";
import { useVenueCapturePoll } from "../lib/use-venue-capture";

function displayValue(field: RiskField): string {
  if (field.kind === "unavailable" || field.value === RISK_UNAVAILABLE) {
    return field.value;
  }
  if (
    field.id === "assumed-pnl" ||
    field.id === "starting-cash" ||
    field.id === "ending-equity" ||
    field.id === "funding"
  ) {
    const [amount, unit] = field.value.split(" ");
    if (amount === undefined) {
      return field.value;
    }
    return unit === undefined
      ? formatGroupedNumber(amount)
      : `${formatGroupedNumber(amount)} ${unit}`;
  }
  return field.value;
}

function RiskTable({ caption, rows }: { caption: string; rows: RiskField[] }) {
  return (
    <div className="risk-scroll">
      <table className="risk-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th>Field</th>
            <th>Value</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className={`tone-${row.tone}`}>
              <td>{row.label}</td>
              <td className={row.kind === "unavailable" ? "tone-warn" : undefined}>
                {displayValue(row)}
              </td>
              <td>{row.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RiskPanel({
  snapshot,
  snapshotError,
  query,
  initial,
}: {
  snapshot: PaperRunSnapshot | undefined;
  snapshotError: string | undefined;
  query: VenueCaptureQuery;
  initial: VenueCaptureStripResponse;
}) {
  const strip = useVenueCapturePoll(query, initial);
  const view = buildRiskView(snapshot, snapshotError, strip);
  const pnlTone = view.assumedPnlTone === "unknown" ? "warn" : view.assumedPnlTone;

  return (
    <section className="risk-panel" aria-label="RISK">
      <div className="risk-head">
        <h2>RISK</h2>
        <p className="panel-kicker">
          Reconstructable PAPER overlay + fail-closed bounds · missing fields stay UNAVAILABLE
        </p>
      </div>
      <div className="risk-glance" aria-label="RISK copied summary">
        <div>
          <span>Position</span>
          <strong>{view.positionBtc}</strong>
        </div>
        <div>
          <span>Assumed overlay PnL</span>
          <strong className={`tone-${pnlTone}`}>{view.assumedNetPnl}</strong>
        </div>
        <div>
          <span>Max assumed loss</span>
          <strong className={view.maxAssumedLoss === RISK_UNAVAILABLE ? "tone-warn" : undefined}>
            {view.maxAssumedLoss}
          </strong>
        </div>
        <div>
          <span>Capture</span>
          <strong className={view.captureLine === RISK_UNAVAILABLE ? "tone-warn" : undefined}>
            {view.captureLine}
          </strong>
        </div>
      </div>
      {view.error !== undefined ? <p className="error">{view.error}</p> : null}
      <div className="risk-board">
        <RiskTable caption="Copied PAPER / fail-closed" rows={view.copied} />
        <RiskTable caption="Not in PAPER artifacts" rows={view.unavailable} />
      </div>
    </section>
  );
}
