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
import { StoredDataStrip } from "./stored-data-strip";
import { PublicCloseSparkline } from "../kpi-sparkline";
import { VenueStrip } from "../venue-strip";
import { Badge } from "../ui/badge";
import { Card, CardBody, CardDisclosure, CardHeader } from "../ui/card";
import { KvList } from "../ui/kv";
import { Notice } from "../ui/notice";
import { OriginBadge } from "../ui/origin-badge";
import { ReadStatus } from "../ui/read-status";
import { Stat } from "../ui/stat";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import {
  dataOriginMeta,
  describeOriginSummary,
  paperOrigin,
  stripOrigin,
} from "../../lib/data-origin";
import { dataStateTone } from "../../lib/data-state";
import { deskPhaseAProgressLine } from "../../lib/desk";
import { formatGroupedNumber } from "../../lib/display";
import { newestTapeEvent } from "../../lib/market-tape-rows";
import type { MarketTapeResponse } from "../../lib/market-tape-types";
import { buildOverviewView } from "../../lib/overview";
import type { PaperBotView } from "../../lib/paper-bot";
import type { VenueCaptureQuery } from "../../lib/paths";
import type { ResearchP0View } from "../../lib/research-p0-view";
import type { VenueCaptureStripResponse } from "../../lib/types";
import { useMarketTape } from "../../lib/use-market-tape";
import { usePaperBot } from "../../lib/use-paper-bot";
import { useResearchP0 } from "../../lib/use-research-p0";
import { usePublicBtcPerp } from "../../lib/use-public-price";
import { useVenueCapturePoll } from "../../lib/use-venue-capture";
import { newestPartMtime } from "../../lib/venue-capture-poll";
import { searchFromQuery } from "../../lib/query-search";

export function OverviewScreen({
  query,
  initialStrip,
  initialResearch,
  initialTape,
  paper,
}: {
  query: VenueCaptureQuery;
  initialStrip: VenueCaptureStripResponse;
  initialResearch: ResearchP0View;
  initialTape: MarketTapeResponse;
  paper: PaperBotView;
}) {
  const { token } = useCockpitRefresh();
  const stripPoll = useVenueCapturePoll(query, initialStrip, token);
  const researchPoll = useResearchP0(query, initialResearch, token);
  const paperPoll = usePaperBot(paper, token);
  const tapePoll = useMarketTape(query, initialTape, token);
  const strip = stripPoll.data;
  const research = researchPoll.data;
  const paperView = paperPoll.data;
  const tape = tapePoll.data;
  const mid = usePublicBtcPerp(token);
  const search = searchFromQuery(query);

  const view = useMemo(
    () =>
      buildOverviewView(
        strip,
        research,
        paperView,
        {
          strip: stripPoll,
          research: researchPoll,
          paper: paperPoll,
          tape: tapePoll,
        },
        tape,
      ),
    [paperPoll, paperView, research, researchPoll, strip, stripPoll, tape, tapePoll],
  );
  const phaseALine = useMemo(() => deskPhaseAProgressLine(strip), [strip]);
  const anyReadFailed =
    stripPoll.error !== undefined ||
    researchPoll.error !== undefined ||
    paperPoll.error !== undefined ||
    tapePoll.error !== undefined;
  const origin = stripOrigin(strip);

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
        <div className="page-head-actions">
          <OriginBadge
            origin={origin.origin}
            detail={describeOriginSummary(origin)}
            live={!stripPoll.degraded}
          />
          <ReadStatus
            state={stripPoll}
            sourceLabel="newest part"
            sourceIso={newestPartMtime(strip)}
          />
        </div>
      </div>

      {origin.origin === "demo" || origin.origin === "mixed" ? (
        <Notice
          state="pending"
          title={
            origin.origin === "demo"
              ? "This screen shows the repository demo fixture, not a capture"
              : "Bound venues come from different origins"
          }
        >
          {dataOriginMeta(origin.origin).meaning} {describeOriginSummary(origin)}. Point{" "}
          <span className="mono">ARTIFACT_ROOT</span> at the retain root (and leave the venue run
          ids unset to auto-detect the live runs) to see real captures.
        </Notice>
      ) : null}

      {anyReadFailed ? (
        <Notice state="error" title="A refresh failed — values below are from an earlier read">
          The refresh clock kept ticking but at least one backend read did not succeed. Each card
          shows when it was last read successfully; the attention list names the failing request.
        </Notice>
      ) : null}

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
          meta={view.capture.error === undefined ? phaseALine : view.capture.error}
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
                <>
                  <OriginBadge
                    origin={paperOrigin(view.paper.lifecycle)}
                    live={!paperPoll.degraded}
                  />
                  <Badge
                    tone={
                      view.paper.lifecycle.state === "historical"
                        ? "info"
                        : view.paper.lifecycle.state === "in-flight"
                          ? "paper"
                          : "muted"
                    }
                    title={view.paper.lifecycle.note}
                  >
                    {view.paper.lifecycle.label}
                  </Badge>
                  <ReadStatus state={paperPoll} compact />
                  <Link href={`/paper${search}`} className="badge badge-outline">
                    Open <ArrowUpRight size={11} aria-hidden="true" />
                  </Link>
                </>
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
                    detail:
                      view.paper.lastOutcome === "REJECT"
                        ? `#65 ${view.paper.lastGate}`
                        : view.paper.lastGate === "UNAVAILABLE"
                          ? undefined
                          : view.paper.lastGate,
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
            <>
              <ReadStatus state={stripPoll} compact />
              <Link href={`/system${search}`} className="badge badge-outline">
                System <ArrowUpRight size={11} aria-hidden="true" />
              </Link>
            </>
          }
        />
        <CardBody>
          <VenueStrip strip={strip} dense degraded={stripPoll.degraded} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Stored market data"
          description="Last trade and best bid/offer decoded from each run's published Parquet parts. Spot, perpetual and quote currency stay separate."
          actions={
            <>
              <ReadStatus
                state={tapePoll}
                sourceLabel="last event"
                sourceIso={newestTapeEvent(tape)}
                compact
              />
              <Link href={`/markets${search}`} className="badge badge-outline">
                Markets <ArrowUpRight size={11} aria-hidden="true" />
              </Link>
            </>
          }
        />
        <CardBody>
          <StoredDataStrip tape={tape} strip={strip} degraded={tapePoll.degraded} />
        </CardBody>
      </Card>

      <div className="grid grid-lg-2">
        <Card>
          <CardHeader
            title="Research availability"
            description="What is actually on disk for the runs you have bound."
            actions={
              <>
                <ReadStatus state={researchPoll} compact />
                <Link href={`/research${search}`} className="badge badge-outline">
                  Open <ArrowUpRight size={11} aria-hidden="true" />
                </Link>
              </>
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
                  detail:
                    "Capture last and BBO mid for every bound venue. Public candles stay context.",
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
