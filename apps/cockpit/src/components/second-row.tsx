import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { KvTable } from "./kv-table";
import type { SecondRowView } from "../lib/second-row";

export function SecondRowPanels({ view }: { view: SecondRowView }) {
  return (
    <div className="zone-grid zone-grid-3" aria-label="Bind, capture, Binance">
      <Card aria-label="D01 paper_risk bind health">
        <CardHeader>
          <CardTitle>D01 / paper_risk bind</CardTitle>
          <CardDescription>{view.bind.note}</CardDescription>
        </CardHeader>
        <CardContent>
          <KvTable
            rows={[
              { label: "Bind", value: view.bind.bound },
              { label: "Strategy", value: view.bind.strategyClass },
              { label: "Same D01 smoke risk", value: view.bind.sameD01SmokeRisk },
              { label: "paper_risk catalog", value: view.bind.paperRiskCatalog },
              { label: "Source", value: view.bind.source },
            ]}
          />
        </CardContent>
      </Card>
      <Card aria-label="Read-only capture health">
        <CardHeader>
          <CardTitle>Capture health</CardTitle>
          <CardDescription>{view.capture.note}</CardDescription>
        </CardHeader>
        <CardContent>
          <KvTable
            rows={[
              { label: "Glance", value: view.capture.glanceLine },
              { label: "Fresh max", value: view.capture.freshMaxS },
              { label: "Start / stop", value: view.capture.startStop, tone: "warn" },
              { label: "Source", value: view.capture.source },
            ]}
          />
        </CardContent>
      </Card>
      <Card aria-label="Binance usdm_public callout">
        <CardHeader>
          <CardTitle>BN usdm_public</CardTitle>
          <CardDescription>{view.binance.note}</CardDescription>
        </CardHeader>
        <CardContent>
          <KvTable
            rows={[
              { label: "Profile", value: view.binance.profile },
              { label: "Channel", value: view.binance.channel },
              { label: "Status", value: view.binance.status },
              { label: "Age", value: view.binance.lastPartAge },
              { label: "G/R", value: view.binance.gapsReconnects },
              { label: "Bind", value: view.binance.binding },
              { label: "Run", value: view.binance.runId },
            ]}
          />
        </CardContent>
      </Card>
    </div>
  );
}
