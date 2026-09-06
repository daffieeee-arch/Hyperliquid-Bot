import { PaperBlotter } from "./paper-blotter";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { KvTable } from "./kv-table";
import { formatGroupedNumber, uniqueStrings, yesNo } from "../lib/display";
import type { IntentFillRow } from "../lib/intent-fill";
import type { PaperBotView } from "../lib/paper-bot";
import type { PaperRunSnapshot } from "../lib/types";

function TapeTable({ rows }: { rows: IntentFillRow[] }) {
  if (rows.length === 0) {
    return <p className="empty">No PAPER intents in this reconstructable run.</p>;
  }
  return (
    <div className="markets-scroll">
      <table className="blotter">
        <thead>
          <tr>
            <th>Intent</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th>Why</th>
            <th>RO</th>
            <th>Risk codes</th>
            <th className="num">Fill</th>
            <th className="num">Price</th>
            <th className="num">Pos after</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.clientOrderId}>
              <td>{row.clientOrderId}</td>
              <td className={row.side === "BUY" ? "tone-up" : "tone-down"}>{row.side}</td>
              <td className="num">{formatGroupedNumber(row.quantity)}</td>
              <td>{row.intentReason}</td>
              <td>{yesNo(row.reduceOnly)}</td>
              <td className={row.riskReasons === "UNAVAILABLE" ? "tone-warn" : undefined}>
                {row.riskReasons}
              </td>
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

export function PaperBotPanel({
  view,
  snapshot,
}: {
  view: PaperBotView;
  snapshot: PaperRunSnapshot | undefined;
}) {
  return (
    <section className="paper-bot" aria-label="PAPER bot">
      <div className="zone-grid zone-grid-3">
        <Card>
          <CardHeader>
            <CardTitle>What</CardTitle>
            <CardDescription>Sandbox PAPER bot · no venue orders</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                { label: "Strategy", value: view.what.strategyClass },
                { label: "Instrument", value: view.what.instrument },
                { label: "Position", value: view.what.positionBtc },
                { label: "Position label", value: view.what.positionLabel, tone: "warn" },
                {
                  label: "Intents / fills",
                  value: `${view.what.intentCount} / ${view.what.fillCount}`,
                },
                {
                  label: "Venue orders submitted",
                  value: view.what.venueOrdersSubmitted,
                },
              ]}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Why</CardTitle>
            <CardDescription>
              Copied intent reasons + preflight bind · Quant why-this-trade UNAVAILABLE
            </CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                {
                  label: "Intent reasons",
                  value: view.why.intentReasons.join(" · ") || "UNAVAILABLE",
                },
                { label: "Preflight strategy", value: view.why.preflightStrategy },
                { label: "Same D01 smoke risk", value: view.why.sameD01SmokeRisk },
              ]}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Results</CardTitle>
            <CardDescription>{view.results.assumedPnlLabel}</CardDescription>
          </CardHeader>
          <CardContent>
            <KvTable
              rows={[
                { label: "Assumed overlay PnL", value: view.results.assumedNetPnl, tone: "warn" },
                { label: "Ending equity (assumed)", value: view.results.endingEquity },
                { label: "Soak mark", value: formatGroupedNumber(view.results.soakMark) },
                { label: "Venue PnL", value: view.results.venuePnl },
              ]}
            />
          </CardContent>
        </Card>
      </div>
      <Card aria-label="Preflight caps">
        <CardHeader>
          <CardTitle>Preflight caps</CardTitle>
          <CardDescription>{view.preflight.source}</CardDescription>
        </CardHeader>
        <CardContent>
          <KvTable
            rows={[
              { label: "Strategy class", value: view.preflight.strategyClass },
              { label: "Order qty", value: view.preflight.orderQuantityBtc },
              { label: "Max entry notional", value: view.preflight.maxEntryNotionalUsdc },
              { label: "Max assumed loss", value: view.preflight.maxAssumedLossUsdc },
              { label: "Same D01 smoke risk", value: view.preflight.sameD01SmokeRisk },
            ]}
          />
        </CardContent>
      </Card>
      <Card aria-label="Intents to fills">
        <CardHeader>
          <CardTitle>Intents → fills</CardTitle>
          <CardDescription>
            Copied COURSE-1 intents/fills · paper_risk reason codes stay UNAVAILABLE unless present
          </CardDescription>
        </CardHeader>
        <CardContent>
          <TapeTable rows={view.tape} />
        </CardContent>
      </Card>
      <Card aria-label="Risk rejection panel">
        <CardHeader>
          <CardTitle>paper_risk gates</CardTitle>
          <CardDescription>{view.riskRejections.source}</CardDescription>
        </CardHeader>
        <CardContent>
          <KvTable
            rows={[
              { label: "Copied rejections", value: view.riskRejections.copiedRejectionCount },
              { label: "Rejection source", value: view.riskRejections.copiedRejectionSource },
              { label: "Halt state", value: view.riskRejections.haltState, tone: "warn" },
              { label: "Halt source", value: view.riskRejections.haltStateSource },
              { label: "Reduce-only after halt", value: view.riskRejections.reduceOnlyAfterHalt },
            ]}
          />
          <div className="markets-scroll">
            <table className="risk-table">
              <caption>Documented #65 gates · not a live portfolio snapshot</caption>
              <thead>
                <tr>
                  <th>Gate</th>
                  <th>Default</th>
                  <th>On breach</th>
                  <th>Kind</th>
                </tr>
              </thead>
              <tbody>
                {view.riskRejections.catalog.map((gate) => (
                  <tr key={gate.id}>
                    <td>{gate.id}</td>
                    <td>{gate.defaultCap}</td>
                    <td>{gate.onBreach}</td>
                    <td>{gate.kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
      {snapshot !== undefined ? (
        <PaperBlotter orders={snapshot.orders} fills={snapshot.fills} />
      ) : null}
      {snapshot !== undefined ? (
        <Card>
          <CardHeader>
            <CardTitle>Limitations</CardTitle>
            <CardDescription>Copied from reconstructable JSON · not invented</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="limitations">
              {uniqueStrings([
                ...snapshot.position.limitations,
                ...snapshot.pnl.limitations,
                ...snapshot.health.limitations,
                ...snapshot.orders.limitations,
                ...snapshot.fills.limitations,
                ...view.why.limitations,
              ]).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}
