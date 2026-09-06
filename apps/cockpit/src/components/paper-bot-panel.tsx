import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { KvTable } from "./kv-table";
import { formatGroupedNumber, yesNo } from "../lib/display";
import type { IntentFillRow } from "../lib/intent-fill";
import type { PaperBotView } from "../lib/paper-bot";

function outcomeTone(outcome: IntentFillRow["outcome"]): "ok" | "down" | "warn" {
  switch (outcome) {
    case "ACCEPT":
      return "ok";
    case "REJECT":
      return "down";
    case "UNAVAILABLE":
      return "warn";
    default: {
      const exhaustive: never = outcome;
      throw new Error(`Unhandled tape outcome: ${String(exhaustive)}`);
    }
  }
}

function TapeTable({ rows }: { rows: IntentFillRow[] }) {
  if (rows.length === 0) {
    return <p className="empty">No PAPER intents in this reconstructable COURSE-1 soak.</p>;
  }
  return (
    <div className="markets-scroll">
      <table className="blotter">
        <thead>
          <tr>
            <th>Outcome</th>
            <th>Gate</th>
            <th>Intent</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th>Why</th>
            <th>RO</th>
            <th className="num">Fill</th>
            <th className="num">Price</th>
            <th className="num">Pos after</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.clientOrderId}>
              <td className={`tone-${outcomeTone(row.outcome)}`}>{row.outcome}</td>
              <td className={row.gateCode === "UNAVAILABLE" ? "tone-warn" : "mono-id"}>
                {row.gateCode}
              </td>
              <td className="mono-id">{row.clientOrderId}</td>
              <td className={row.side === "BUY" ? "tone-up" : "tone-down"}>{row.side}</td>
              <td className="num">{formatGroupedNumber(row.quantity)}</td>
              <td>{row.intentReason}</td>
              <td>{yesNo(row.reduceOnly)}</td>
              <td className="num">{row.matched ? row.fillOrdinal : "UNAVAILABLE"}</td>
              <td className="num">
                {row.matched ? formatGroupedNumber(row.fillPrice) : "UNAVAILABLE"}
              </td>
              <td className="num">
                {row.matched ? formatGroupedNumber(row.positionAfter) : "UNAVAILABLE"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PaperBotPanel({ view }: { view: PaperBotView }) {
  return (
    <section className="paper-bot" aria-label="DESK COURSE-1 soak">
      <div className="zone-grid zone-grid-3">
        <Card aria-label="What">
          <CardHeader>
            <CardTitle>What</CardTitle>
            <CardDescription>PAPER · {view.what.kind} · not DATA retain</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                { label: "Mode", value: view.what.mode, tone: "paper" },
                { label: "Kind", value: view.what.kind, tone: "paper" },
                { label: "run_id", value: view.what.runId },
                { label: "Soak claim path", value: view.what.claimPath },
                { label: "path_contract", value: view.what.pathContract },
                { label: "Instrument", value: view.what.instrument },
                { label: "Strategy", value: view.what.strategyClass },
                { label: "Venue orders", value: view.what.venueOrdersSubmitted },
              ]}
            />
          </CardContent>
        </Card>
        <Card aria-label="Why last decision">
          <CardHeader>
            <CardTitle>Why last decision</CardTitle>
            <CardDescription>{view.why.source}</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                {
                  label: "Outcome",
                  value: view.why.outcome,
                  tone: outcomeTone(view.why.outcome),
                },
                { label: "Gate", value: view.why.gateName },
                { label: "Reason", value: view.why.reason },
                { label: "Intent", value: view.why.intentId },
              ]}
            />
          </CardContent>
        </Card>
        <Card aria-label="Results">
          <CardHeader>
            <CardTitle>Results</CardTitle>
            <CardDescription>{view.results.assumedPnlLabel}</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                { label: "Side", value: view.results.side },
                { label: "Size", value: view.results.size },
                { label: "Entry", value: formatGroupedNumber(view.results.entry) },
                { label: "Mark", value: formatGroupedNumber(view.results.mark) },
                { label: "assumed_pnl", value: view.results.assumedPnl, tone: "warn" },
                { label: "Label", value: view.results.venueReconciled, tone: "warn" },
                { label: "D22-B", value: view.results.d22b, tone: "warn" },
                { label: "Venue PnL", value: view.results.venuePnl },
              ]}
            />
          </CardContent>
        </Card>
      </div>
      <Card aria-label="Preflight caps strip">
        <CardHeader>
          <CardTitle>Preflight caps</CardTitle>
          <CardDescription>
            {view.preflight.documentedSource} · {view.preflight.source}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="preflight-strip">
            <div>
              <dt>Equity</dt>
              <dd>{view.preflight.equity}</dd>
            </div>
            <div>
              <dt>Risk / trade</dt>
              <dd>{view.preflight.riskPerTrade}</dd>
            </div>
            <div>
              <dt>Max lev</dt>
              <dd>{view.preflight.maxLeverage}</dd>
            </div>
            <div>
              <dt>Max gross</dt>
              <dd>{view.preflight.maxGross}</dd>
            </div>
            <div>
              <dt>Max net</dt>
              <dd>{view.preflight.maxNet}</dd>
            </div>
            <div>
              <dt>Max positions</dt>
              <dd>{view.preflight.maxPositions}</dd>
            </div>
            <div>
              <dt>Copied notional</dt>
              <dd>{view.preflight.maxEntryNotionalUsdc}</dd>
            </div>
            <div>
              <dt>Copied assumed loss</dt>
              <dd>{view.preflight.maxAssumedLossUsdc}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>
      <Card aria-label="Intent tape">
        <CardHeader>
          <CardTitle>Tape of intent</CardTitle>
          <CardDescription>
            Last {String(view.tapeLimit)} COURSE-1 paper intents/fills · rejected rows keep gate
            codes · codes stay UNAVAILABLE unless on orders.json
          </CardDescription>
        </CardHeader>
        <CardContent>
          <TapeTable rows={view.tape} />
        </CardContent>
      </Card>
    </section>
  );
}
