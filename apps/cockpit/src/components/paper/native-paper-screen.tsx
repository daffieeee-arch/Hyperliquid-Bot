"use client";

import { Badge } from "../ui/badge";
import { Card, CardBody, CardDisclosure, CardHeader } from "../ui/card";
import { KvList } from "../ui/kv";
import { Notice } from "../ui/notice";
import { ReadStatus } from "../ui/read-status";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { dataStateMeta } from "../../lib/data-state";
import type { NativePaperProjection } from "../../lib/native-paper-model";
import { useNativePaper } from "../../lib/use-native-paper";

const ORIGIN = { unbound: "UNBOUND", demo: "DEMO FIXTURE", historical: "HISTORICAL SNAPSHOT" };
const HISTORY_ROWS = 8;
const value = (text: string | null): string => text ?? "UNAVAILABLE";

export function NativePaperScreen({ projection }: { projection: NativePaperProjection }) {
  const { token } = useCockpitRefresh();
  const poll = useNativePaper(projection, token);
  const view = poll.data;
  const snapshot = view.snapshot;
  const read = dataStateMeta(view.state);
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Native PAPER desk</h1>
          <p>
            Hyperliquid BTC-PERP simulation. PnL is assumed, not venue-reconciled; funding is not
            settled.
          </p>
        </div>
        <div className="row">
          <Badge tone="paper">PAPER</Badge>
          <Badge tone={view.origin === "demo" ? "warn" : "muted"}>{ORIGIN[view.origin]}</Badge>
          <Badge tone={read.tone}>{read.label}</Badge>
          <ReadStatus
            state={poll}
            sourceLabel="engine observation"
            sourceIso={snapshot?.observedAtUtc}
          />
        </div>
      </div>
      {poll.degraded ? (
        <Notice state="error" title="Refresh failed">
          Previously read values remain below and are not current.
        </Notice>
      ) : null}
      <Notice state={view.state}>
        {view.message} Liveness is unverified; RUNNING is an engine status, not proof of an active
        feed.
      </Notice>
      {snapshot === null ? (
        <Card>
          <CardHeader title="Native PAPER values unavailable" />
          <CardBody>No position, PnL or history is substituted from another run.</CardBody>
        </Card>
      ) : (
        <>
          <div className="grid grid-sm-2">
            <Card>
              <CardHeader
                title="Run and safety"
                description="Read-only snapshot; no venue orders submitted."
              />
              <CardBody>
                <KvList
                  rows={[
                    { label: "Run", value: snapshot.runId },
                    {
                      label: "Strategy",
                      value: snapshot.strategyLabel,
                      detail: `${snapshot.strategyId} · ${snapshot.strategyVersion}`,
                    },
                    { label: "Engine status", value: snapshot.status },
                    { label: "Run closed", value: snapshot.runClosed ? "yes" : "no" },
                    {
                      label: "Kill switch",
                      value: snapshot.killSwitch,
                      detail: snapshot.killReason ?? "No reason recorded",
                    },
                    { label: "Open simulated order", value: snapshot.openOrder ?? "none" },
                    { label: "Snapshot read (UTC)", value: view.observed_at },
                    { label: "Last market event (UTC)", value: value(snapshot.lastEventAtUtc) },
                  ]}
                />
              </CardBody>
            </Card>
            <Card>
              <CardHeader
                title="Assumed economics"
                description="Exact source decimals. Cash is a simulation ledger balance, not free margin."
              />
              <CardBody>
                <KvList
                  rows={[
                    { label: "Position (BTC)", value: snapshot.positionBtc },
                    { label: "Average entry (USDC)", value: snapshot.averageEntry },
                    { label: "Mark (USDC)", value: value(snapshot.markPrice) },
                    { label: "Cash ledger (USDC)", value: snapshot.cashUsdc },
                    { label: "Equity (USDC)", value: value(snapshot.equityUsdc) },
                    {
                      label: "Realized gross PnL (USDC)",
                      value: snapshot.realizedGrossUsdc,
                      detail: "Fees are recorded separately below.",
                    },
                    { label: "Unrealized PnL (USDC)", value: value(snapshot.unrealizedUsdc) },
                    { label: "Fees (USDC)", value: snapshot.feesUsdc },
                  ]}
                />
              </CardBody>
            </Card>
          </div>
          <Card>
            <CardHeader
              title="Ledger history"
              description={`${String(snapshot.ledgerRecords)} validated ledger records · ${String(snapshot.orders.length)} completed/canceled orders · ${String(snapshot.fills.length)} fills · ${String(snapshot.rejections.length)} risk rejections`}
            />
            <CardBody>
              <p>
                Showing the last {String(HISTORY_ROWS)} rows per category from the complete
                validated ledger within the reader limits. Accepted open orders are separate from
                completed/canceled order counts.
              </p>
              <KvList
                rows={snapshot.orders.slice(-HISTORY_ROWS).map((order) => ({
                  label: `Order ${String(order.ordinal)}`,
                  value: `${order.side} ${order.quantity} BTC · ${order.status}`,
                  detail: `${order.id} · filled ${order.filledQuantity} · reduce-only ${order.reduceOnly ? "yes" : "no"} · ${order.reason}`,
                }))}
              />
              <KvList
                rows={snapshot.fills.slice(-HISTORY_ROWS).map((fill) => ({
                  label: `Fill ${String(fill.ordinal)}`,
                  value: `${fill.side} ${fill.quantity} BTC @ ${fill.price}`,
                  detail: `${fill.orderId} · fee ${fill.feeUsdc} USDC · ${fill.source}`,
                }))}
              />
              <KvList
                rows={snapshot.rejections.slice(-HISTORY_ROWS).map((rejection) => ({
                  label: `Rejection ${String(rejection.ordinal)}`,
                  value: rejection.reason,
                  detail: rejection.detail ?? "No detail recorded",
                }))}
              />
            </CardBody>
            <CardDisclosure summary="Source identity and snapshot limits">
              <KvList
                rows={[
                  { label: "Source commit", value: snapshot.sourceCommit },
                  { label: "Image digest", value: snapshot.imageDigest },
                  { label: "Projection", value: view.schema },
                ]}
              />
              <p>
                Matching artifacts and ledger checks do not prove atomic event completion, venue
                reconciliation or continuous operation. Unknown source identities remain unknown.
              </p>
            </CardDisclosure>
          </Card>
        </>
      )}
    </div>
  );
}
