"use client";

import { useState } from "react";

import { Notice } from "../ui/notice";
import { ReadStatus } from "../ui/read-status";
import { useCockpitRefresh } from "../providers/cockpit-refresh";
import type { HypothesisResultList } from "../../lib/hypothesis-result-model";
import { useNow } from "../../lib/use-now";
import { useHypothesisResults } from "../../lib/use-hypothesis-results";
import { HypothesisResultsView } from "./hypothesis-results-view";
import { ResearchSectionNav } from "./research-section-nav";

export function HypothesisResultsScreen({ initial }: { initial: HypothesisResultList }) {
  const { token } = useCockpitRefresh();
  const poll = useHypothesisResults(initial, token);
  const nowIso = useNow();
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);
  const list = poll.data;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Hypothesis results</h1>
          <p>
            Work-package artifacts only. Each row is what was tested, whether H1 passed, and the
            recorded gross and net bps per trade. Missing fields stay UNAVAILABLE. Nothing here
            places an order.
          </p>
        </div>
        <div className="page-head-actions">
          <ResearchSectionNav current="results" />
          <ReadStatus state={poll} sourceLabel="results read" sourceIso={list.observedAt} />
        </div>
      </div>
      {poll.error === undefined ? null : (
        <Notice
          state="error"
          title={
            poll.degraded
              ? "Hypothesis results refresh failed — values below are from an earlier read"
              : "Hypothesis results could not be read"
          }
        >
          {poll.error}
        </Notice>
      )}
      <HypothesisResultsView
        list={list}
        nowIso={nowIso}
        selectedId={selectedId}
        onSelect={setSelectedId}
      />
    </div>
  );
}
