"use client";

import { Ban } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "../ui/badge";
import { Card, CardBody, CardHeader } from "../ui/card";
import { DataTable, dataTableColumnHelper, type DataTableColumns } from "../ui/data-table";
import { KvList } from "../ui/kv";
import { Notice } from "../ui/notice";
import { Stat } from "../ui/stat";
import {
  configsLabel,
  hypothesisHeadline,
  hypothesisLabelTone,
  parseReportMarkdown,
  passesH1Tone,
  presentPromotion,
  promotionSummary,
  RESULT_UNAVAILABLE,
  shortWindowLabel,
  type HypothesisResultItem,
  type HypothesisResultList,
  type ReportBlock,
  type ReportInline,
} from "../../lib/hypothesis-result-model";
import { updatedAgoLabel } from "../../lib/poll-state";
import { localDateTimeLabel } from "../../lib/time-display";

const columnHelper = dataTableColumnHelper<HypothesisResultItem>();

function Unavailable({ value }: { value: string }) {
  return value === RESULT_UNAVAILABLE ? <span className="tone-muted">{value}</span> : value;
}

function WindowCell({ value }: { value: string }) {
  const short = shortWindowLabel(value);
  return (
    <span title={value}>
      <Unavailable value={short} />
    </span>
  );
}

/** Gate copy uses the PAPER accent, not the clock treatment used for stale age. */
function GateNotice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="notice notice-gate" role="status">
      <Ban className="notice-icon" size={14} aria-hidden="true" />
      <div className="notice-body">
        <strong>{title}</strong>
        <span>{children}</span>
      </div>
    </div>
  );
}

function InlineRun({ inlines }: { inlines: ReportInline[] }) {
  return (
    <>
      {inlines.map((inline, index) => {
        const key = `${inline.kind}-${String(index)}`;
        switch (inline.kind) {
          case "code":
            return <code key={key}>{inline.text}</code>;
          case "strong":
            return <strong key={key}>{inline.text}</strong>;
          case "text":
            return <span key={key}>{inline.text}</span>;
          default: {
            const unexpected: never = inline;
            throw new Error(`Unhandled report inline: ${JSON.stringify(unexpected)}`);
          }
        }
      })}
    </>
  );
}

function ReportBlockView({ block, index }: { block: ReportBlock; index: number }) {
  const key = `${block.kind}-${String(index)}`;
  switch (block.kind) {
    case "heading":
      return block.level === 2 ? <h3 key={key}>{block.text}</h3> : <h4 key={key}>{block.text}</h4>;
    case "paragraph":
      return (
        <p key={key}>
          <InlineRun inlines={block.inlines} />
        </p>
      );
    case "list": {
      const items = block.items.map((item, itemIndex) => (
        <li key={`${key}-${String(itemIndex)}`}>
          <InlineRun inlines={item} />
        </li>
      ));
      return block.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>;
    }
    case "code":
      return (
        <pre key={key}>
          <code>{block.text}</code>
        </pre>
      );
    default: {
      const unexpected: never = block;
      throw new Error(`Unhandled report block: ${JSON.stringify(unexpected)}`);
    }
  }
}

function ReportMarkdown({ source }: { source: string }) {
  let blocks: ReportBlock[];
  try {
    blocks = parseReportMarkdown(source);
  } catch {
    return (
      <Notice state="error" title="Report could not be rendered">
        The markdown stays unavailable rather than breaking this page.
      </Notice>
    );
  }
  if (blocks.length === 0) {
    return (
      <Notice state="missing" title="Report markdown is empty">
        The report stays UNAVAILABLE.
      </Notice>
    );
  }
  return (
    <div className="report">
      {blocks.map((block, index) => (
        <ReportBlockView key={`${block.kind}-${String(index)}`} block={block} index={index} />
      ))}
    </div>
  );
}

function UpdatedLine({ item, nowIso }: { item: HypothesisResultItem; nowIso: string | undefined }) {
  if (item.updatedAt === RESULT_UNAVAILABLE) {
    return <p className="eyebrow">Updated {RESULT_UNAVAILABLE}</p>;
  }
  const ago = updatedAgoLabel(item.updatedAt, nowIso, "");
  return (
    <p className="eyebrow">
      Updated {localDateTimeLabel(item.updatedAt)}
      {ago === "" ? null : ` · ${ago}`}
    </p>
  );
}

function ResultDetail({
  item,
  nowIso,
}: {
  item: HypothesisResultItem;
  nowIso: string | undefined;
}) {
  const promotion = presentPromotion(item.passesH1, item.promotionDecision);
  return (
    <Card>
      <CardHeader
        title={hypothesisHeadline(item)}
        description={<span className="mono">{item.sourcePath}</span>}
        actions={
          <>
            {item.synthetic ? <Badge tone="muted">synthetic</Badge> : null}
            {item.status === "unreadable" ? <Badge tone="down">unreadable</Badge> : null}
            <Badge tone={promotion.tone} data-promotion-gate={promotion.gate}>
              {promotion.badge}
            </Badge>
          </>
        }
      />
      <CardBody>
        <UpdatedLine item={item} nowIso={nowIso} />
        {item.status === "unreadable" ? (
          <Notice state="error" title="This result file was not used">
            {item.problem}
          </Notice>
        ) : null}
        {promotion.conflict === undefined ? null : (
          <GateNotice title="Promotion stays forbidden">{promotion.conflict}</GateNotice>
        )}
        {promotion.gate === "recorded" ? (
          <GateNotice title="H1 passed; this view still does not promote">
            Recorded promotion_decision is {item.promotionDecision}. The cockpit does not promote a
            hypothesis or place an order.
          </GateNotice>
        ) : null}
        <KvList
          rows={[
            { label: "Work package", value: item.workPackage },
            { label: "Hypothesis", value: item.hypothesisId },
            { label: "run_id", value: <Unavailable value={item.runId} /> },
            { label: "product", value: <Unavailable value={item.product} /> },
            { label: "path_contract", value: <Unavailable value={item.pathContract} /> },
            { label: "Title", value: item.title },
            { label: "Label", value: item.label, tone: hypothesisLabelTone(item.label) },
            { label: "passes_h1", value: item.passesH1, tone: passesH1Tone(item.passesH1) },
            {
              label: "Configs passed / tested",
              value: configsLabel(item.configsPassed, item.configsTested),
            },
            { label: "Best net bps/trade", value: <Unavailable value={item.netBps} /> },
            { label: "Best gross bps/trade", value: <Unavailable value={item.grossBps} /> },
            { label: "OOS window", value: item.oosWindow, title: item.oosWindow },
            { label: "Holdout window", value: item.holdoutWindow, title: item.holdoutWindow },
            { label: "Data range", value: item.dataRange, title: item.dataRange },
            {
              label: "promotion_decision",
              value: item.promotionDecision,
              tone: promotion.tone,
            },
            { label: "trading_mode", value: item.tradingMode },
            { label: "schema_version", value: item.schemaVersion },
            { label: "Notes", value: item.notes },
            ...(item.problems === ""
              ? []
              : [{ label: "Field gaps", value: item.problems, tone: "warn" as const }]),
          ]}
        />
        <section id="hypothesis-detail" aria-label="Hypothesis report">
          <h3 className="card-title">Report</h3>
          {item.reportMarkdown === undefined ? (
            <Notice state="missing" title="Report unavailable">
              {item.reportNote}
            </Notice>
          ) : (
            <ReportMarkdown source={item.reportMarkdown} />
          )}
          <p className="eyebrow">{item.reportNote}</p>
        </section>
      </CardBody>
    </Card>
  );
}

export function HypothesisResultsView({
  list,
  nowIso,
  selectedId,
  onSelect,
}: {
  list: HypothesisResultList;
  nowIso: string | undefined;
  selectedId: string | undefined;
  onSelect: (id: string) => void;
}) {
  const selected = list.items.find((item) => item.id === selectedId) ?? list.items[0];
  const passed = list.items.filter((item) => item.passesH1 === "yes").length;
  const unreadable = list.items.filter((item) => item.status === "unreadable").length;
  const summary = promotionSummary(list.items);
  const columns = columnHelper.columns([
    columnHelper.accessor("workPackage", {
      header: "Work package",
      cell: ({ row }) => {
        const item = row.original;
        const pressed = selected?.id === item.id;
        return (
          <button
            type="button"
            className="row-select"
            aria-pressed={pressed}
            aria-controls="hypothesis-detail"
            title={item.status === "unreadable" ? item.problem : undefined}
            onClick={() => {
              onSelect(item.id);
            }}
          >
            {hypothesisHeadline(item)}
            {item.status === "unreadable" ? (
              <>
                {" "}
                <Badge tone="down">unreadable</Badge>
              </>
            ) : null}
            <span className="sr-only"> Show report. {item.problem}</span>
          </button>
        );
      },
    }),
    columnHelper.accessor("runId", {
      header: "run_id",
      cell: ({ row }) => <Unavailable value={row.original.runId} />,
    }),
    columnHelper.accessor("product", {
      header: "product",
      cell: ({ row }) => <Unavailable value={row.original.product} />,
    }),
    columnHelper.accessor("pathContract", {
      header: "path_contract",
      cell: ({ row }) => <Unavailable value={row.original.pathContract} />,
    }),
    columnHelper.accessor("label", {
      header: "Label",
      cell: ({ row }) => (
        <Badge tone={hypothesisLabelTone(row.original.label)}>{row.original.label}</Badge>
      ),
    }),
    columnHelper.accessor("passesH1", {
      header: "passes_h1",
      cell: ({ row }) => (
        <Badge tone={passesH1Tone(row.original.passesH1)}>{row.original.passesH1}</Badge>
      ),
    }),
    columnHelper.display({
      id: "configs",
      header: "Passed / tested",
      cell: ({ row }) => (
        <Unavailable value={configsLabel(row.original.configsPassed, row.original.configsTested)} />
      ),
    }),
    columnHelper.accessor("netBps", {
      header: "Net bps/trade",
      cell: ({ row }) => <Unavailable value={row.original.netBps} />,
    }),
    columnHelper.accessor("grossBps", {
      header: "Gross bps/trade",
      cell: ({ row }) => <Unavailable value={row.original.grossBps} />,
    }),
    columnHelper.accessor("oosWindow", {
      header: "OOS",
      cell: ({ row }) => <WindowCell value={row.original.oosWindow} />,
    }),
    columnHelper.accessor("holdoutWindow", {
      header: "Holdout",
      cell: ({ row }) => <WindowCell value={row.original.holdoutWindow} />,
    }),
    columnHelper.accessor("dataRange", {
      header: "Data range",
      cell: ({ row }) => <WindowCell value={row.original.dataRange} />,
    }),
    columnHelper.accessor("promotionDecision", {
      header: "promotion_decision",
      cell: ({ row }) => {
        const promotion = presentPromotion(row.original.passesH1, row.original.promotionDecision);
        return (
          <Badge tone={promotion.tone} title={promotion.conflict ?? row.original.promotionDecision}>
            {promotion.badge}
          </Badge>
        );
      },
    }),
  ]) as DataTableColumns<HypothesisResultItem>;

  let body: ReactNode;
  if (list.items.length === 0) {
    body = (
      <Notice state="missing" title="No hypothesis results">
        {list.note}
        {list.root === undefined ? null : <span className="mono"> {list.root}</span>}
      </Notice>
    );
  } else {
    body = (
      <>
        <div className="grid grid-sm-2 grid-lg-4">
          <Stat label="Results" value={String(list.items.length)} compact meta={list.note} />
          <Stat
            label="H1 passed"
            value={String(passed)}
            compact
            tone={passed === 0 ? "muted" : "info"}
            meta="Counted only when passes_h1 is true."
          />
          <Stat
            label="Unreadable"
            value={String(unreadable)}
            compact
            tone={unreadable === 0 ? "muted" : "down"}
            meta="Malformed files stay in the table with UNAVAILABLE metrics."
          />
          <Stat label="Promotion" value={summary.value} compact tone="warn" meta={summary.meta} />
        </div>
        <Card>
          <CardHeader
            title="Work packages"
            description="Sort any column. Choose a row to read its report. No order can be placed from this table."
          />
          <CardBody flush>
            <DataTable
              columns={columns}
              data={list.items}
              caption="Hypothesis and work-package results. Read only."
              numericColumns={["configs", "netBps", "grossBps"]}
              monoColumns={["runId", "product", "pathContract"]}
              nowrap
              stickyFirst
              emptyLabel="No hypothesis results. Metrics stay UNAVAILABLE."
              cellClassName={(_columnId, row) =>
                row.id === selected?.id ? "row-selected" : undefined
              }
            />
          </CardBody>
        </Card>
        {selected === undefined ? null : <ResultDetail item={selected} nowIso={nowIso} />}
      </>
    );
  }

  return (
    <div className="stack">
      <GateNotice title="Promotion is forbidden unless H1 passes">
        This view is read-only. It does not promote a hypothesis, place an order, or change trading
        mode. A recorded promotion_decision other than forbidden is ignored when H1 did not pass.
      </GateNotice>
      {body}
    </div>
  );
}
