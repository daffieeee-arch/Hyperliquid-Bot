"use client";

import { ArrowUpRight, CheckCircle2 } from "lucide-react";
import Link from "next/link";

import { Badge } from "../ui/badge";
import { dataStateMeta, type DataState } from "../../lib/data-state";
import type { AttentionItem } from "../../lib/overview";

const LABEL: Record<DataState, string> = {
  error: "Error",
  stale: "Stale",
  missing: "Missing",
  pending: "Pending",
  ok: "OK",
};

export function AttentionList({ items, search }: { items: AttentionItem[]; search: string }) {
  if (items.length === 0) {
    return (
      <div className="empty">
        <CheckCircle2 size={18} aria-hidden="true" style={{ marginBottom: "0.35rem" }} />
        <p style={{ margin: 0 }}>
          Nothing is asking for attention. Every bound capture is inside its freshness window and
          the PAPER run reads cleanly.
        </p>
      </div>
    );
  }

  return (
    <div className="attention">
      {items.map((item) => {
        const meta = dataStateMeta(item.state);
        return (
          <Link key={item.id} href={`${item.href}${search}`} className="attention-row">
            <Badge tone={meta.tone} title={meta.meaning}>
              {LABEL[item.state]}
            </Badge>
            <span className="attention-copy">
              <strong>{item.title}</strong>
              <span>{item.detail}</span>
            </span>
            <span className="attention-side">
              <span className="eyebrow">{item.target}</span>
              <ArrowUpRight size={14} aria-hidden="true" />
            </span>
          </Link>
        );
      })}
    </div>
  );
}
