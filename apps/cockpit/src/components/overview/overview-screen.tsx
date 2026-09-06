"use client";

import {
  Activity,
  ArrowUpRight,
  Bot,
  CandlestickChart,
  FlaskConical,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { AttentionList } from "./attention-list";
import { PublicCloseSparkline } from "../kpi-sparkline";
import { VenueStrip } from "../venue-strip";
import { Badge } from "../ui/badge";
import { Card, CardBody, CardDisclosure, CardHeader } from "../ui/card";
import { KvList } from "../ui/kv";
import { Notice } from "../ui/notice";
import { Stat } from "../ui/stat";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import { dataStateTone } from "../../lib/data-state";
import { formatGroupedNumber } from "../../lib/display";
import { buildOverviewView } from "../../lib/overview";
import type { PaperBotView } from "../../lib/paper-bot";
import type { VenueCaptureQuery } from "../../lib/paths";
import type { ResearchP0View } from "../../lib/research-p0-view";
import type { VenueCaptureStripResponse } from "../../lib/types";
import { useResearchP0 } from "../../lib/use-research-p0";
import { usePublicBtcPerp } from "../../lib/use-public-price";
import { useVenueCapturePoll } from "../../lib/use-venue-capture";
import { searchFromQuery } from "../../lib/query-search";

export function OverviewScreen({
  query,
  initialStrip,
  initialResearch,
  paper,
}: {
  query: VenueCaptureQuery;
  initialStrip: VenueCaptureStripResponse;
  initialResearch: ResearchP0View;
  paper: PaperBotView;
}) {
  const { token } = useCockpitRefresh();
  const strip = useVenueCapturePoll(query, initialStrip, token);
  const { view: research } = useResearchP0(query, initialResearch, token);
  const mid = usePublicBtcPerp(token);
  const search = searchFromQuery(query);

  const view = useMemo(() => buildOverviewView(strip, research, paper), [paper, research, strip]);

  const attentionTone =
    view.attention.length === 0 ? "ok" : dataStateTone(view.attention[0]?.state ?? "ok");

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Operator overview</h1>
          <p>
            One read of the whole desk: which captures are running, what needs a human, and which
            research artifacts are actually attributable to the runs you have bound.
          </p>
        </div>
      </div>

      <div className="grid grid-sm-2 grid-lg-4">
        <Stat
          label="Capture"
          value={
            view.capture.error === undefined
              ? `${String(view.capture.live)}/${String(view.capture.bound)}`
              : "—"
          }
          tone={dataStateTone(view.capture.worst)}
          icon={<Activity size={14} aria-hidden="true" />}
          meta={
            view.capture.error === undefined
              ? `live · ${String(view.capture.stale)} stale · ${String(view.capture.missing)} missing · ${String(view.capture.degraded)} degraded · fresh ≤ ${String(view.capture.freshMaxS)}s`
              : view.capture.error
          }
        />
        <Stat
          label="Needs attention"
          value={String(view.attention.length)}
          tone={attentionTone}
          icon={<TriangleAlert size={14} aria-hidden="true" />}
          meta={
            view.attention.length === 0
              ? "No missing, stale or failing artifact right now."
              : `Most urgent: ${view.attention[0]?.title ?? ""}`
          }
        />
        <Stat
          label="Research verdict"
          value={view.research.verdict}
          compact
          tone={view.research.verdictTone === "muted" ? "muted" : view.research.verdictTone}
          icon={<FlaskConical size={14} aria-hidden="true" />}
          meta={view.research.bindingNote}
        />
        <Stat
          label="PAPER assumed PnL"
          value={view.paper.assumedPnl}
          compact
          tone="warn"
          icon={<Bot size={14} aria-hidden="true" />}
          meta={`${view.paper.kind} · not venue-reconciled · exact ${view.paper.assumedPnlExact}`}
        />
      </div>

      <div className="grid grid-lg-sidebar">
        <Card>
          <CardHeader
            title="Needs attention"
            description="Ranked by severity: errors first, then stale reads, then artifacts that were never written."
            actions={
              <Badge tone={attentionTone}>
                {view.attention.length === 0 ? "Clear" : `${String(view.attention.length)} open`}
              </Badge>
            }
          />
          <CardBody flush>
            <AttentionList items={view.attention} search={search} />
          </CardBody>
          <CardDisclosure summary="What these states mean">
            <p style={{ margin: "0 0 0.4rem" }}>
              <strong>Missing</strong> — never pointed at or never written. Values stay UNAVAILABLE
              instead of collapsing to zero.
            </p>
            <p style={{ margin: "0 0 0.4rem" }}>
              <strong>Stale</strong> — the artifact exists, but its newest write is older than the
              freshness bound (<span className="mono">{String(view.capture.freshMaxS)}s</span>).
            </p>
            <p style={{ margin: 0 }}>
              <strong>Error</strong> — the artifact is present but unreadable or violates its path
              contract. That is a fault, not a gap.
            </p>
          </CardDisclosure>
        </Card>

        <div className="stack">
          <Card>
            <CardHeader
              title="Market context"
              description="Public Hyperliquid mid. Context only — never research truth and never PAPER PnL."
              actions={
                <Link href={`/markets${search}`} className="badge badge-outline">
                  Open <ArrowUpRight size={11} aria-hidden="true" />
                </Link>
              }
            />
            <CardBody>
              {mid.status === "ready" ? (
                <>
                  <div className="stat-value">{formatGroupedNumber(mid.price.mid)}</div>
                  <div className="stat-meta">
                    BTC-PERP mid · {mid.price.source} · credentialless
                  </div>
                  <PublicCloseSparkline refreshToken={token} />
                </>
              ) : mid.status === "loading" ? (
                <div className="skeleton" style={{ height: "3.5rem" }} aria-hidden />
              ) : (
                <Notice state="error" title="Public mid unavailable">
                  {mid.message}
                </Notice>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="PAPER desk"
              description="COURSE-1 soak only. DATA retain stays a separate identity."
              actions={
                <Link href={`/paper${search}`} className="badge badge-outline">
                  Open <ArrowUpRight size={11} aria-hidden="true" />
                </Link>
              }
            />
            <CardBody>
              <KvList
                rows={[
                  { label: "Run", value: view.paper.runId, tone: "paper" },
                  {
                    label: "Last decision",
                    value: view.paper.lastOutcome,
                    tone:
                      view.paper.lastOutcome === "ACCEPT"
                        ? "ok"
                        : view.paper.lastOutcome === "REJECT"
                          ? "down"
                          : "warn",
                    detail: view.paper.lastGate === "UNAVAILABLE" ? undefined : view.paper.lastGate,
                  },
                  { label: "Position", value: `${view.paper.side} · ${view.paper.size}` },
                  {
                    label: "Intents / rejects",
                    value: `${String(view.paper.intents)} / ${String(view.paper.rejects)}`,
                  },
                ]}
              />
            </CardBody>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader
          title="Bound capture runs"
          description="Read-only. The cockpit never starts or stops a collector."
          actions={
            <Link href={`/system${search}`} className="badge badge-outline">
              System <ArrowUpRight size={11} aria-hidden="true" />
            </Link>
          }
        />
        <CardBody>
          <VenueStrip strip={strip} dense />
        </CardBody>
      </Card>

      <div className="grid grid-lg-2">
        <Card>
          <CardHeader
            title="Research availability"
            description="What is actually on disk for the runs you have bound."
            actions={
              <Link href={`/research${search}`} className="badge badge-outline">
                Open <ArrowUpRight size={11} aria-hidden="true" />
              </Link>
            }
          />
          <CardBody>
            <KvList
              rows={[
                {
                  label: "Runs in registry",
                  value: `${String(view.research.runsWithArtifacts)} / ${String(view.research.registryRows)} bound`,
                },
                {
                  label: "Sufficiency verdict",
                  value: view.research.verdict,
                  tone:
                    view.research.verdictTone === "muted" ? "unknown" : view.research.verdictTone,
                },
                { label: "Attribution", value: view.research.runBinding, tone: "unknown" },
                { label: "Summary source", value: view.research.summarySource },
              ]}
            />
            {view.research.runBinding === "matched" ? null : (
              <Notice
                state={view.research.available ? "stale" : "missing"}
                title={view.research.available ? "Not attributable" : "No summary pointed"}
              >
                {view.research.bindingNote}
              </Notice>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Where to go next" description="Each workspace answers one question." />
          <CardBody flush>
            <div className="attention">
              {[
                {
                  href: `/markets${search}`,
                  icon: <CandlestickChart size={15} aria-hidden="true" />,
                  title: "Markets",
                  detail: "Public mid and candles with interval choice and preserved zoom.",
                },
                {
                  href: `/research${search}`,
                  icon: <FlaskConical size={15} aria-hidden="true" />,
                  title: "Research",
                  detail: "Run registry, capture health and WP-Q1 summaries tied to a run.",
                },
                {
                  href: `/paper${search}`,
                  icon: <Bot size={15} aria-hidden="true" />,
                  title: "PAPER",
                  detail: "What ran, why it was accepted or rejected, and the intent tape.",
                },
              ].map((item) => (
                <Link key={item.href} href={item.href} className="attention-row">
                  <span className="tone-paper" style={{ display: "inline-flex" }}>
                    {item.icon}
                  </span>
                  <span className="attention-copy">
                    <strong>{item.title}</strong>
                    <span>{item.detail}</span>
                  </span>
                  <span className="attention-side">
                    <ArrowUpRight size={14} aria-hidden="true" />
                  </span>
                </Link>
              ))}
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
