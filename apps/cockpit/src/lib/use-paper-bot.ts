"use client";

import type { PaperBotView } from "./paper-bot";
import type { PollState } from "./poll-state";
import { usePoll } from "./use-poll";

export const PAPER_RUN_REQUEST_URL = "/api/paper-run";

export function parsePaperRunResponse(payload: unknown): PaperBotView {
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("ok" in payload) ||
    payload.ok !== true ||
    !("view" in payload) ||
    typeof payload.view !== "object" ||
    payload.view === null
  ) {
    const message =
      typeof payload === "object" &&
      payload !== null &&
      "error" in payload &&
      typeof payload.error === "string"
        ? payload.error
        : "PAPER run response was not a fail-closed object.";
    throw new Error(message);
  }
  return payload.view as PaperBotView;
}

export async function fetchPaperBotView(): Promise<PaperBotView> {
  const response = await fetch(PAPER_RUN_REQUEST_URL, { cache: "no-store" });
  const payload: unknown = await response.json();
  return parsePaperRunResponse(payload);
}

/** PAPER desk view re-read on the shared cockpit clock. */
export function usePaperBot(initial: PaperBotView, refreshToken = 0): PollState<PaperBotView> {
  return usePoll(PAPER_RUN_REQUEST_URL, fetchPaperBotView, initial, refreshToken);
}
